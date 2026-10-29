"""Loader for Grant's per-race grade breakdown (Word .docx or the same text pasted into .txt).

Format, one block per race:

    RACE 4 — 1320 yds / D / Clm 11000b | Par Spd: 84 | Active Pace Scenario: Honest (Active QSP Sum: 22)
    (Scratch: #7 Boom Shocka Boom)
    A+ : #2 CHARGING AERO (3.00) — A+ — reason...
    A : #7 SUTTON BREEZE (4.00) — A[W][E] — reason... #1 NEXT HORSE (6.00) — A — reason...
    B / C (Meltdown Only) / D (Smell Test) : same layout, or None
    F- (Nuclear Bet-Against Lock): ...
    F (Chalk Fades & Tosses): #5 TACONY ROAD (3.50) (reason), #3 OTHER (4.00) (reason).
    Sniper Action (...): free text

Unlike the archived chat outputs, every active runner is graded, so races are stored
grades_complete=1 and are checked against the official field once results are loaded.
The race date is not in the file; it comes from --date or from the "Last Updated" stamp
in the file name, and is flagged when taken from the stamp.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .db import flag

TRACK_NAMES = {
    "finger lakes": "FL", "belmont": "BEL", "belmont park": "BEL", "churchill downs": "CD", "churchill": "CD",
    "laurel": "LRL", "laurel park": "LRL", "gulfstream": "GP", "gulfstream park": "GP", "parx": "PRX",
    "parx racing": "PRX", "aqueduct": "AQU", "saratoga": "SAR", "keeneland": "KEE", "santa anita": "SA",
    "del mar": "DMR", "tampa bay downs": "TAM", "tampa": "TAM", "mountaineer": "MNR", "mahoning valley": "MVR",
    "penn national": "PEN", "charles town": "CT", "turfway park": "TP", "turfway": "TP", "oaklawn": "OP",
    "oaklawn park": "OP", "woodbine": "WO", "fair grounds": "FG", "delta downs": "DED", "remington": "RP",
    "remington park": "RP", "zia park": "ZIA", "golden gate": "GG", "los alamitos": "LRC",
    "presque isle": "PID", "evangeline": "EVD", "lone star": "LS", "prairie meadows": "PRM",
    "kentucky downs": "KD", "ellis park": "ELP", "colonial downs": "CNL", "monmouth": "MTH",
}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}

HEADER_RE = re.compile(
    r"^RACE\s+(\d{1,2})\b(?:\s*\([^)]*\))?\s*[—–-]\s*(\d+)\s*(?:yards?|yds?|y)?\s*/\s*([A-Za-z]+)\s*/\s*(.+?)\s*\|\s*Par Spd:\s*"
    r"(\d+(?:\.\d+)?|[^|]*?)\s*\|\s*Active Pace Scenario:\s*([A-Za-z ]+?)\s*\(Active QSP Sum:\s*(\d+|[^)|]*?)\s*(?:\|\s*Implied QSP:\s*(\d+)\s*)?\)", re.I)
SCRATCH_LINE = re.compile(r"^\(.*scratch.*$", re.I)
SCRATCH_ONE = re.compile(r"#(\d{1,2}[A-Z]?)\s+(.+?)(?=\s+(?:and|is|are|was|has|have|were)\b|[;,.):]|\s*#|$)", re.I)
BUCKET_LINE = re.compile(r"^(A\+|A|B|C|D|F-|F)(?![A-Za-z0-9+\-])[^:]*:\s*(.*)$")
REMAINING_LINE = re.compile(r"^(?:Remaining|Primary Public|Outclassed)\b[^:]{0,140}:\s*(.*)$", re.I)
NAME_CH = r"[A-Za-z][A-Za-z0-9'’.&\- ]*?"
SCRATCH_A = re.compile(rf"#(\d{{1,2}}[A-Z]?)\s+({NAME_CH})\s+(?:is |was |has been |were )?scratched\b", re.I)
SCRATCH_B = re.compile(rf"\bscratch(?:ed|ing|es)?\b\s*(?:removed[^:]*)?:?\s*#(\d{{1,2}}[A-Z]?)\s+({NAME_CH})(?=\s+(?:and|is|are|was)\b|[;,.):]|\s*#|$)", re.I)
SNIPER_LINE = re.compile(r"^Sniper Action", re.I)
# "#4 Private Speaker (6.00)", "(4.5 ML)", "(ML 0.5)", "(9/1)", and the unclosed "(30.0 — reason"
ODDS = r"\(\s*(?:ML\s*)?(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?(?:\s*ML)?\s*(?:\)|(?=[—–]))"
ENTRY = re.compile(rf"#(\d{{1,2}}[A-Z]?)\s+(?:\[Post \d+\]\s*)?([A-Za-z][A-Za-z0-9'’.&\- ]*?)\s+{ODDS}")
HEAD = re.compile(r"^\s*(?:[—–-]\s*)?(?:Tag:\s*)?(?:None\b\s*)?(?P<g>A\+|A|B|C|D-INT|D-DOM|D|F-|F)?(?![A-Za-z0-9])"
                  r"(?P<br>(?:\s*\[[^\]]*\])*)\s*(?:[—–-]\s*)?", re.S)
SCENARIOS = {"honest": "HONEST", "meltdown": "MELTDOWN", "lone speed": "LONE_SPEED", "lone": "LONE_SPEED"}
RACE_TYPES = [(r"^md\s*sp", "MSW"), (r"^md", "MCL"), (r"^(o|n)?clm|^claim", "CLM"), (r"^alw|^allow", "ALW"),
              (r"^stk|^stake|^g\d|^grade", "STK"), (r"^ostr|^str", "STR")]


@dataclass
class Entry:
    race: int
    pgm: str
    name: str
    odds: float | None
    bucket: str
    mods: set
    reason: str
    tags: set = field(default_factory=set)


@dataclass
class RaceBlock:
    race: int
    dist: int | None = None
    surface: str | None = None
    race_type: str | None = None
    par_spd: float | None = None
    scenario: str | None = None
    qsp_sum: int | None = None
    scratches: list = field(default_factory=list)      # [(pgm, name)]
    entries: list = field(default_factory=list)
    sniper: str = ""


@dataclass
class Breakdown:
    races: list = field(default_factory=list)
    notes: list = field(default_factory=list)          # (race, severity, message)


def read_lines(path: Path) -> list[str]:
    if path.suffix.lower() == ".docx":
        import docx
        d = docx.Document(str(path))
        lines = [p.text for p in d.paragraphs]
        for t in d.tables:                              # tolerate a table-based layout
            for row in t.rows:
                lines.append(" ".join(c.text.strip() for c in row.cells))
        return lines
    return path.read_text(errors="replace").splitlines()


def race_type_of(cond: str) -> str:
    c = cond.strip().lower()
    for pat, code in RACE_TYPES:
        if re.search(pat, c):
            return code
    return cond.strip().split()[0].upper() if cond.strip() else "UNK"


def _odds(a: str, b: str | None) -> float | None:
    x = float(a)
    if b:
        y = float(b)
        return round(x / y, 2) if y else None
    return x


def _f(x):
    try:
        return float(x)
    except ValueError:
        return None


def _i(x):
    try:
        return int(x)
    except ValueError:
        return None


def parse_entries(line_bucket: str, body: str, race: int, notes: list) -> list[Entry]:
    out = []
    body = body.strip()
    if not body or re.match(r"^(none|covered above)\b", body, re.I):
        return out
    ms = []
    for m in ENTRY.finditer(body):
        tail = body[m.end():m.end() + 80]
        h = HEAD.match(tail)
        has_marker = bool(h and (h.group("g") or h.group("br") or re.match(r"^\s*(?:[—–:-]|\()", tail)
                                 or re.match(r"^\s*Tag:", tail)))
        if has_marker:
            ms.append(m)
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        rest = body[m.end():end].strip().rstrip(",;. ")
        h = HEAD.match(rest)
        bucket = h.group("g") or line_bucket
        br = re.findall(r"\[([^\]]+)\]", h.group("br") or "")
        mods = {b for b in br if b in ("W", "E")}
        tags = set()
        if any("UNSEEN" in b.upper() for b in br):
            tags.add("UNSEEN")
        if any("RETAIL" in b.upper() or "TRAP" in b.upper() for b in br):
            tags.add("RETAIL_TRAP")
        reason = rest[h.end():].strip()
        if reason.startswith("(") and reason.endswith(")"):
            reason = reason[1:-1]
        reason = reason.lstrip(") ").strip()
        if bucket in ("D-INT", "D-DOM"):            # Breeders' Cup D split: stored as D plus a tag
            tags.add(bucket)
            bucket = "D"
        if bucket != line_bucket:
            notes.append((race, "warn", f"#{m.group(1)} sits on the {line_bucket} line but is tagged {bucket}; using {bucket}"))
        if bucket in ("F", "F-") and re.search(r"public|underlay|chalk|favorite|choice", reason, re.I):
            tags.add("RETAIL_TRAP")
        name = m.group(2).strip()
        out.append(Entry(race, m.group(1).upper(), name,
                         _odds(m.group(3), m.group(4)), bucket, mods, reason, tags))
    if not ms:
        notes.append((race, "warn", f"{line_bucket} line has text but no horses could be read: {body[:70]!r}"))
    return out


def parse_lines(lines: list[str]) -> Breakdown:
    bd = Breakdown()
    cur: RaceBlock | None = None
    label, body = None, []

    def flush():
        nonlocal label, body
        if cur is not None and label is not None:
            cur.entries += parse_entries(label, " ".join(body), cur.race, bd.notes)
        label, body = None, []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        h = HEADER_RE.match(line)
        if h:
            flush()
            cur = RaceBlock(race=int(h.group(1)), dist=int(h.group(2)), surface=h.group(3).upper(),
                            race_type=race_type_of(h.group(4)), par_spd=_f(h.group(5)),
                            scenario=SCENARIOS.get(h.group(6).strip().lower()), qsp_sum=_i(h.group(7)))
            if cur.scenario is None:
                bd.notes.append((cur.race, "warn", f"unknown pace scenario {h.group(6)!r}"))
            bd.races.append(cur)
            continue
        if re.match(r"^RACE\s+\d", line, re.I):
            flush(); cur = None
            bd.notes.append((None, "error", f"race header not understood, race skipped: {line[:90]!r}"))
            continue
        if cur is None:
            continue
        s_ = SCRATCH_LINE.match(line)
        if s_:
            flush()
            cur.scratches += [(m.group(1).upper(), m.group(2).strip()) for m in SCRATCH_ONE.finditer(s_.group(0))]
            continue
        if not SNIPER_LINE.match(line):                 # scratches mentioned in passing inside other lines
            for rx in (SCRATCH_A, SCRATCH_B):
                for m in rx.finditer(line):
                    cur.scratches.append((m.group(1).upper(), m.group(2).strip()))
        if SNIPER_LINE.match(line):
            flush()
            cur.sniper = line.split(":", 1)[-1].strip()
            continue
        r_ = REMAINING_LINE.match(line)
        if r_:
            flush()
            label, body = "F", [r_.group(1)]
            continue
        b_ = BUCKET_LINE.match(line)
        if b_:
            flush()
            label, body = b_.group(1), [b_.group(2)]
        elif label is not None and line.startswith("#"):
            body.append(line)
    flush()
    for r in bd.races:
        seen, uniq = set(), []
        graded = {e.pgm for e in r.entries}
        for pgm, name in r.scratches:
            if pgm not in seen and pgm not in graded:
                seen.add(pgm); uniq.append((pgm, name))
        r.scratches = uniq
    return bd


def guess_track_and_date(name: str, year: int | None = None):
    """'Finger_Lakes_Last_Updated__October_26_11_02_AM_ET.docx' -> ('FL', date(…, 10, 26))."""
    stem = re.sub(r"[_\s]+", " ", Path(name).stem).strip()
    t = re.split(r"\blast updated\b", stem, flags=re.I)
    track = TRACK_NAMES.get(t[0].strip().lower())
    d = None
    m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\b", t[1] if len(t) > 1 else stem)
    if m and m.group(1).lower() in MONTHS:
        d = date(year or date.today().year, MONTHS[m.group(1).lower()], int(m.group(2)))
    return track, d


def ingest_breakdown(con, path: str | Path, track: str | None = None, race_date: str | None = None,
                     source: str = "grant_docx") -> tuple[str, dict]:
    path = Path(path)
    g_track, g_date = guess_track_and_date(path.name)
    track = (track or g_track or "").upper()
    if not track:
        raise ValueError(f"{path.name}: track not recognised from the file name; pass --track")
    assumed = race_date is None
    if race_date is None:
        if g_date is None:
            raise ValueError(f"{path.name}: no race date; pass --date YYYY-MM-DD")
        race_date = g_date.isoformat()
    bd = parse_lines(read_lines(path))
    if not bd.races:
        raise ValueError(f"{path.name}: no 'RACE n — … yds / …' headers found; is this the breakdown format?")

    card_id = f"{track}-{race_date}-grant"
    con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM data_quality_flags WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,source_file,notes) VALUES (?,?,?,?,?,?)",
                (card_id, track, race_date, source, path.name, "Grant's per-race grade breakdown"))
    if assumed:
        flag(con, card_id, None, "warn", f"race_date {race_date} taken from the file's 'Last Updated' stamp; "
                                         "confirm it is the day the races ran")
    seen: set = set()
    for r in bd.races:
        if r.race in seen:
            flag(con, card_id, r.race, "error", "race appears twice in the file; second block ignored")
            continue
        seen.add(r.race)
        con.execute("INSERT INTO races(card_id,race,surface,distance_yds,race_type,field_size,qsp_sum,"
                    "pace_scenario,par_spd,grades_complete) VALUES (?,?,?,?,?,?,?,?,?,1)",
                    (card_id, r.race, r.surface, r.dist, r.race_type, len(r.entries), r.qsp_sum, r.scenario, r.par_spd))
        pgms = set()
        for e in r.entries:
            if e.pgm in pgms:
                flag(con, card_id, r.race, "error", f"#{e.pgm} graded twice; keeping the first")
                continue
            pgms.add(e.pgm)
            con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,tags,ml_odds,"
                        "grade_confidence,reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (card_id, r.race, e.pgm, e.name, e.bucket, ",".join(sorted(e.mods)),
                         ",".join(sorted(e.tags)), e.odds, "explicit", e.reason))
        for pgm, name in r.scratches:
            if pgm in pgms:
                flag(con, card_id, r.race, "error", f"#{pgm} is listed as scratched and also graded")
                continue
            con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,scratched,grade_confidence)"
                        " VALUES (?,?,?,?,NULL,1,'explicit')", (card_id, r.race, pgm, name))
        if not r.entries:
            flag(con, card_id, r.race, "error", "no graded horses found in this race block")
        nums = sorted({int(re.match(r"\d+", p).group()) for p in pgms | {x for x, _ in r.scratches}})
        gap = sorted(set(range(1, (nums[-1] if nums else 0) + 1)) - set(nums))
        if gap:
            flag(con, card_id, r.race, "info", f"program numbers {gap} are neither graded nor listed as scratched")
        if r.par_spd is None:
            flag(con, card_id, r.race, "info", "par speed unavailable in the file")
    for race, sev, msg in bd.notes:
        flag(con, card_id, race, sev, msg)
    flags_vs_results(con, card_id, track, race_date)
    con.commit()
    n = sum(len(r.entries) for r in bd.races)
    return card_id, {"races": len(seen), "runners": n, "scratched": sum(len(r.scratches) for r in bd.races),
                     "date": race_date, "date_assumed": assumed}


def flags_vs_results(con, card_id: str, track: str, race_date: str) -> int:
    """Compare the graded field with the official field, if results are already loaded."""
    off = {}
    for r in con.execute("SELECT race, pgm, scratched FROM post_race_official WHERE track=? AND race_date=?",
                         (track, race_date)):
        off.setdefault(r["race"], {})[str(r["pgm"]).upper()] = r["scratched"]
    if not off:
        return 0
    n = 0
    for race in {r[0] for r in con.execute("SELECT race FROM races WHERE card_id=?", (card_id,))}:
        graded = {r["pgm"] for r in con.execute(
            "SELECT pgm FROM pre_race_grades WHERE card_id=? AND race=? AND scratched=0", (card_id, race))}
        o = off.get(race)
        if o is None:
            flag(con, card_id, race, "warn", "race is not in the official results"); n += 1
            continue
        ran = {p for p, s in o.items() if not s}
        if ran - graded:
            flag(con, card_id, race, "warn", f"official starters not graded: {sorted(ran - graded)}"); n += 1
        if graded - ran:
            flag(con, card_id, race, "warn", f"graded but not official starters (late scratch?): {sorted(graded - ran)}"); n += 1
    return n


def find_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.suffix.lower() in (".docx", ".txt") and not x.name.startswith("~$"))
        else:
            out.append(p)
    return out
