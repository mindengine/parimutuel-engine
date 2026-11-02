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
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db"); ap.add_argument("score_date", help="race date(s) to measure, comma-separated"); ap.add_argument("v3", nargs="+")
    ap.add_argument("--out", default=str(ROOT / "frontend" / "value_board.html"))
    a = ap.parse_args()
    con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row
    data = {"cards": build(con, a.v3), "measured": measured(con, a.score_date.split(",")),
            "builtAt": datetime.now().strftime("%b %d, %Y %H:%M")}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    tpl = (ROOT / "frontend" / "value_board_template.html").read_text()
    Path(a.out).write_text(tpl.replace("/*__DATA__*/null", blob))
    print("wrote", a.out, "measured:", data["measured"])


if __name__ == "__main__":
    main()
