"""Command line:  python -m pe <command> ...

  init                         create the database
  ingest  <tail_block.json>... load structured card output
  legacy  <card.txt> [--track BEL] [--date 2026-10-25]   parse an archived chat output
  results <results.csv>        load official finish order
  payouts <payouts.csv>        load official payouts
  brisnet <charts.zip>...      load Brisnet Comprehensive Charts ZIPs (finish, odds, payouts)
  grade <distilled.csv> --track BEL --date 2026-10-25   rules-grade every starter (load results first)
  validate <tail_block.json>   check a tail block without loading it
  report                       print flags, hit rates and the LOTT audit
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from . import analytics, db
from .legacy import ingest_legacy
from .tickets import Ticket


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pe")
    ap.add_argument("--db", default=str(db.DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("ingest"); p.add_argument("files", nargs="+")
    p = sub.add_parser("legacy"); p.add_argument("files", nargs="+")
    p.add_argument("--track"); p.add_argument("--date")
    p = sub.add_parser("results"); p.add_argument("file")
    p = sub.add_parser("payouts"); p.add_argument("file")
    p = sub.add_parser("brisnet"); p.add_argument("files", nargs="+")
    p = sub.add_parser("grade"); p.add_argument("file"); p.add_argument("--track", required=True); p.add_argument("--date", required=True)
    p = sub.add_parser("validate"); p.add_argument("file")
    sub.add_parser("report")
    a = ap.parse_args(argv)

    if a.cmd == "validate":
        data = json.load(open(a.file))
        bad = 0
        for t in data.get("tickets", []):
            tk = Ticket(pool=t["pool"], base=float(t["base"]), legs=t.get("legs"),
                        positions=t.get("positions"), race=t.get("race"), label=t.get("label", ""),
                        claimed_combos=t.get("claimed_combos"), claimed_cost=t.get("claimed_cost"))
            issues = tk.validate()
            mark = "FAIL" if issues else "ok  "
            bad += bool(issues)
            print(f"{mark} {tk.label or tk.pool:40.40} {tk.combos:>5} combos ${tk.cost:>8.2f}"
                  + ("  <- " + "; ".join(issues) if issues else ""))
        sys.exit(1 if bad else 0)

    con = db.connect(a.db)
    if a.cmd == "init":
        print(f"database ready: {a.db}")
    elif a.cmd == "ingest":
        for f in a.files:
            print("loaded", db.ingest_tail_block(con, f))
    elif a.cmd == "legacy":
        for f in a.files:
            cid, stats = ingest_legacy(con, f, a.track, a.date)
            print(cid, stats)
    elif a.cmd == "results":
        print(db.import_results_csv(con, a.file), "result rows")
    elif a.cmd == "payouts":
        print(db.import_payouts_csv(con, a.file), "payout rows")
    elif a.cmd == "brisnet":
        from .brisnet import import_brisnet_zip
        for f in a.files:
            print(f, import_brisnet_zip(con, f))
    elif a.cmd == "grade":
        from .grading import load_graded_card
        print("graded", load_graded_card(con, a.file, a.track.upper(), a.date))
    elif a.cmd == "report":
        pd.set_option("display.width", 200, "display.max_columns", 30)
        print("== Data quality flags ==")
        print(pd.read_sql_query("SELECT card_id, race, severity, message FROM data_quality_flags", con)
              .to_string(index=False))
        print("\n== Bucket hit rates ==")
        print(analytics.bucket_hit_rates(con).to_string(index=False))
        print("\n== LOTT audit ==")
        print(analytics.lott_audit(con).to_string(index=False))


if __name__ == "__main__":
    main()
