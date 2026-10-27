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
