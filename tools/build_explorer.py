"""Build frontend/explorer.html from the v2 database.

    python tools/build_explorer.py            # after python -m pe.v2 rebuild

The page is self-contained (data embedded), so it opens straight from disk
and can be published or emailed.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "engine_v2.db"
TEMPLATE = ROOT / "frontend" / "explorer_template.html"
OUT = ROOT / "frontend" / "explorer.html"

RUNNER_COLS = ["race_id", "prog_num", "post_pos", "horse_name", "is_scratched", "is_fts", "ml_odds", "run_style",
               "qsp", "prime_power", "pp_rank", "pp_lead_over_2nd", "last_fig", "last_fig_delta", "best_3back_delta",
               "last_e1", "last_e1_rank", "e1_lead_over_ep", "last_lp", "last_lp_rank", "purse_ratio", "days_off",
               "is_bullet_work", "work_1_sec_per_furlong", "has_trouble_comment", "last_comment", "jockey",
               "valid_jock_pct", "trainer", "valid_trn_pct", "primary_grade", "grade_source", "grade_reasons",
               "tag_w", "tag_e", "tag_retail_trap", "tag_unseen_retail", "has_herd_radar", "finish_pos",
               "final_tote_odds", "clv_ratio"]


def main(db=DB):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    races = [dict(r) for r in con.execute("SELECT * FROM races ORDER BY race_date, track_code, race_num")]
    runners = [dict(r) for r in con.execute(f"SELECT {','.join(RUNNER_COLS)} FROM runners ORDER BY race_id, post_pos")]
    pools = [dict(r) for r in con.execute("SELECT * FROM pools")]
    tickets = [dict(r) for r in con.execute("SELECT * FROM tickets")]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("races", "runners", "pools", "tickets")}
    cols = {t: [c[1] for c in con.execute(f"PRAGMA table_xinfo({t})")] for t in ("races", "runners", "pools", "tickets")}
    data = {"races": races, "runners": runners, "pools": pools, "tickets": tickets, "counts": counts, "columns": cols,
            "settled": con.execute("SELECT COUNT(*) FROM runners WHERE finish_pos IS NOT NULL").fetchone()[0]}
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))
    print(f"wrote {OUT.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DB)
