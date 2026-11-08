"""A++ flag: the A+ horses we expect to be true anchors. Rule frozen 2026-11-07 - do not tune it.

An A+ horse is A++ when ALL three hold (inputs from Grant's master_merged CSV, scratches removed):
  1. Last_LP >= Par_LP                       (closes at least as well as par)
  2. Days_Off <= 35                          (raced within the last 35 days)
  3. NOT (QSP >= 6 and the race's active QSP sum >= 20)   (not a speed horse in a hot-pace race)

In-sample (10/28-11/5, 52 A+ with inputs): A++ 14/14 top-3, other A+ 68%. Races from 11/06 on
were never looked at when the rule was chosen; those are the real test.

usage: python tools/a_plus_plus.py DB CSV [CSV ...] [--log a_plus_plus_log.csv]
"""
from __future__ import annotations

import argparse
import os
import sqlite3

import pandas as pd

FROZEN = "2026-11-07"
FIRST_TEST_DAY = "2026-11-06"


def num(s):
    return pd.to_numeric(s.astype(str).str.replace("%", ""), errors="coerce")


def flag(paths):
    out = []
    for p in paths:
        d = pd.read_csv(p, dtype=str, keep_default_na=False)
        if not {"Grade", "QSP", "Last_LP", "Par_LP", "Days_Off"} <= set(d.columns):
            continue                                   # not a graded master_merged file
        if "Prog" not in d:
            d["Prog"] = d["Pgm"] if "Pgm" in d else d["Post"]
        d = d[~d.Grade.str.upper().isin(["SCR", "QH", "QH_EXCL"])].copy()
        d["date"] = d.Date.str[:4] + "-" + d.Date.str[4:6] + "-" + d.Date.str[6:8]
        d["race"] = d.Race.astype(int)
        d["pgm"] = d.Prog.str.strip().str.upper()
        d["qsp"], d["lp"], d["par_lp"], d["days"] = num(d.QSP), num(d.Last_LP), num(d.Par_LP), num(d.Days_Off)
        d["race_qsp"] = d.groupby("race").qsp.transform("sum")
        a = d[d.Grade.str.strip() == "A+"].copy()
        a["rule_lp"] = a.lp >= a.par_lp
        a["rule_fresh"] = a.days <= 35
        a["rule_pace"] = ~((a.qsp >= 6) & (a.race_qsp >= 20))
        a["a_plus_plus"] = a.rule_lp & a.rule_fresh & a.rule_pace
        out.append(a[["Track", "date", "race", "pgm", "Horse", "ML_Odds", "lp", "par_lp", "days", "qsp", "race_qsp",
                      "rule_lp", "rule_fresh", "rule_pace", "a_plus_plus"]].rename(columns={"Track": "track"}))
    return pd.concat(out, ignore_index=True).drop_duplicates(["track", "date", "race", "pgm"], keep="last")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("csvs", nargs="+")
    ap.add_argument("--log", default=None)
    a = ap.parse_args(argv)
    f = flag(a.csvs)
    con = sqlite3.connect(a.db)
    o = pd.read_sql_query("SELECT track, race_date AS date, race, pgm, finish_pos, final_odds, scratched "
                          "FROM post_race_official", con)
    f = f.merge(o, on=["track", "date", "race", "pgm"], how="left")
    f["top3"] = f.finish_pos <= 3
    f["won"] = f.finish_pos == 1
    if a.log:
        old = pd.read_csv(a.log) if os.path.exists(a.log) else pd.DataFrame()
        keys = set(map(tuple, f[["track", "date", "race", "pgm"]].astype(str).values))
        if len(old):
            old = old[[tuple(map(str, k)) not in keys for k in old[["track", "date", "race", "pgm"]].values]]
        f = pd.concat([old, f], ignore_index=True)
        f.to_csv(a.log, index=False)
    ran = f[(f.scratched == 0) & f.finish_pos.notna()]
    for label, part in (("before the rule (in-sample)", ran[ran.date < FIRST_TEST_DAY]),
                        (f"since {FIRST_TEST_DAY} (real test)", ran[ran.date >= FIRST_TEST_DAY])):
        if part.empty:
            continue
        print(f"\n{label}:")
        for name, g in part.groupby(part.a_plus_plus.map({True: "A++", False: "other A+"})):
            print(f"  {name:8s} n={len(g):3d}  top3 {100 * g.top3.mean():4.0f}%  win {100 * g.won.mean():4.0f}%")
    today = f[f.finish_pos.isna() & f.a_plus_plus.astype(bool)]
    if len(today):
        print("\nA++ flags awaiting results:\n" + today[["track", "date", "race", "pgm", "Horse", "ML_Odds"]].to_string(index=False))
    return f


if __name__ == "__main__":
    main()
