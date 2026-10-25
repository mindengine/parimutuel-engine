import csv
import io
import zipfile

from pe import analytics, db
from pe.brisnet import import_brisnet_zip, pool_code
from tests.test_engine import ROOT


def _csv(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def _start(race, pgm, post, odds, fin):
    r = [""] * 89
    r[0:4] = ["BEL", "20261025", str(race), "D"]
    r[4], r[7], r[8], r[30], r[60] = f"Horse {pgm}", str(post), pgm, str(odds), str(fin)
    return r


def _exotic(race, wager, bet, payoff, nums, ncorrect=""):
    r = [""] * 25
    r[0:4] = ["BEL", "20261025", str(race), "D"]
    r[4:11] = [wager, str(bet), str(payoff), ncorrect, nums, "100000", "0"]
    return r


def make_zip(path):
    starts = [_start(6, "5", 5, 3.4, 1), _start(6, "1", 1, 9.1, 2), _start(6, "4", 4, 4.0, 3),
              _start(6, "6", 6, 0.9, 4), _start(6, "7", 7, 7.0, 5), _start(6, "SCR", 99, 0, 0)]
    exotics = [_exotic(6, "$1 Trifecta", 1.0, 211.40, "5-1-4"),
               _exotic(6, "$2 Exacta", 2.0, 68.20, "5-1"),
               _exotic(9, "$0.50 Pick 5", 0.5, 9000.0, "4-6-3-5-10", "5"),
               _exotic(9, "$0.50 Pick 5", 0.5, 120.0, "4-6-3-5-10", "4"),   # consolation, skipped
               _exotic(6, "$1 Mystery Pool", 1.0, 10.0, "5")]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("BEL20261025start.csv", _csv(starts))
        z.writestr("BEL20261025exotic.csv", _csv(exotics))


def test_pool_codes():
    assert pool_code("$1 Exacta") == "EX" and pool_code("$0.10 Superfecta") == "SUP"
    assert pool_code("$0.50 Pick 5") == "P5" and pool_code("$2 Daily Double") == "DD"
    assert pool_code("Bingo") is None


def test_brisnet_zip_end_to_end(tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.ingest_tail_block(con, ROOT / "examples" / "tail_block_example.json")
    z = tmp_path / "charts.zip"
    make_zip(z)
    stats = import_brisnet_zip(con, z)
    assert stats["starts"] == 6 and stats["payouts"] == 3 and stats["skipped_payouts"] == 2
    assert any("Mystery" in w for w in stats["warnings"])
    p5 = con.execute("SELECT winning_combo FROM payouts WHERE pool='P5'").fetchone()[0]
    assert p5 == "4/6/3/5/10"
    g = analytics.grade_tickets(con)
    assert bool(g[g.pool == "TRI"].iloc[0].hit)
