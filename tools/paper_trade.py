"""Paper-trade Grant's betting scripts against the official Brisnet charts.

usage: python tools/paper_trade.py DB [--dates 2026-11-03,...] [--log paper_trade_log.csv]

Tickets are built by Grant's own generators (tools/grant/) from the graded horses that
actually ran (late scratches removed, as if the script were re-run after scratches), then
scored against the payouts table. Nothing is bet.

  SUP_v2   superfecta_wager_generator.export_disjointed_superfecta_csv (QSP 19+, 8+ starters, 10c)
  TRI_PB1  trifecta_wager_generator playbook 1: A / [W] runners / C+D (50c)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import os
import sqlite3
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "grant"))
import superfecta_wager_generator as sup  # noqa: E402
import trifecta_wager_generator as tri  # noqa: E402

POOL = {"SUPERFECTA": "SUP", "TRIFECTA": "TRI"}
BACK = {v: k for k, v in sup.TRACK_CODE_MAP.items() if k not in ("INJ", "IJN")}


def grades(con, dates):
    q = f"""SELECT c.track, c.race_date, g.race, g.pgm, g.bucket, g.ml_odds, g.modifiers, r.qsp_sum
            FROM pre_race_grades g JOIN cards c USING(card_id)
            JOIN races r ON r.card_id = g.card_id AND r.race = g.race
            JOIN post_race_official o ON o.track = c.track AND o.race_date = c.race_date
                 AND o.race = g.race AND o.pgm = g.pgm
            WHERE o.scratched = 0 AND g.bucket IS NOT NULL AND c.card_id LIKE '%-grant'
              AND c.race_date IN ({",".join("?" * len(dates))})"""
    g = pd.read_sql_query(q, con, params=dates)
    g["scratched"] = 0
    g["modifiers"] = g.modifiers.fillna("").map(lambda m: "".join(f"[{x}]" for x in m.split(",") if x))
    return g


def score(rows, label, pay):
    out = []
    for r in rows:
        d, t, race, pool = r[0], BACK.get(r[1], r[1]), int(r[2]), POOL[r[3]]
        legs = [set(x.strip("()").split(";")) for x in r[4].split("-")]
        cost = sum(1 for p in itertools.product(*legs) if len(set(p)) == len(p)) * float(r[5])
        if (t, d, race, pool) not in pay:
            continue
        combo, pbase, payout = pay[(t, d, race, pool)]
        win = [set(w.split("/")) for w in combo.split("-")]
        hit = len(win) == len(legs) and all(l & w for l, w in zip(legs, win))
        out.append({"script": label, "date": d, "track": t, "race": race, "cost": round(cost, 2),
                    "return": round(payout / pbase * float(r[5]), 2) if hit else 0.0, "hit": int(hit)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--dates", required=True)
    ap.add_argument("--log", default=None, help="CSV to append the day's ticket results to")
    a = ap.parse_args(argv)
    con = sqlite3.connect(a.db)
    dates = a.dates.split(",")
    g = grades(con, dates)
    p = pd.read_sql_query(f"SELECT * FROM payouts WHERE race_date IN ({','.join('?' * len(dates))})", con, params=dates)
    pay = {(r.track, r.race_date, r.race, r.pool): (r.winning_combo, r.base, r.payout) for r in p.itertuples()}
    tmp = os.path.join(tempfile.mkdtemp(), "t.csv")
    with contextlib.redirect_stdout(io.StringIO()):
        rows = score(sup.export_disjointed_superfecta_csv(g, tmp), "SUP_v2", pay)
        tr = []
        for t, d in g[["track", "race_date"]].drop_duplicates().values:
            tr += tri.export_trifecta_playbooks_csv(g, tmp, track=t, race_date=d, playbook="PLAYBOOK_1")
        rows += score(tr, "TRI_PB1", pay)
    df = pd.DataFrame(rows)
    if df.empty:
        print("no scorable tickets (are the charts loaded?)")
        return df
    s = df.groupby(["script", "date"]).agg(lines=("cost", "size"), hits=("hit", "sum"),
                                           cost=("cost", "sum"), returned=("return", "sum"))
    s["roi_%"] = (100 * (s.returned / s.cost - 1)).round(0)
    print(s.round(2).to_string())
    hits = df[df.hit == 1]
    if len(hits):
        print("\nhits:\n" + hits.to_string(index=False))
    if a.log:
        old = pd.read_csv(a.log) if os.path.exists(a.log) else pd.DataFrame()
        if len(old):
            old = old[~old.date.isin(dates)]
        pd.concat([old, df]).to_csv(a.log, index=False)
    return df


if __name__ == "__main__":
    main()
