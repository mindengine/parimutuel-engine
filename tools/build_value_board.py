"""Build frontend/value_board.html: paste live odds, find value against Grant's grades.

usage: python tools/build_value_board.py DB SCORE_DATE V3_CSV... [--out frontend/value_board.html]

Cards come from tools/build_bc_tickets.py (Grant's grades + morning lines). The "Week measured"
multipliers are actual wins / odds-expected wins per grade over the SCORE_DATE list (comma-separated).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_bc_tickets import build  # noqa: E402


SHRINK = 5.0   # pull small groups toward 1.0: (wins + 5) / (expected + 5)


def measured(con, dates):
    """Actual wins / odds-expected wins per grade (and grade[W]) over the given race dates."""
    dates = [dates] if isinstance(dates, str) else list(dates)
    ph = ",".join("?" * len(dates))
    d = pd.read_sql_query(
        f"""SELECT g.bucket, g.modifiers, o.finish_pos, o.final_odds, c.track, c.race_date, g.race, g.pgm
           FROM pre_race_grades g JOIN cards c USING(card_id)
           JOIN post_race_official o ON o.track=c.track AND o.race_date=c.race_date AND o.race=g.race AND o.pgm=g.pgm
           WHERE c.race_date IN ({ph}) AND c.card_id LIKE '%-grant' AND g.scratched=0 AND o.scratched=0
             AND o.finish_pos IS NOT NULL AND g.bucket IS NOT NULL""", con, params=dates)
    o = pd.read_sql_query(f"SELECT track, race_date, race, pgm, final_odds FROM post_race_official "
                          f"WHERE race_date IN ({ph}) AND scratched=0 AND finish_pos IS NOT NULL", con, params=dates)
    o["p"] = 1 / (o.final_odds + 1)
    o["p"] = o.p / o.groupby(["track", "race_date", "race"]).p.transform("sum")
    d = d.merge(o[["track", "race_date", "race", "pgm", "p"]], on=["track", "race_date", "race", "pgm"])
    d["bucket"] = d.bucket.replace({"F-": "F"})
    d["key"] = d.bucket + d.modifiers.fillna("").str.contains("W").map({True: "[W]", False: ""})
    out = {}
    for k, g in d.groupby("key"):
        if g.p.sum() >= 1:                       # skip groups too small to say anything
            out[k] = round(float(((g.finish_pos == 1).sum() + SHRINK) / (g.p.sum() + SHRINK)), 2)
    for k, g in d.groupby("bucket"):
        if k in ("A+", "A", "B", "C", "D") and k not in out and g.p.sum() >= 1:
            out[k] = round(float(((g.finish_pos == 1).sum() + SHRINK) / (g.p.sum() + SHRINK)), 2)
    out["_n"] = int(len(d))
    out["_races"] = int(d.groupby(["track", "race_date", "race"]).ngroups)
    out["_days"] = int(d.groupby(["track", "race_date"]).ngroups)
    ds = sorted(d.race_date.unique())
    out["_span"] = f"{int(ds[0][5:7])}/{int(ds[0][8:])}\u2013{int(ds[-1][5:7])}/{int(ds[-1][8:])}" if ds else ""
    return out


def build_daily(con, paths):
    """Cards from Grant's daily picks_and_tags / master_merged CSVs, graded from the DB (card_id TRK-YYYY-MM-DD-grant)."""
    cards = {}
    for p in paths:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        trk, date = df.Track.iloc[0].strip().upper(), df.Date.iloc[0].strip()
        iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        pcol = "Prog" if "Prog" in df else "Pgm" if "Pgm" in df else "Post"
        grades = {(int(r["race"]), str(r["pgm"]).upper()): dict(r) for r in con.execute(
            "SELECT race, pgm, bucket, modifiers, tags, scratched FROM pre_race_grades WHERE card_id=?",
            (f"{trk}-{iso}-grant",))}
        scr = {(r[0], str(r[1]).upper()) for r in con.execute(
            "SELECT race, pgm FROM post_race_official WHERE track=? AND race_date=? AND scratched=1", (trk, iso))}
        races = []
        for race, g in df.groupby(df.Race.astype(int)):
            first = g.iloc[0]
            horses = []
            for r in g.to_dict("records"):
                pgm = str(r[pcol]).strip().upper()
                G = grades.get((race, pgm), {})
                tags = (G.get("tags") or "").split(",")
                mods = (G.get("modifiers") or "").split(",")
                horses.append({"pgm": pgm, "name": str(r["Horse"]).title(), "ml": num(r.get("ML_Odds", "")),
                               "grade": G.get("bucket"), "sub": next((t for t in tags if t in ("D-INT", "D-DOM")), None),
                               "w": "W" in mods, "e": "E" in mods, "trap": "RETAIL_TRAP" in tags,
                               "unseen": "UNSEEN" in tags,
                               "scr": int(bool(G.get("scratched")) or (race, pgm) in scr or str(r.get("Grade", "")).upper() == "SCR")})
            yds = num(first.get("Today_Dist_Yds", ""))
            surf = {"T": "turf", "D": "dirt"}.get(first.get("Today_Surf", ""), first.get("Today_Surf", ""))
            info = f"{yds / 220:g}f {surf} · {len(g)} entered" if yds else f"{len(g)} entered"
            races.append({"race": race, "title": first.get("Today_Class", ""), "info": info, "horses": horses})
        cards[f"{trk}_{date}"] = {"name": f"{trk} · {int(date[4:6])}/{int(date[6:])}", "races": races}
    return cards


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db"); ap.add_argument("score_date", help="race date(s) to measure, comma-separated"); ap.add_argument("v3", nargs="*")
    ap.add_argument("--daily", nargs="*", default=[], help="Grant's daily master_merged CSVs to show as cards")
    ap.add_argument("--out", default=str(ROOT / "frontend" / "value_board.html"))
    a = ap.parse_args()
    con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row
    cards = build_daily(con, a.daily) if a.daily else {}
    cards.update(build(con, a.v3) if a.v3 else {})
    data = {"cards": cards, "measured": measured(con, a.score_date.split(",")),
            "builtAt": datetime.now().strftime("%b %d, %Y %H:%M")}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "frontend" / "value_board_template.html").read_text()
    Path(a.out).write_text(tpl.replace("/*__DATA__*/null", blob))
    print("wrote", a.out, "measured:", data["measured"])


if __name__ == "__main__":
    main()
