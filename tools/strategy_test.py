"""Replay Grant's grades as mechanical tickets against official Brisnet payouts.

usage: python tools/strategy_test.py DB [--dates 2026-10-28,...] [--out results.csv]

Every ticket is built only from the grades (no hindsight) and only from horses that actually ran.
Payouts come from the chart files (payouts table), rescaled to the ticket's base amount.

  WIN_A        $2 win on every A+ and A
  SUPER_wide   $0.10 superfecta  A+/A  over  A+,A,B  over  A+,A,B,C  over  A+,A,B,C,D
  SUPER_tight  $0.10 superfecta  A+/A  over  A+,A,B  over  A+,A,B   over  A+,A,B,C
  TRI          $0.50 trifecta    A+/A  over  A+,A,B  over  A+,A,B,C
  P5_A         $0.50 Pick 5: each leg = the A+ alone if there is one, else all A's (B's if no A ran)
  P5_AB        $0.50 Pick 5: each leg = A+, A and B
  P4_A, P4_AB  the same for the Pick 4 ($0.50)
"""
from __future__ import annotations

import argparse
import itertools
import sqlite3
from collections import defaultdict

import pandas as pd

TOP = {"A+", "A"}
AB = {"A+", "A", "B"}
ABC = AB | {"C"}
ABCD = ABC | {"D"}


def load(con, dates):
    q = f"""SELECT c.track, c.race_date, g.race, g.pgm, g.bucket, o.finish_pos, o.final_odds
            FROM pre_race_grades g JOIN cards c USING(card_id)
            JOIN post_race_official o ON o.track=c.track AND o.race_date=c.race_date AND o.race=g.race AND o.pgm=g.pgm
            WHERE g.bucket IS NOT NULL AND o.scratched=0 AND o.finish_pos IS NOT NULL
              AND c.race_date IN ({",".join("?" * len(dates))})"""
    return pd.read_sql_query(q, con, params=dates)


def payouts(con, dates):
    q = f"SELECT * FROM payouts WHERE race_date IN ({','.join('?' * len(dates))})"
    p = pd.read_sql_query(q, con, params=dates)
    return {(r.track, r.race_date, r.race, r.pool): (r.winning_combo, r.base, r.payout) for r in p.itertuples()}


def vertical(sets, finish, n):
    """combos and whether the ticket hit, for an n-position vertical with position sets."""
    combos = sum(1 for t in itertools.product(*sets) if len(set(t)) == n)
    hit = len(finish) >= n and all(finish[i] in sets[i] for i in range(n))
    return combos, hit


def leg(race_horses, wide):
    g = race_horses
    if wide:
        s = {p for p, b in g.items() if b in AB}
    else:
        aplus = [p for p, b in g.items() if b == "A+"]
        s = set(aplus) if aplus else {p for p, b in g.items() if b == "A"}
    return s or {p for p, b in g.items() if b == "B"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--dates", default="2026-10-28,2026-10-29,2026-10-30,2026-10-31")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    con = sqlite3.connect(a.db)
    dates = a.dates.split(",")
    df = load(con, dates)
    pay = payouts(con, dates)

    races = {}
    for (t, d, r), g in df.groupby(["track", "race_date", "race"]):
        races[(t, d, r)] = {"grades": dict(zip(g.pgm, g.bucket)),
                            "finish": list(g.sort_values("finish_pos").pgm),
                            "odds": dict(zip(g.pgm, g.final_odds)), "fin": dict(zip(g.pgm, g.finish_pos))}

    rows = []

    def add(strategy, key, cost, ret, hit):
        rows.append({"strategy": strategy, "track": key[0], "date": key[1], "race": key[2],
                     "cost": round(cost, 2), "return": round(ret, 2), "hit": int(hit)})

    for key, R in races.items():
        g, fin = R["grades"], R["finish"]
        # graded-field check: the official top 4 must all be graded horses, or the race isn't comparable
        for p, b in g.items():
            if b in TOP:
                won = R["fin"][p] == 1
                add("WIN_A", key, 2.0, (R["odds"][p] + 1) * 2 if won else 0.0, won)
        top, ab = {p for p, b in g.items() if b in TOP}, {p for p, b in g.items() if b in AB}
        abc, abcd = {p for p, b in g.items() if b in ABC}, {p for p, b in g.items() if b in ABCD}
        if not top:
            continue
        for name, sets, base, pool, n in (("SUPER_wide", [top, ab, abc, abcd], 0.10, "SUP", 4),
                                          ("SUPER_tight", [top, ab, ab, abc], 0.10, "SUP", 4),
                                          ("TRI", [top, ab, abc], 0.50, "TRI", 3)):
            combos, hit = vertical(sets, fin, n)
            if combos == 0 or (key[0], key[1], key[2], pool) not in pay:
                continue
            combo, pbase, payout = pay[(key[0], key[1], key[2], pool)]
            ret = payout / pbase * base if hit and combo.split("-")[:n] == fin[:n] else 0.0
            add(name, key, combos * base, ret, ret > 0)

    # horizontals
    for (t, d, last, pool), (combo, pbase, payout) in pay.items():
        if pool not in ("P5", "P4"):
            continue
        n = 5 if pool == "P5" else 4
        keys = [(t, d, last - n + 1 + i) for i in range(n)]
        if not all(k in races for k in keys):
            continue
        winners = [races[k]["finish"][0] for k in keys]
        for wide, label in ((False, "A"), (True, "AB")):
            legs = [leg(races[k]["grades"], wide) for k in keys]
            combos = 1
            for s in legs:
                combos *= len(s)
            hit = all(w in s for w, s in zip(winners, legs))
            add(f"{pool}_{label}", (t, d, last), combos * 0.50, payout / pbase * 0.50 if hit else 0.0, hit)

    out = pd.DataFrame(rows)
    summ = out.groupby("strategy").agg(tickets=("cost", "size"), hits=("hit", "sum"), cost=("cost", "sum"),
                                       returned=("return", "sum"))
    summ["profit"] = summ.returned - summ.cost
    summ["roi_%"] = 100 * summ.profit / summ.cost
    summ["hit_%"] = 100 * summ.hits / summ.tickets
    print(summ.round(1).to_string())
    by_day = out.groupby(["strategy", "date"]).apply(lambda x: round(100 * (x["return"].sum() / x.cost.sum() - 1), 1),
                                                       include_groups=False).unstack()
    print("\nROI % by day:\n", by_day.to_string())
    if a.out:
        out.to_csv(a.out, index=False)
    return out, summ


if __name__ == "__main__":
    main()
