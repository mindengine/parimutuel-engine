import csv
import warnings

import pandas as pd
import pytest

from pe.v2 import etl, pools as P

CARD = "examples/distilled_BEL_2026-10-22.csv"
warnings.filterwarnings("ignore")


# ---- pools: cost-to-capture --------------------------------------------------

def test_horizontal_capture_is_product_of_tier_counts():
    legs = [{"1": "A", "2": "B", "3": "F"}, {"1": "A+", "2": "A", "3": "C"}]
    c = P.capture("DD", legs, [["2"], ["1"]])
    assert c["lowest_tier_needed"] == "B"           # deepest winner is a B
    assert c["combos_to_capture"] == 2 * 2           # leg1 {A,B}, leg2 {A+,A}
    assert c["captured_by_playable"] == 1


def test_any_f_winner_is_f_miss():
    c = P.capture("DD", [{"1": "A"}, {"1": "F-"}], [["1"], ["1"]])
    assert c == {"captured_by_playable": 0, "lowest_tier_needed": "F_MISS",
                 "combos_to_capture": None, "combos_to_capture_keyed": None}


def test_dead_heat_takes_shallowest_winner():
    c = P.capture("DD", [{"1": "A", "2": "D"}, {"1": "A"}], [["1", "2"], ["1"]])
    assert c["lowest_tier_needed"] == "A"


def test_vertical_box_and_keyed():
    race = {"1": "A+", "2": "A", "3": "B", "4": "B", "5": "F"}
    c = P.capture("TRI", [race], [["2"], ["3"], ["1"]])
    assert c["lowest_tier_needed"] == "B"
    assert c["combos_to_capture"] == 4 * 3 * 2          # 4!/(4-3)!
    assert c["combos_to_capture_keyed"] == 2 * 3 * 2    # |{A+,A}| * 3!/1!


def test_keyed_is_none_when_winner_not_top():
    race = {"1": "A+", "2": "B", "3": "B"}
    assert P.capture("EXA", [race], [["2"], ["1"]])["combos_to_capture_keyed"] is None


# ---- pools: disjoint slips ---------------------------------------------------

def test_disjoint_slips_pass_and_overlap_raises():
    ok = [[["1"], ["2", "3"]], [["4"], ["2", "3"]]]
    assert [len(s) for s in P.assert_disjoint(ok)] == [2, 2]
    with pytest.raises(P.OverlapError, match="slips 1 and 2"):
        P.assert_disjoint([[["1", "4"], ["2"]], [["4"], ["2", "3"]]])


def test_vertical_slip_drops_same_runner_twice():
    assert len(P.slip_combos([["1", "2"], ["1", "2"]], vertical=True)) == 2


# ---- ETL rules ----------------------------------------------------------------

def test_bullet_decoder_and_connection_gate(tmp_path):
    df = pd.read_csv(CARD, dtype=str, keep_default_na=False)
    df.loc[0, "Work_1_Time"] = "-48.2"
    df.loc[0, "Work_1_Dist"] = "880"
    df.loc[0, "Jock_Sts"] = "3"
    f = tmp_path / "c.csv"
    df.to_csv(f, index=False)
    d = etl.stage1_load(f)
    assert d.loc[0, "is_bullet_work"] == 1
    assert d.loc[0, "work_1_seconds"] == pytest.approx(48.2)
    assert d.loc[0, "work_1_sec_per_furlong"] == pytest.approx(12.05)
    assert pd.isna(d.loc[0, "valid_jock_pct"])


def test_fig_delta_floor(tmp_path):
    df = pd.read_csv(CARD, dtype=str, keep_default_na=False)
    df.loc[0, "Last_Fig"] = "-3"
    f = tmp_path / "c.csv"
    df.to_csv(f, index=False)
    assert etl.stage1_load(f).loc[0, "last_fig_delta"] == etl.FIG_DELTA_FLOOR


def test_scratch_removed_before_ranking():
    d = etl.stage1_load(CARD)
    r1 = d[d["Race"] == 1]
    top = r1.loc[r1["Prime_Power"].idxmax(), "Prog"]
    d2, races = etl.stage2_scratch(d, {1: [top]})
    a = d2[(d2["Race"] == 1) & (d2["is_scratched"] == 0)]
    assert races[1]["active_starters"] == len(r1) - 1
    assert sorted(a["pp_rank"].tolist())[0] == 1
    assert pd.isna(d2.loc[(d2["Race"] == 1) & (d2["Prog"] == top), "pp_rank"]).all()


def test_ids():
    assert etl.runner_id(20261026, "mvr", 6, "1a") == "20261026_MVR_06_01A"


# ---- end to end ---------------------------------------------------------------

@pytest.fixture
def settled(tmp_path):
    con = etl.connect(":memory:")
    herd = [{"race": 1, "prog": "5", "drf_pick_rank": 1, "consensus_rank": 1, "beyer_1": 80},
            {"race": 1, "prog": "1", "drf_pick_rank": 0, "consensus_rank": 0, "beyer_1": 75}]
    etl.ingest_card(con, CARD, {"scratches": {"1": ["2"]}, "herd_radar": herd})
    rows = con.execute("SELECT race_id, prog_num, ml_odds FROM runners WHERE is_scratched=0 ORDER BY race_id, "
                       "prime_power DESC").fetchall()
    res = tmp_path / "res.csv"
    with res.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["race", "prog", "finish_pos", "final_odds", "win_pay", "place_pay", "show_pay"])
        pos = {}
        for r in rows:
            race = int(r["race_id"][-2:])
            pos[race] = pos.get(race, 0) + 1
            w.writerow([race, r["prog_num"], pos[race], r["ml_odds"], 6.0 if pos[race] == 1 else 0, 0, 0])
    win = {int(r["race_id"][-2:]): r["prog_num"] for r in reversed(rows)}  # top PP wins each race
    pl = tmp_path / "pools.csv"
    pd.DataFrame([{"pool_type": "DD", "first_race": 1, "winning_prog_str": f"{win[1]}-{win[2]}", "base": 2,
                   "payout": 30.0}]).to_csv(pl, index=False)
    pid = etl.ensure_pool(con, "BEL", 20261022, 1, "DD")
    etl.log_slips(con, pid, [{"slip_tier": "Slip_1_Primary", "matrix": f"{win[1]} / {win[2]}", "base": 1}])
    s = etl.settle_card(con, "BEL", 20261022, res, pl)
    return con, s


def test_settlement_and_views(settled):
    con, s = settled
    assert s["pools"] == 1 and s["tickets"] == 1
    p = con.execute("SELECT * FROM pools").fetchone()
    assert p["payout_1d_norm"] == 15.0 and p["captured_by_playable"] in (0, 1)
    assert p["theo_win_parlay_1d"] > 0 and p["parlay_leverage_ratio"] > 0
    t = con.execute("SELECT * FROM tickets").fetchone()
    assert t["is_winner"] == 1 and t["gross_return"] == 15.0
    r = con.execute("SELECT clv_ratio, final_implied_prob_q FROM runners WHERE finish_pos=1 LIMIT 1").fetchone()
    assert r["clv_ratio"] == pytest.approx(1.0)
    q = con.execute("SELECT SUM(final_implied_prob_q) FROM runners WHERE race_id='20261022_BEL_01'").fetchone()[0]
    assert q == pytest.approx(1.0)
    for v in ("v_grade_calibration", "v_pool_efficiency", "v_parameter_leaks"):
        con.execute(f"SELECT * FROM {v}").fetchall()
    assert con.execute("SELECT SUM(starters) FROM v_grade_calibration").fetchone()[0] > 0


def test_unseen_retail_only_with_radar(settled):
    con, _ = settled
    u = dict(con.execute("SELECT prog_num, tag_unseen_retail FROM runners WHERE race_id='20261022_BEL_01' "
                         "AND is_scratched=0").fetchall())
    assert u["1"] == 1 and u["5"] == 0
    assert con.execute("SELECT MAX(tag_unseen_retail) FROM runners WHERE race_id!='20261022_BEL_01'").fetchone()[0] == 0


def test_mnr_card_with_na_values_and_missing_pars():
    con = etl.connect(":memory:")
    ids = etl.ingest_card(con, "examples/distilled_MNR_2026-10-26.csv")
    assert len(ids) == 8
    assert con.execute("SELECT COUNT(*) FROM runners WHERE primary_grade IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT SUM(is_bullet_work) FROM runners").fetchone()[0] == 6
    # Race 5 has no pars, so no par-relative deltas
    assert con.execute("SELECT COUNT(last_fig_delta) FROM runners WHERE race_id='20261026_MNR_05'").fetchone()[0] == 0
