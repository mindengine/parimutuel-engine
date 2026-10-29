"""Score Grant's grades for one race date against Brisnet chart results.

usage: python tools/score_grades.py DB DATE CHARTS_ZIP... [--out runners.csv]

Joins every graded runner to the official finish (program number), checks the horse
name against the chart so a post/program mix-up can't score the wrong horse, and prints
win/top-3/ROI by grade, by tag, and by track.
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pe.brisnet import read_zip  # noqa: E402


def key(s):
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"[\(\[][A-Z]{2,4}[\)\]]", "", str(s).upper()))


def chart_names(zips):
    names = {}
    for z in zips:
        for r in read_zip(z).get("start", []):
            names[(r[0].strip().upper(), int(r[2]), r[8].strip().upper())] = r[4].strip()
            names[(r[0].strip().upper(), int(r[2]), "NAME:" + key(r[4]))] = r[8].strip().upper()
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db"); ap.add_argument("date"); ap.add_argument("zips", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    names = chart_names(a.zips)
    g = pd.read_sql_query(
        """select c.track, g.race, g.pgm, g.horse_name, g.bucket, g.modifiers, g.tags, g.ml_odds
           from pre_race_grades g join cards c using(card_id)
           where c.race_date=? and c.source in ('grant_csv','grant_docx') and g.scratched=0""", con, params=(a.date,))
    o = pd.read_sql_query("select track, race, pgm, finish_pos, final_odds, scratched from post_race_official "
                          "where race_date=?", con, params=(a.date,))
    rows, issues = [], []
    for _, r in g.iterrows():
        pgm = str(r.pgm).upper()
        chart = names.get((r.track, r.race, pgm))
        if chart and key(chart) != key(r.horse_name):
            alt = names.get((r.track, r.race, "NAME:" + key(r.horse_name)))
            issues.append(f"{r.track} R{r.race} #{pgm} {r.horse_name}: chart has {chart} at #{pgm}"
                          + (f"; using #{alt}" if alt else "; dropped"))
            if not alt:
                continue
            pgm = alt
        m = o[(o.track == r.track) & (o.race == r.race) & (o.pgm == pgm)]
        if m.empty:
            issues.append(f"{r.track} R{r.race} #{pgm} {r.horse_name}: not in chart"); continue
        m = m.iloc[0]
        if m.scratched:
            continue                                   # late scratch: not a starter
        rows.append({**r.to_dict(), "pgm": pgm, "finish": m.finish_pos, "odds": m.final_odds})
    d = pd.DataFrame(rows)
    d["win"] = d.finish == 1
    d["top3"] = d.finish <= 3
    d["ret"] = d.win * (d.odds.fillna(0) + 1) * 2
    d["group"] = d.bucket.map(lambda b: "F/F-" if b in ("F", "F-") else b)

    def table(df, by):
        t = df.groupby(by).agg(starters=("win", "size"), wins=("win", "sum"), top3=("top3", "sum"),
                               ret=("ret", "sum")).reset_index()
        t["win%"] = (100 * t.wins / t.starters).round(1)
        t["top3%"] = (100 * t.top3 / t.starters).round(1)
        t["roi%"] = (100 * (t.ret / (2 * t.starters) - 1)).round(1)
        return t.drop(columns="ret")

    order = {"A+": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F/F-": 5}
    print("== by grade =="); print(table(d, "group").sort_values("group", key=lambda s: s.map(order)).to_string(index=False))
    for tag in ["RETAIL_TRAP", "UNSEEN"]:
        print(f"\n== tag {tag} =="); print(table(d[d.tags.str.contains(tag)], "group").to_string(index=False))
    d["W"] = d.modifiers.str.contains("W")
    print("\n== [W] horses =="); print(table(d[d.W], "group").to_string(index=False))
    print("\n== by track: winners' grades ==")
    print(d[d.win].groupby(["track", "group"]).size().unstack(fill_value=0).to_string())
    races = o[o.finish_pos == 1].groupby("track").race.nunique()
    print("\nraces with a result:", races.to_dict(), " graded winners:", int(d.win.sum()))
    print("\n" + "\n".join(issues) if issues else "\nno name/program mismatches")
    if a.out:
        d.to_csv(a.out, index=False)


if __name__ == "__main__":
    main()
