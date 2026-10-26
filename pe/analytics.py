"""Module 2: post-race forensics.

* bucket_hit_rates  - empirical W/P/S by bucket x pace scenario (x field size band)
* grade_tickets     - did each proposed ticket hit, and what did it return
* lott_audit        - Left-on-the-Table: payouts the grades covered but tickets missed
* sequence_signatures - bucket makeup of winning horizontal sequences
"""
from __future__ import annotations

import json
from collections import Counter

import pandas as pd

NON_F = {"A+", "A", "B", "C", "D"}


def _df(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def field_band(n):
    if n is None or pd.isna(n):
        return "unknown"
    n = int(n)
    return "<=6" if n <= 6 else "7-9" if n <= 9 else "10+"


def bucket_hit_rates(con, by_field_size: bool = False) -> pd.DataFrame:
    df = _df(con, "SELECT * FROM v_graded_results WHERE finish_pos IS NOT NULL AND bucket IS NOT NULL")
    if df.empty:
        return df
    df["pace_scenario"] = df["pace_scenario"].fillna("UNKNOWN")
    keys = ["bucket", "pace_scenario"]
    if by_field_size:
        df["field"] = df["field_size"].map(field_band)
        keys.append("field")
    g = df.groupby(keys)
    out = g.agg(starters=("pgm", "size"),
                wins=("finish_pos", lambda s: (s == 1).sum()),
                win_pct=("finish_pos", lambda s: round((s == 1).mean(), 3)),
                place_pct=("finish_pos", lambda s: round((s <= 2).mean(), 3)),
                show_pct=("finish_pos", lambda s: round((s <= 3).mean(), 3)),
                avg_final_odds=("final_odds", "mean")).reset_index()
    # $2 win ROI, using final odds as a payout proxy (exact win payouts can replace this)
    df["win_return"] = (df["finish_pos"] == 1) * (df["final_odds"].fillna(0) + 1) * 2
    roi = df.groupby(keys)["win_return"].agg(lambda s: round(s.sum() / (2 * len(s)) - 1, 3))
    out = out.merge(roi.rename("flat_win_roi").reset_index(), on=keys)
    order = {b: i for i, b in enumerate(["A+", "A", "B", "C", "D", "F-", "F"])}
    return out.sort_values(keys, key=lambda c: c.map(order) if c.name == "bucket" else c)


def _winners(con, track, date, races):
    rows = con.execute(
        f"SELECT race, pgm FROM post_race_official WHERE track=? AND race_date=? AND finish_pos=1 "
        f"AND race IN ({','.join('?' * len(races))})", (track, date, *races)).fetchall()
    w = {r["race"]: r["pgm"] for r in rows}
    return [w.get(r) for r in races]


def _order(con, track, date, race, n):
    rows = con.execute("SELECT pgm FROM post_race_official WHERE track=? AND race_date=? AND race=? "
                       "AND finish_pos IS NOT NULL ORDER BY finish_pos LIMIT ?",
                       (track, date, race, n)).fetchall()
    return [r["pgm"] for r in rows]


def _horizontal_payout(con, track, date, close_race, pool):
    r = con.execute("SELECT winning_combo, base, payout FROM payouts WHERE track=? AND race_date=? AND race=? "
                    "AND pool=? ORDER BY payout DESC LIMIT 1", (track, date, close_race, pool)).fetchone()
    return (r["winning_combo"], r["base"], r["payout"]) if r else (None, None, None)


def _payout_for(con, track, date, close_race, pool, combo):
    r = con.execute("SELECT base, payout FROM payouts WHERE track=? AND race_date=? AND race=? AND pool=? "
                    "AND winning_combo=?", (track, date, close_race, pool, combo)).fetchone()
    return (r["base"], r["payout"]) if r else (None, None)


def grade_tickets(con) -> pd.DataFrame:
    from .tickets import HORIZONTAL_POOLS, VERTICAL_POOLS
    t = _df(con, "SELECT t.*, c.track, c.race_date FROM tickets t JOIN cards c USING(card_id) "
                 "WHERE c.race_date IS NOT NULL")
    out = []
    for _, r in t.iterrows():
        sel = json.loads(r["selections"])
        pool = r["pool"]
        if pool in HORIZONTAL_POOLS:
            close = r["race"] + len(sel) - 1
            combo, pay_base, pay = _horizontal_payout(con, r["track"], r["race_date"], close, pool)
            pool_offered = con.execute("SELECT 1 FROM payouts WHERE track=? AND race_date=? AND pool=? LIMIT 1",
                                       (r["track"], r["race_date"], pool)).fetchone()
            if combo is None and pool_offered:
                # Payouts for this pool are loaded, but none closes here: the track
                # didn't offer this sequence, so the ticket could not have been bet.
                out.append(dict(card_id=r["card_id"], ticket_id=r["ticket_id"], label=r["label"], pool=pool,
                                race=r["race"], cost=r["cost"], result="(sequence not offered)", hit=None,
                                returned=0.0, net=None))
                continue
            if combo is None:   # no official payout loaded: fall back to race winners
                res = _winners(con, r["track"], r["race_date"], list(range(r["race"], close + 1)))
                combo = "-".join(x or "?" for x in res)
                pay_base = pay = None
            legs = [l.split("/") for l in combo.split("-")]
            hit = None if "?" in combo or len(legs) != len(sel) else \
                all(any(w in sel[i] for w in legs[i]) for i in range(len(sel)))
            ret = (pay / pay_base * r["base"]) if (hit and pay) else 0.0
            out.append(dict(card_id=r["card_id"], ticket_id=r["ticket_id"], label=r["label"], pool=pool,
                            race=r["race"], cost=r["cost"], result=combo, hit=hit, returned=round(ret, 2),
                            net=round(ret - r["cost"], 2) if hit is not None else None))
            continue
        elif pool in VERTICAL_POOLS:
            n = VERTICAL_POOLS[pool]
            res = _order(con, r["track"], r["race_date"], r["race"], n)
            close = r["race"]
            combo = "-".join(res)
            hit = None if len(res) < n else all(res[i] in sel[i] for i in range(n))
        else:
            continue
        pay_base, pay = _payout_for(con, r["track"], r["race_date"], close, pool, combo)
        ret = (pay / pay_base * r["base"]) if (hit and pay) else 0.0
        out.append(dict(card_id=r["card_id"], ticket_id=r["ticket_id"], label=r["label"], pool=pool,
                        race=r["race"], cost=r["cost"], result=combo, hit=hit,
                        returned=round(ret, 2), net=round(ret - r["cost"], 2) if hit is not None else None))
    return pd.DataFrame(out)


def lott_audit(con) -> pd.DataFrame:
    """For every official payout: were all winning runners graded non-F, and did any ticket hit?

    'system_covered' = every runner in the winning combination sits in A+/A/B/C/D.
    'ticket_hit'     = at least one proposed ticket on this card cashed it.
    Rows with system_covered & not ticket_hit are the money left on the table.
    """
    from .tickets import HORIZONTAL_POOLS
    pays = _df(con, "SELECT p.*, c.card_id FROM payouts p JOIN cards c "
                    "ON c.track=p.track AND c.race_date=p.race_date")
    if pays.empty:
        return pays
    grades = {(r["card_id"], r["race"], r["pgm"]): r["bucket"]
              for r in con.execute("SELECT card_id, race, pgm, bucket FROM pre_race_grades")}
    graded_ticks = grade_tickets(con)
    race_winners = {(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT track, race_date, race, pgm FROM post_race_official WHERE finish_pos = 1")}
    rows = []
    for _, p in pays.iterrows():
        pool = p["pool"]
        legs = [l.split("/") for l in p["winning_combo"].split("-")]
        if pool in HORIZONTAL_POOLS:
            races = list(range(p["race"] - len(legs) + 1, p["race"] + 1))
        else:
            races = [p["race"]] * len(legs)
        # Judge each horizontal leg on the horse(s) that officially won; the payout's extra
        # credited numbers are often scratch substitutions, not finishers.
        if pool in HORIZONTAL_POOLS:
            legs = [[pg for pg in leg if (p["track"], p["race_date"], rc, pg.upper()) in race_winners] or leg
                    for rc, leg in zip(races, legs)]
        leg_buckets = [[grades.get((p["card_id"], rc, pg.upper())) for pg in leg] for rc, leg in zip(races, legs)]
        def best(bs):
            order = ["A+", "A", "B", "C", "D", "F-", "F"]
            known = [b for b in bs if b]
            return min(known, key=order.index) if known else None
        buckets = [best(bs) for bs in leg_buckets]
        covered = all(b in NON_F for b in buckets)
        hit = False
        if not graded_ticks.empty:
            m = graded_ticks[(graded_ticks.card_id == p["card_id"]) & (graded_ticks.pool == pool)
                             & (graded_ticks.result == p["winning_combo"]) & (graded_ticks.hit == True)]
            hit = not m.empty
        rows.append(dict(card_id=p["card_id"], pool=pool, close_race=p["race"],
                         winning_combo=p["winning_combo"], buckets="/".join(b or "?" for b in buckets),
                         payout=p["payout"], base=p["base"], system_covered=covered, ticket_hit=hit,
                         left_on_table=covered and not hit))
    return pd.DataFrame(rows).sort_values("payout", ascending=False)


def sequence_signatures(con, min_payout: float = 0.0) -> pd.DataFrame:
    df = lott_audit(con)
    if df.empty:
        return df
    df = df[df.pool.isin(["P4", "P5", "P6"]) & (df.payout >= min_payout)].copy()

    def sig(b):
        c = Counter("A" if x in ("A+", "A") else "C/D" if x in ("C", "D") else x for x in b.split("/"))
        return " + ".join(f"{n}{k}" for k, n in sorted(c.items()))
    df["signature"] = df["buckets"].map(sig)
    return (df.groupby(["pool", "signature"])
              .agg(sequences=("payout", "size"), median_payout=("payout", "median"),
                   max_payout=("payout", "max"), captured=("ticket_hit", "sum"))
              .reset_index().sort_values("sequences", ascending=False))
