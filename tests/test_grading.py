from pathlib import Path

from pe.grading import grade_card, pace_scenario

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "distilled_BEL_2026-10-22.csv"


def test_pace_scenario_thresholds():
    assert pace_scenario(12) == "LONE_SPEED" and pace_scenario(22) == "HONEST" and pace_scenario(48) == "MELTDOWN"


def test_grades_every_runner_and_finds_exonerate_anchor():
    grades, races, df = grade_card(str(SAMPLE))
    assert len(grades) == len(df)                       # every runner gets a bucket
    assert all(g.bucket in {"A+", "A", "B", "C", "D", "F-", "F"} for g in grades)
    ex = next(g for g in grades if g.race == 8 and g.pgm == "5")
    assert ex.bucket == "A+"                            # Grant's ledger: R8 #5 Exonerate (A+)
    assert races[8]["qsp_sum"] == 51 and races[8]["pace_scenario"] == "MELTDOWN"
    fts = [g for g in grades if g.race == 5 and g.pgm in {"4", "5", "10", "11"}]
    assert all(g.bucket in {"D", "F"} for g in fts)     # Law 4: FTS only D or F
