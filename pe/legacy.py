"""Best-effort parser for archived chat-output cards (pre tail-block).

These outputs are prose + tables with inconsistent layout, so this extracts
only what is explicit:
  * runners mentioned with a bucket (A+, A, B, C, D, F-, F) and modifiers,
  * retail-trap / unseen tags,
  * horizontal tickets written as consecutive `R<n>: #x, #y` lines.

Anything ambiguous is recorded as a data-quality flag rather than guessed.
Races are never marked grades_complete, because archives only name the
notable runners.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .db import flag, insert_ticket
from .tickets import Ticket

TRACK_HINTS = {"bel": "BEL", "chrch": "CD", "cd": "CD", "lrl": "LRL", "gp": "GP",
               "px": "PRX", "prx": "PRX", "kee": "KEE", "sa": "SA", "dmr": "DMR",
               "aqu": "AQU", "sar": "SAR", "tam": "TAM"}

RACE_RE = re.compile(r"(?:\bRace\s+(\d{1,2})\b|\bRACE\s+(\d{1,2})\b|(?:^|[\s*(])R(\d{1,2})\b(?=[\s:–—\-(]|$))")
HORSE_RE = re.compile(r"#(\d{1,2}[A-Z]?)\s+([A-Za-z][A-Za-z'’.\- ]{1,40}?)\s*\(([^()]*)\)([^#\n]{0,60})")
GRADE_RE = re.compile(r"(?<![A-Za-z])\[?(A\+|F-|A|B|C|D|F)\]?((?:\[[WE]\])*)(?![a-z])")
ODDS_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)(?:\s*/\s*(\d{1,2}))?")
LEG_RE = re.compile(r"^\s*[*\-•]?\s*R(\d{1,2})\b[^:]{0,40}:\s*(.+)$")
COMBO_RE = re.compile(r"(\d[\d,]*)\s*(?:Combinations|combinations|combos|Combos)")
BASE_RE = re.compile(r"\$(\d+(?:\.\d{2})?)\s*(?:Base|base)")
POOL_WORDS = [("Pick 6", "P6"), ("Pick 5", "P5"), ("Big 5", "P5"), ("Pick 4", "P4"),
              ("Pick 3", "P3"), ("Daily Double", "DD"), ("Double", "DD")]


def guess_track(filename: str) -> str:
    stem = Path(filename).stem.lower()
    m = re.match(r"[a-z]+", stem)
    key = m.group(0) if m else stem
    return TRACK_HINTS.get(key, key.upper())


def parse_odds(text: str) -> float | None:
    m = ODDS_RE.match(text.strip())   # odds must lead the parenthetical: "(4.5 ML | A)" or "(9/2 A[W])"
    if not m:
        return None
    a = float(m.group(1))
    if m.group(2):
        b = float(m.group(2))
        return round(a / b, 2) if b else None
    return a if a <= 99 else None


@dataclass
class Mention:
    race: int
    pgm: str
    name: str
    bucket: str | None
    mods: set
    tags: set
    odds: float | None


@dataclass
class LegacyCard:
    track: str
    source_file: str
    mentions: list[Mention] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)
    notes: list[tuple[int | None, str, str]] = field(default_factory=list)  # (race, severity, msg)


def _grade_from(blob: str):
    blob_clean = re.sub(r"\[UNSEEN[^\]]*\]|\[RETAIL[^\]]*\]|\[Chalk[^\]]*\]", " ", blob)
    for m in GRADE_RE.finditer(blob_clean):
        g, mods = m.group(1), m.group(2)
        # Skip things like "D to T" surface notes or "F" in "FTS"
        tail = blob_clean[m.end():m.end() + 3]
        if g in {"D", "F"} and re.match(r"\s*to\b", tail):
            continue
        return g, set(re.findall(r"\[([WE])\]", mods))
    return None, set()


def _tags(blob: str) -> set:
    t = set()
    if re.search(r"UNSEEN", blob):
        t.add("UNSEEN")
    if re.search(r"RETAIL TRAP|Retail Trap|Chalk Underlay", blob):
        t.add("RETAIL_TRAP")
    return t


RANGE_RE = re.compile(r"\bR(?:aces?\s*)?\d{1,2}\s*[–—\-]\s*R?\d{1,2}\b|\bRaces\s+\d{1,2}\s*[–—\-]|\bR\d{1,2}\s*(?:&|,)\s*R\d{1,2}\b")
BARE_RE = re.compile(r"#(\d{1,2}[A-Z]?)\s+([A-Za-z][A-Za-z'’.\- ]{1,40}?)(?=\s*#\d|\s*$)")
GRADE_ONLY_RE = re.compile(r"^\s*(?:\[?(?:A\+|F-|A|B|C|D|F)\]?(?:\[[WE]\])*(?:\s*\[UNSEEN BY RETAIL\])?\s*)+$")
ODDS_LINE_RE = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2})?\s*\(~")


def _race_of(m) -> int:
    return int(next(g for g in m.groups() if g))


def parse_text(text: str, track: str, source_file: str = "") -> LegacyCard:
    card = LegacyCard(track=track, source_file=source_file)
    lines = text.splitlines()
    race: int | None = None
    pending: list[Mention] = []      # ungraded mentions from a table row awaiting a grade line
    for line in lines:
        m = RACE_RE.search(line)
        if m:
            race = _race_of(m)
        if race is None:
            continue
        stripped = line.strip()
        # --- table continuation lines (grades / odds / trap designations on their own line)
        if pending and GRADE_ONLY_RE.match(stripped):
            toks = re.findall(r"(A\+|F-|A|B|C|D|F)((?:\[[WE]\])*)(\s*\[UNSEEN BY RETAIL\])?", stripped)
            if len(toks) == len(pending):
                for mt, (g, mods, uns) in zip(pending, toks):
                    mt.bucket, mt.mods = g, set(re.findall(r"\[([WE])\]", mods))
                    if uns:
                        mt.tags.add("UNSEEN")
            pending = []
            continue
        if pending and ODDS_LINE_RE.match(stripped):
            odds = re.findall(r"(\d{1,2}(?:\.\d{1,2})?)\s*\(~", stripped)
            for mt, o in zip(pending, odds):
                mt.odds = float(o)
            continue
        if pending and re.match(r"\[(F-\]|RETAIL TRAP)", stripped):
            for mt in pending:
                mt.tags.add("RETAIL_TRAP")
                mt.bucket = "F-" if stripped.startswith("[F-]") else (mt.bucket or "F")
            pending = []
            continue
        # A line spanning several races (e.g. "R1–R2 Double: #1, #3 WITH #2") is ambiguous.
        if RANGE_RE.search(line):
            continue
        found = list(HORSE_RE.finditer(line))
        if not found:
            bare = BARE_RE.findall(stripped) if stripped.startswith("#") else []
            if bare:
                pending = [Mention(race, p.upper(), n.strip(), None, set(), set(), None) for p, n in bare]
                card.mentions.extend(pending)
            continue
        pending = []
        for hm in found:
            pgm, name, inner, after = hm.groups()
            name = name.strip(" -")
            r = race
            rin = re.fullmatch(r"\s*R(\d{1,2})\s*", inner)
            if rin:                      # "#6 Florida Patriot (R4)"
                r, inner = int(rin.group(1)), ""
            else:
                local = RACE_RE.findall(line[:hm.start()])
                if local:
                    r = int(next(g for g in local[-1] if g))
            bucket, mods = _grade_from(inner)
            if bucket is None:
                lead = re.match(r"\s*(\[?(?:A\+|F-|A|B|C|D|F)\]?(?:\[[WE]\])*)(?=[\s,;)]|$)", after)
                if lead:
                    bucket, mods = _grade_from(lead.group(1))
            mt = Mention(r, pgm.upper(), name, bucket, mods, _tags(inner + after[:30]), parse_odds(inner))
            card.mentions.append(mt)
            if bucket is None:
                pending.append(mt)
    card.tickets = _parse_horizontal_tickets(lines, card)
    return card


def delatex(s: str) -> str:
    s = re.sub(r"\\(?:text|mathbf|mathrm)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:text|mathbf|mathrm)\{([^{}]*)\}", r"\1", s)   # nested once more
    s = s.replace("\\times", "×").replace("\\$", "$").replace("\\%", "%").replace("\\to", "→")
    return re.sub(r"\s+", " ", s)


def _pool_for(header: str, nlegs: int) -> str:
    # Leg count is the reliable signal; headers like "Double [A+] Anchor Press" mislead.
    return {2: "DD", 3: "P3", 4: "P4", 5: "P5", 6: "P6"}.get(nlegs, f"H{nlegs}")


def _parse_horizontal_tickets(lines: list[str], card: LegacyCard) -> list[Ticket]:
    tickets: list[Ticket] = []
    section = ""
    i = 0
    while i < len(lines):
        if re.search(r"Pick [3-6]|Big 5|Double", lines[i]) and not LEG_RE.match(lines[i]):
            section = lines[i].strip()
        m = LEG_RE.match(lines[i])
        if not m:
            i += 1
            continue
        header = ""
        for back in range(1, 4):
            if i - back >= 0 and not LEG_RE.match(lines[i - back]) and lines[i - back].strip():
                header = lines[i - back].strip()
                break
        legs, races = [], []
        while i < len(lines) and (lm := LEG_RE.match(lines[i])):
            body = re.sub(r"\([^()]*\)", " ", lm.group(2))     # drop parentheticals
            body = re.split(r"\s—\s|\s-\sSINGLE", body)[0]
            nums = re.findall(r"#(\d{1,2}[A-Z]?)", body)
            if not nums:   # Parx style "R1: #6, #7" already covered; bare "6, 7"?
                nums = re.findall(r"\b(\d{1,2})\b", body)
            races.append(int(lm.group(1)))
            legs.append(nums)
            i += 1
        footer = lines[i].strip() if i < len(lines) else ""
        if len(legs) < 2:
            continue
        ctx = delatex(header + " " + footer)
        claimed = COMBO_RE.search(ctx)
        cost_m = re.search(r"combinations?\s*(?:×|x|\*)\s*\$\d+(?:\.\d+)?\s*=\s*\$([\d,]+(?:\.\d{2})?)", ctx) \
            or re.search(r"Combinations\s*=\s*\$([\d,]+(?:\.\d{2})?)", ctx)
        base = BASE_RE.search(header) or BASE_RE.search(section) or re.search(r"\$(\d+\.\d{2})", header)
        if not base:
            card.notes.append((races[0], "warn", f"ticket R{races[0]}-R{races[-1]} has no stated base; skipped"))
            continue
        t = Ticket(pool=_pool_for(section + " " + header, len(legs)), base=float(base.group(1)),
                   legs=legs, race=races[0], label=(header[:80] or section[:80]),
                   claimed_combos=int(claimed.group(1).replace(",", "")) if claimed else None,
                   claimed_cost=float(cost_m.group(1).replace(",", "")) if cost_m else None)
        if re.search(r"Trim|trim|Concentrate|split into|scaled to|Key-Linkage", ctx):
            t.issues.append("ticket matrix is revised in prose (trimmed/scaled); the bet actually placed is ambiguous")
        if races != list(range(races[0], races[0] + len(races))):
            t.issues.append(f"legs not consecutive races: {races}")
        tickets.append(t)
    return tickets


def consolidate(card: LegacyCard):
    """Collapse mentions into one grade per runner; flag disagreements."""
    by = defaultdict(list)
    for m in card.mentions:
        by[(m.race, m.pgm)].append(m)
    out = []
    for (race, pgm), ms in sorted(by.items()):
        grades = Counter(m.bucket for m in ms if m.bucket)
        mods = set().union(*(m.mods for m in ms))
        tags = set().union(*(m.tags for m in ms))
        names = Counter(m.name for m in ms)
        odds = next((m.odds for m in ms if m.odds is not None), None)
        if not grades:
            bucket, conf = ("F" if "RETAIL_TRAP" in tags else None), "inferred"
        elif len(grades) == 1:
            bucket, conf = next(iter(grades)), "explicit"
        else:
            # A+ beats A when both appear (anchors are often re-tagged as A in passing)
            if set(grades) == {"A+", "A"}:
                bucket, conf = "A+", "explicit"
            else:
                bucket, conf = grades.most_common(1)[0][0], "conflict"
                card.notes.append((race, "warn", f"#{pgm} graded {dict(grades)}; using {bucket}"))
        out.append(dict(race=race, pgm=pgm, name=names.most_common(1)[0][0], bucket=bucket,
                        mods=",".join(sorted(mods)), tags=",".join(sorted(tags)), odds=odds,
                        confidence=conf))
    return out


def ingest_legacy(con, path: str | Path, track: str | None = None,
                  race_date: str | None = None) -> tuple[str, dict]:
    path = Path(path)
    track = (track or guess_track(path.name)).upper()
    card = parse_text(path.read_text(errors="replace"), track, path.name)
    card_id = f"{track}-{race_date}" if race_date else f"{track}-{path.stem}"
    con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM data_quality_flags WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,source_file,notes) VALUES (?,?,?,?,?,?)",
                (card_id, track, race_date, "legacy_text", path.name,
                 "archive parse; grades incomplete by construction"))
    if not race_date:
        flag(con, card_id, None, "warn", "race_date unknown; results cannot be joined until set")
    rows = consolidate(card)
    for race in sorted({r["race"] for r in rows}):
        con.execute("INSERT INTO races(card_id,race,grades_complete) VALUES (?,?,0)", (card_id, race))
    for r in rows:
        con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,tags,"
                    "ml_odds,grade_confidence) VALUES (?,?,?,?,?,?,?,?,?)",
                    (card_id, r["race"], r["pgm"], r["name"], r["bucket"], r["mods"], r["tags"],
                     r["odds"], r["confidence"]))
    issues = 0
    for t in card.tickets:
        pre = list(t.issues)
        got = insert_ticket(con, card_id, t, "LEGACY")
        for p in pre:
            flag(con, card_id, t.race, "warn", f"ticket '{t.label}': {p}")
        issues += bool(got or pre)
    for race, sev, msg in card.notes:
        flag(con, card_id, race, sev, msg)
    con.commit()
    return card_id, {"runners": len(rows), "graded": sum(1 for r in rows if r["bucket"]),
                     "tickets": len(card.tickets), "tickets_with_issues": issues}
