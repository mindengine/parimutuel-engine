"""Loader for Grant's "Sniper" grade breakdown (plain-text bullets, one block per race).

    RACE 4 — 1540 / D / BCFMSprt-G1 | Par Spd: 101 | Active Pace Scenario: Meltdown (Active QSP Sum: 40 | ...)
    (Scratched: #3 Praying, #13 Nooni)
    * A :
       * #4 R Disaster (8/1) — A[W] [UNSEEN BY RETAIL] — Prime_Power: ...
    * D (Form-Translation & Smell Test):
       1. #10 Expensive Queen (15/1) — D-DOM[W] — [UNSEEN BY RETAIL] — ...
    * F (Chalk Fades & Tosses):
       * 3. Remaining Domestic Tosses: #7 Durante (20/1) (...), #1 Look Forward (30/1) (...)
    * Sniper Action (...): ...

Grades come out as pe.broadcast.Race/Grade so pe.broadcast.write stores them.
"""
from __future__ import annotations

import re
from pathlib import Path

from pe.broadcast import Grade, Race, write

HDR = re.compile(r"^RACE\s+(\d+)\s*[—-]\s*(\d+)\s*(?:yds)?\s*/\s*([DT]|AW)\s*/\s*([^|]+?)\s*\|\s*Par Spd:\s*(\d+)"
                 r".*?Pace Scenario:\s*([^(|]+?)\s*\(Active QSP Sum:\s*(\d+)", re.I)
SECTION = re.compile(r"^\*\s*(A\+|A|B|C|D|F-|F)\s*(?:\(|:)")
END = re.compile(r"^\*\s*Sniper", re.I)
ODDS = r"\((\d+/\d+|\d+(?:\.\d+)?)\)"
NAME = r"([A-Z0-9][^#()]*?(?:\s\([A-Z]{2,3}\))?)"
ENTRY = re.compile(r"^\s+(?:\*|\d+\.)\s+#(\d+[A-Z]?)(?:-AE)?\s+" + NAME + r"\s+" + ODDS + r"\s*(?:—\s*(.*))?$")
TOSS_LINE = re.compile(r"^\s+\*\s+\d\.\s+[^:#]*:\s*(.*)$")
TOSS = re.compile(r"#(\d+[A-Z]?)(?:-AE)?\s+" + NAME + r"\s+" + ODDS)
CODE = re.compile(r"^\[?(A\+|A|B|C|D-INT|D-DOM|D|F-|F)\]?((?:\[[WE]\])*)(?![A-Za-z])")
SCR_ITEM = re.compile(r"#(\d+[A-Z]?)\s+([^,#|()]+)")


def _odds(s):
    a, _, b = s.partition("/")
    return float(a) / float(b) if b else float(a)


def _scenario(s):
    s = s.upper()
    return "MELTDOWN" if "MELTDOWN" in s else "LONE_SPEED" if "LONE" in s else "HONEST"


def parse(text: str):
    races, cur, section, problems = [], None, None, []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = HDR.match(line.strip())
        if m:
            cur = Race(int(m.group(1)), int(m.group(2)), m.group(3).upper(), float(m.group(5)),
                       int(m.group(7)), _scenario(m.group(6)))
            races.append(cur)
            section = None
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("(") and "Scratch" in s.split(":")[0]:
            body = s.split(":", 1)[1].split("|")[0]
            if not body.strip().lower().startswith("none"):
                cur.scratches += [(p, n.strip()) for p, n in SCR_ITEM.findall(body)]
            continue
        if END.match(s):
            section = None
            continue
        sm = SECTION.match(s)
        if sm:
            section = sm.group(1)
            continue
        if section is None:
            continue
        t = TOSS_LINE.match(line)
        if t:
            for pgm, name, odds in TOSS.findall(t.group(1)):
                cur.grades.append(Grade(pgm, name.strip(), _odds(odds), section))
            continue
        e = ENTRY.match(line)
        if not e:
            if line.startswith((" ", "\t")) and "#" in line:
                problems.append(f"R{cur.race}: line not understood: {s[:80]}")
            continue
        g = Grade(e.group(1), e.group(2).strip(), _odds(e.group(3)), section)
        rest = e.group(4) or ""
        segs = [x.strip() for x in rest.split(" — ")]
        head = []
        for seg in segs:
            if len(seg) < 45 and re.match(r"^(\[|A\+?(\[|\s|$)|[BCF]-?(\[|\s|$)|D(-|\[|\s|$))", seg):
                head.append(seg)
            else:
                break
        g.reason = " — ".join(segs[len(head):]) or None
        h = " ".join(head)
        c = CODE.match(h)
        if c:
            code = c.group(1)
            if code in ("D-INT", "D-DOM"):
                g.tags.add(code)
                code = "D"
            if code != section and not (section == "F" and code == "F-"):
                problems.append(f"R{cur.race} #{g.pgm} {g.name}: code {c.group(1)} listed under section {section}; used {code}")
            g.bucket = code
            g.mods |= set(re.findall(r"\[([WE])\]", c.group(2)))
        if "UNSEEN" in h:
            g.tags.add("UNSEEN")
        if "TRAP" in h:
            g.tags.add("RETAIL_TRAP")
        cur.grades.append(g)
    return races, problems


def ingest(con, path, card_id: str, track: str, race_date: str, source: str = "grant_sniper"):
    races, problems = parse(Path(path).read_text(encoding="utf-8"))
    if not races:
        raise ValueError(f"{path}: no 'RACE n — ...' headers found")
    return races, problems + write(con, races, path, card_id, track, race_date, source)
