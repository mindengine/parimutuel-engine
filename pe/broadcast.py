"""Loader for Grant's "Boss Dogg broadcast" grade packets (emoji tier names, one table per race).

Only the per-race detail sections ("🏁 RACE n — ...") are read; the at-a-glance matrix at the top
repeats them and is ignored. Each graded horse sits on its own line ("#2 Stowaway (6.0)") under a
tier row such as "🐕 The Top Dawgs (A)"; the short tag lines after it carry the exact grade code
("(A[W])", "(D-DOM[W][E])"), Ghost Mode (UNSEEN) and "[FUGAZI CHALK TRAP]" (RETAIL_TRAP).
Tossed horses are listed inline on "•" bullet lines under the F tier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RACE_HDR = re.compile(r"^🏁\s*RACE\s+(\d+)\s*[—-]\s*(\d+)\s*Yds.*?\|\s*(Dirt|Turf|Synthetic|AW)", re.I)
PAR = re.compile(r"Par Spd:\s*(\d+).*?Pace Scenario:.*?(HONEST|MELTDOWN|LONE[ _]SPEED).*?Active QSP:\s*(\d+)", re.I)
SCR = re.compile(r"Scratched:\s*([^)]*)")
TIER = re.compile(r"\((A\+|A|B|C|D|F-|F)\)\s*(?:\t|$)")
HORSE = re.compile(r"^#(\d+[A-Z]?)\s+(.+?)\s+\((\d+(?:\.\d+)?)\)\s*(\(AE\))?\s*$")
CODE = re.compile(r"\((A\+|A|B|C|D-INT|D-DOM|D|F-|F)((?:\[[WE]\])*)\)")
TOSS = re.compile(r"#(\d+[A-Z]?)\s+([A-Za-z][A-Za-z'’.\- ]*?)\s+\((\d+(?:\.\d+)?)\)")
SCR_ITEM = re.compile(r"#(\d+[A-Z]?)\s+([^,#]+)")


@dataclass
class Grade:
    pgm: str
    name: str
    odds: float
    bucket: str
    mods: set = field(default_factory=set)
    tags: set = field(default_factory=set)
    reason: str = ""


@dataclass
class Race:
    race: int
    dist: int
    surface: str
    par_spd: float | None = None
    qsp: int | None = None
    scenario: str | None = None
    grades: list = field(default_factory=list)
    scratches: list = field(default_factory=list)


def parse(text: str) -> list[Race]:
    races, cur, tier, horse = [], None, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = RACE_HDR.match(line)
        if m:
            cur = Race(int(m.group(1)), int(m.group(2)), {"dirt": "D", "turf": "T"}.get(m.group(3).lower(), "AW"))
            races.append(cur)
            tier = horse = None
            continue
        if cur is None:
            continue                                   # the matrix at the top
        if line.startswith("Par Spd"):
            p = PAR.search(line)
            if p:
                cur.par_spd, cur.scenario, cur.qsp = float(p.group(1)), p.group(2).upper().replace(" ", "_"), int(p.group(3))
            s = SCR.search(line)
            if s:
                cur.scratches += [(a, b.strip()) for a, b in SCR_ITEM.findall(s.group(1))]
            continue
        if line.startswith("🎙") or line.startswith("Hotbox") or line.startswith("Boss Dogg Grade"):
            tier = horse = None
            continue
        t = TIER.search(line)
        if t and line.index(t.group(0)) < 60:
            tier, horse = t.group(1), None
            if "•" in line:
                line = line[line.index("•"):]
            else:
                continue
        if tier is None:
            continue
        if "•" in line:                                # toss list under the F / F- tier
            horse = None
            if "Note:" in line.split("#")[0]:
                continue                               # e.g. "Underlay Note: #3 Libertango" (graded elsewhere)
            for pgm, name, odds in TOSS.findall(line.split(":", 1)[1] if ":" in line.split("#")[0] else line):
                cur.grades.append(Grade(pgm, name.strip(), float(odds), tier))
            continue
        h = HORSE.match(line)
        if h:
            horse = Grade(h.group(1), h.group(2).strip(), float(h.group(3)), tier)
            if h.group(4):
                horse.tags.add("AE")
            cur.grades.append(horse)
            continue
        if horse is None:
            continue
        if len(line) < 90 and not horse.reason:        # tag lines between the horse and its reason
            c = CODE.search(line)
            if c:
                code = c.group(1)
                if code in ("D-INT", "D-DOM"):
                    horse.tags.add(code)
                    code = "D"
                horse.bucket = code
                horse.mods |= set(re.findall(r"\[([WE])\]", c.group(2)))
            if "Ghost Mode" in line or "🕵" in line or "UNSEEN" in line:
                horse.tags.add("UNSEEN")
            if "TRAP" in line:
                horse.tags.add("RETAIL_TRAP")
            if "💨" in line:
                horse.mods.add("W")
            if "🕹" in line:
                horse.mods.add("E")
            continue
        if not horse.reason:
            horse.reason = line
    return races


def ingest(con, path: str | Path, card_id: str, track: str, race_date: str, source: str = "grant_broadcast"):
    races = parse(Path(path).read_text(encoding="utf-8"))
    if not races:
        raise ValueError(f"{path}: no '🏁 RACE n' sections found")
    return races, write(con, races, path, card_id, track, race_date, source)


def write(con, races, path, card_id: str, track: str, race_date: str, source: str):
    """Replace card_id's grades with `races` (list of Race). Returns a list of problems found."""
    con.execute("DELETE FROM pre_race_grades WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM races WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,source_file,notes) VALUES (?,?,?,?,?,?)",
                (card_id, track, race_date, source, Path(path).name, f"Grant's grades ({source})"))
    problems = []
    for r in races:
        con.execute("INSERT INTO races(card_id,race,surface,distance_yds,field_size,qsp_sum,pace_scenario,par_spd,"
                    "grades_complete) VALUES (?,?,?,?,?,?,?,?,1)",
                    (card_id, r.race, r.surface, r.dist, len(r.grades), r.qsp, r.scenario, r.par_spd))
        seen = set()
        for g in r.grades:
            if g.pgm in seen:
                problems.append(f"R{r.race} #{g.pgm} {g.name} graded twice; kept the first")
                continue
            seen.add(g.pgm)
            con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,tags,ml_odds,"
                        "grade_confidence,reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (card_id, r.race, g.pgm, g.name, g.bucket, ",".join(sorted(g.mods)),
                         ",".join(sorted(g.tags - {"AE"})), g.odds, "explicit", g.reason or None))
        for pgm, name in r.scratches:
            if pgm not in seen:
                con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,scratched,grade_confidence)"
                            " VALUES (?,?,?,?,NULL,1,'explicit')", (card_id, r.race, pgm, name))
    con.commit()
    return problems
