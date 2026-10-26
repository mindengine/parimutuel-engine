"""v2 command line:  python -m pe.v2 <command> ...

  ingest <distilled.csv> [--extras card.json]        stages 1-3 (scratches, herd radar, grade overrides in extras)
  slips  <slips.json>                               log disjoint ADW slips (fails on any overlap)
  settle --track MVR --date 20261026 [--results r.csv] [--pools p.csv]   stage 4
  report                                            print the three diagnostic views
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings

import pandas as pd

from . import etl
from .pools import OverlapError

warnings.filterwarnings("ignore", category=FutureWarning)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pe.v2")
    ap.add_argument("--db", default=str(etl.DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("file"); p.add_argument("--extras")
    p = sub.add_parser("slips"); p.add_argument("file")
    p = sub.add_parser("settle"); p.add_argument("--track", required=True); p.add_argument("--date", required=True)
    p.add_argument("--results"); p.add_argument("--pools")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    con = etl.connect(a.db)

    if a.cmd == "ingest":
        ids = etl.ingest_card(con, a.file, a.extras)
        print(f"loaded {len(ids)} races: {ids[0]} .. {ids[-1]}")
    elif a.cmd == "slips":
        # {"track","date","first_race","pool_type","takeout","slips":[{"slip_tier","matrix","base"}]}
        d = json.load(open(a.file))
        pid = etl.ensure_pool(con, d["track"], d["date"], d["first_race"], d["pool_type"], d.get("takeout", 0.0),
                              d.get("carryover", 0.0))
        try:
            rows = etl.log_slips(con, pid, d["slips"])
        except OverlapError as e:
            print(e); sys.exit(1)
        for r in rows:
            print(f"{r['ticket_id']:32} {r['matrix_definition']:40} {r['unique_combos']:>5} x ${r['base_wager']:.2f}"
                  f" = ${r['ticket_cost']:.2f}")
        print(f"total ${sum(r['ticket_cost'] for r in rows):.2f}")
    elif a.cmd == "settle":
        print(etl.settle_card(con, a.track, a.date, a.results, a.pools))
    elif a.cmd == "report":
        pd.set_option("display.width", 220, "display.max_columns", 30)
        for title, v, order in [("A. Grade & modifier calibration", "v_grade_calibration", "flat_win_roi DESC"),
                                ("B. Pool efficiency & tier yield", "v_pool_efficiency",
                                 "pool_type, theoretical_tier_roi DESC"),
                                ("C. Parameter leaks", "v_parameter_leaks", "sample_size DESC")]:
            print(f"\n== {title} ==")
            print(pd.read_sql_query(f"SELECT * FROM {v} ORDER BY {order}", con).to_string(index=False))


if __name__ == "__main__":
    main()
