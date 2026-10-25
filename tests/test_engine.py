import csv
from pathlib import Path

import pytest

from pe import analytics, db
from pe.legacy import ingest_legacy
from pe.tickets import Ticket, box, vertical_combos

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "archive"


# ---------- combinatorics ----------

def test_belmont_r6_trifecta_is_4_not_6():
    t = Ticket(pool="TRI", base=2.0, race=6, positions=[["4", "5"], ["1", "4", "5"], ["1", "4", "5"]],
               claimed_combos=6, claimed_cost=12.0)
    assert t.combos == 4 and t.cost == 8.0
    assert any("claimed 6" in i for i in t.validate())


def test_exacta_key_and_box():
    assert vertical_combos([["4", "5"], ["1", "4", "5"]]) == 4
    assert vertical_combos(box(["1", "2", "3", "4"], 2)) == 12
    assert vertical_combos(box(["1", "2", "3"], 3)) == 6


def test_horizontal_product_and_leg_count():
    t = Ticket(pool="P5", base=0.5, legs=[["2", "6", "8", "9"], ["1", "4", "5"], ["3", "5"], ["5"], ["7", "10", "12"]])
    assert t.combos == 72 and t.cost == 36.0 and t.validate() == []
    bad = Ticket(pool="P4", base=1.0, legs=[["1"], ["2"], ["3"]])
    assert any("needs 4 legs" in i for i in bad.validate())


def test_hits():
    t = Ticket(pool="P4", base=1, legs=[["1", "4", "5"], ["3", "5"], ["5"], ["5", "7"]])
    assert t.hits(["4", "3", "5", "7"]) and not t.hits(["6", "3", "5", "7"])
    e = Ticket(pool="EX", base=1, positions=[["3"], ["1", "6"]])
    assert e.hits(["3", "6", "2"]) and not e.hits(["6", "3"])


# ---------- archive parser ----------

@pytest.fixture
def con(tmp_path):
    return db.connect(tmp_path / "t.db")


def grade(con, cid, race, pgm):
    return con.execute("SELECT bucket FROM pre_race_grades WHERE card_id=? AND race=? AND pgm=?",
                       (cid, race, pgm)).fetchone()["bucket"]


def test_belmont_archive(con):
    cid, stats = ingest_legacy(con, ARCHIVE / "bel25.txt", race_date="2026-10-25")
    assert cid == "BEL-2026-10-25"
    assert grade(con, cid, 6, "6") == "F-"          # Mo for the King
    assert grade(con, cid, 8, "5") == "A+"          # Lion Lake
    assert grade(con, cid, 5, "4") == "A+"          # Ground Support
    assert grade(con, cid, 1, "2") == "F"           # Kayla's Komet (retail trap)
    assert grade(con, cid, 4, "6") == "A"           # Florida Patriot, not mis-assigned to R2
    assert con.execute("SELECT COUNT(*) FROM pre_race_grades WHERE card_id=? AND race=2 AND pgm='6'",
                       (cid,)).fetchone()[0] == 0
    pools = [r["pool"] for r in con.execute("SELECT pool FROM tickets WHERE card_id=? ORDER BY ticket_id", (cid,))]
    assert pools == ["P5", "P5", "P5", "P5", "P4", "P4"]


@pytest.mark.parametrize("f", ["bel25.txt", "chrch25.txt", "lrl254.txt", "gp25.txt", "px25.txt"])
def test_all_archives_parse(con, f):
    cid, stats = ingest_legacy(con, ARCHIVE / f)
    assert stats["graded"] >= 20 and stats["tickets"] >= 4


# ---------- tail block -> results -> analytics ----------

def _write(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def test_end_to_end_lott(con, tmp_path):
    cid = db.ingest_tail_block(con, ROOT / "examples" / "tail_block_example.json")
    flags = [r["message"] for r in con.execute("SELECT message FROM data_quality_flags WHERE card_id=?", (cid,))]
    assert any("claimed 6 combos" in m for m in flags)

    # Synthetic result: 5-1-4, F- favorite #6 off the board.
    _write(tmp_path / "res.csv", ["track", "race_date", "race", "pgm", "finish_pos", "final_odds"],
           [["BEL", "2026-10-25", 6, "5", 1, 3.4], ["BEL", "2026-10-25", 6, "1", 2, 9.1],
            ["BEL", "2026-10-25", 6, "4", 3, 4.0], ["BEL", "2026-10-25", 6, "6", 4, 0.9],
            ["BEL", "2026-10-25", 6, "7", 5, 7.0]])
    _write(tmp_path / "pay.csv", ["track", "race_date", "race", "pool", "winning_combo", "base", "payout"],
           [["BEL", "2026-10-25", 6, "TRI", "5-1-4", 1.0, 211.40],
            ["BEL", "2026-10-25", 6, "EX", "5-1", 2.0, 68.20]])
    db.import_results_csv(con, tmp_path / "res.csv")
    db.import_payouts_csv(con, tmp_path / "pay.csv")

    g = analytics.grade_tickets(con)
    tri = g[g.pool == "TRI"].iloc[0]
    assert bool(tri.hit) and tri.returned == pytest.approx(422.80)   # $2 base on a $1 payout
    ex = g[g.pool == "EX"].iloc[0]
    assert bool(ex.hit) and ex.returned == pytest.approx(102.30)     # $3 base on a $2 payout

    hr = analytics.bucket_hit_rates(con)
    fminus = hr[hr.bucket == "F-"].iloc[0]
    assert fminus.starters == 1 and fminus.show_pct == 0

    lott = analytics.lott_audit(con)
    assert lott.system_covered.all() and lott.ticket_hit.all() and not lott.left_on_table.any()
