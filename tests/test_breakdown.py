import sqlite3
from pathlib import Path

from pe import db
from pe.breakdown import guess_track_and_date, ingest_breakdown, parse_lines

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "FL_breakdown_sample.txt"
TXT = SAMPLE.read_text().splitlines()


def test_file_name_gives_track_and_date():
    t, d = guess_track_and_date("Finger_Lakes_Last_Updated__October_26_11_02_AM_ET.docx")
    assert t == "FL" and (d.month, d.day) == (10, 26)


def test_parses_every_graded_horse_and_scratches():
    bd = parse_lines(TXT)
    r = {x.race: x for x in bd.races}
    assert len(r) == 8
    assert r[1].scenario == "HONEST" and r[1].qsp_sum == 25 and r[1].dist == 1320 and r[1].par_spd == 77
    assert r[3].race_type == "MSW" and r[4].race_type == "CLM" and r[7].race_type == "ALW"
    assert r[8].scenario == "MELTDOWN" and r[8].scratches == [("4", "Divine Peace")]
    pr = {(e.pgm): e for e in r[1].entries}
    assert pr["4"].bucket == "A" and pr["4"].mods == {"W"} and pr["4"].odds == 6.0
    assert pr["3"].bucket == "F" and pr["5"].bucket == "F"            # both horses on the F line
    r4 = {e.pgm: e for e in r[4].entries}
    assert r4["2"].bucket == "A+" and r4["7"].mods == {"E", "W"}
    assert not bd.notes                                   # nothing odd: every line read cleanly


def test_ingest_stores_complete_card_and_is_repeatable(tmp_path):
    con = db.connect(tmp_path / "t.db")
    cid, st = ingest_breakdown(con, SAMPLE, "FL", "2026-10-26")
    cid, st2 = ingest_breakdown(con, SAMPLE, "FL", "2026-10-26")      # re-load replaces, never duplicates
    assert st == st2 and cid == "FL-2026-10-26-grant"
    n = con.execute("SELECT COUNT(*) FROM pre_race_grades WHERE card_id=? AND scratched=0", (cid,)).fetchone()[0]
    assert n == st["runners"] > 40
    assert con.execute("SELECT MIN(grades_complete) FROM races WHERE card_id=?", (cid,)).fetchone()[0] == 1
    assert con.execute("SELECT reason FROM pre_race_grades WHERE card_id=? AND race=1 AND pgm='4'", (cid,)).fetchone()[0]


VARIANT = """RACE 2 (Quarter Horse Finale) — 400 Yards / D / Alw 19500n2l | Par Spd: Unavailable (NaN) | Active Pace Scenario: Lone Speed (Active QSP Sum: Unavailable)
(Scratch Note: #4 The Last Moxy is scratched; 7 active runners remain.)
A+ : None (#8 No Ceiling leads Prime_Power by +0.67 pts over #4 Eyael (4.5), short of the threshold)
A :
#6 Tap the Dot (2.5) — A — #1 Prime_Power (119.74), holds #1 Last_LP (86 vs Par_LP 82).
#7 Sonic Posse (4.0 ML) — A[W][E] [UNSEEN BY RETAIL] — Rank 2 Prime_Power.
B : #1 [Post 4] Lookin At Mac (ML 1.5) — Tag: [W] [UNSEEN BY RETAIL] — Rank #2. #2 Corkscrew Andy (9/2) — Tag: None — Rank 3.
F- (Nuclear Bet-Against Lock): None (scratched: #5 Bertie's World)
F (Chalk Fades & Tosses): #3 Runaway Power (2/1) [RETAIL TRAP FADE] (Morning-line favorite), #8 Last One (30.0 — Prime_Power last).
Remaining FTS tosses: #9 Zarianzaeli (15.00) (FTS, 0.0% trainer).
"""


def test_other_layouts_odds_forms_tags_and_scratches():
    bd = parse_lines(VARIANT.splitlines())
    r = bd.races[0]
    assert r.race == 2 and r.dist == 400 and r.par_spd is None and r.qsp_sum is None and r.scenario == "LONE_SPEED"
    e = {x.pgm: x for x in r.entries}
    assert set(e) == {"6", "7", "1", "2", "3", "8", "9"}      # A+ line says None; "Eyael (4.5)" inside it is not a runner
    assert e["7"].bucket == "A" and e["7"].mods == {"W", "E"} and "UNSEEN" in e["7"].tags and e["7"].odds == 4.0
    assert e["1"].bucket == "B" and e["1"].mods == {"W"} and e["1"].odds == 1.5     # "[Post 4]" and "ML 1.5"
    assert e["2"].mods == set() and e["2"].odds == 4.5                              # "Tag: None", 9/2
    assert e["3"].bucket == "F" and "RETAIL_TRAP" in e["3"].tags and e["3"].odds == 2.0
    assert e["8"].odds == 30.0 and e["9"].bucket == "F"                              # unclosed "(30.0 —", "Remaining …:" line
    assert {p for p, _ in r.scratches} == {"4", "5"} and not bd.notes
