from pathlib import Path

from pe import db
from pe.picks_csv import ingest_picks_csv

EX = Path(__file__).resolve().parents[1] / "examples"


def test_picks_and_merged_csvs_load(tmp_path):
    con = db.connect(tmp_path / "t.db")
    cid, st = ingest_picks_csv(con, EX / "FL_20261027_picks_and_tags.csv")
    assert cid == "FL-2026-10-27-grant" and st["runners"] == 55 and not st["merged"]
    r = con.execute("SELECT bucket, modifiers, tags FROM pre_race_grades WHERE card_id=? AND race=1 AND pgm='5'", (cid,)).fetchone()
    assert tuple(r) == ("B", "E,W", "UNSEEN")
    cid, st = ingest_picks_csv(con, EX / "FL_20261028_master_merged.csv")
    assert st["merged"] and st["runners"] == 53
    race = con.execute("SELECT qsp_sum, pace_scenario, par_spd FROM races WHERE card_id=? AND race=1", (cid,)).fetchone()
    assert tuple(race) == (15, "LONE_SPEED", 79.0)


def test_breeders_cup_d_split_kept_as_d_plus_tag(tmp_path):
    f = tmp_path / "KEE_20261031_picks_and_tags.csv"
    f.write_text("Race,Post,Horse,ML_Odds,Grade,Modifiers,Full_Tag,Retail_Radar\n"
                 "11,1,ETHICAL DIAMOND,10.0,D-INT,[W],D-INT[W],[UNSEEN BY RETAIL]\n"
                 "11,2,SOMEONE,6.0,D-DOM,,D-DOM,\n11,3,OTHER,3.0,A,,A,\n")
    con = db.connect(tmp_path / "t.db")
    cid, _ = ingest_picks_csv(con, f)
    rows = {r[0]: (r[1], r[2]) for r in con.execute("SELECT pgm, bucket, tags FROM pre_race_grades WHERE card_id=?", (cid,))}
    assert rows["1"] == ("D", "D-INT,UNSEEN") and rows["2"] == ("D", "D-DOM") and rows["3"] == ("A", "")


def test_distill_v3_group_class():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dv3", Path(__file__).resolve().parents[1] / "tools" / "distill_v3.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.group_class("G1", "Travers-G1") == "G1"
    assert m.group_class("N", "ExcelsiorL150k") == "L" and m.group_class("N", "YorktownB100k") == "Stk"
    assert m.group_class("AO", "OC80000n2x") == "Alw" and m.group_class("S", "MdSpWt") == "MSW"
