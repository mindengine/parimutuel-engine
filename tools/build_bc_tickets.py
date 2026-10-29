"""Build frontend/bc_tickets.html: the ticket builder, preloaded with the Breeders' Cup cards.

usage: python tools/build_bc_tickets.py DB V3_CSV... [--out frontend/bc_tickets.html]

Cards with Grant's grades in DB (card_id KEE-YYYY-MM-DD-grant) come preloaded graded; the
others come with the field only, ready for Grant to load his CSV in the page.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_bc_explorer import BC_NAMES, num  # noqa: E402

TEMPLATE = ROOT / "frontend" / "bc_tickets_template.html"


def build(con, paths):
    cards = {}
    for p in paths:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        date = df.Date.iloc[0]
        iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        grades = {(int(r["race"]), str(r["pgm"]).upper()): dict(r) for r in con.execute(
            "SELECT race, pgm, bucket, modifiers, tags, scratched FROM pre_race_grades WHERE card_id=?",
            (f"KEE-{iso}-grant",))}
        races = []
        for race, g in df.groupby(df.Race.astype(int)):
            first = g.iloc[0]
            m = re.match(r"(BC[A-Za-z]+)-G1", first.Today_Class)
            yds = num(first.Today_Dist_Yds)
            surf = {"T": "turf", "D": "dirt"}.get(first.Today_Surf, first.Today_Surf)
            purse = num(first.Today_Purse)
            horses = []
            for r in g.itertuples():   # Status (from tools/apply_changes.py) marks scratches and AEs not drawn in
                G = grades.get((race, r.Prog.strip().upper()), {})
                tags = (G.get("tags") or "").split(",")
                mods = (G.get("modifiers") or "").split(",")
                sub = next((t for t in tags if t in ("D-INT", "D-DOM")), None)
                horses.append({"pgm": r.Prog.strip().upper(), "name": r.Horse.title(), "ml": num(r.ML_Odds),
                               "grade": G.get("bucket"), "sub": sub, "w": "W" in mods, "e": "E" in mods,
                               "trap": "RETAIL_TRAP" in tags, "unseen": "UNSEEN" in tags,
                               "scr": int(bool(G.get("scratched")) or getattr(r, "Status", "RUNNING") in ("SCRATCHED", "AE_OUT"))})
            races.append({"race": race,
                          "title": f"BC {BC_NAMES[m.group(1)]}" if m and m.group(1) in BC_NAMES else first.Today_Class,
                          "info": (f"{yds / 220:g}f {surf} · ${purse:,.0f} · " + (f"{(~g.Status.isin(['SCRATCHED', 'AE_OUT'])).sum()} running" if "Status" in g else f"{len(g)} entered")) if yds and purse else "",
                          "horses": horses})
        day = "Friday" if date.endswith("30") else "Saturday"
        graded = any(h["grade"] for r in races for h in r["horses"])
        cards[f"KEE_{date}"] = {"name": f"Breeders' Cup {day} · Keeneland {int(date[4:6])}/{int(date[6:])}"
                                        + ("" if graded else " (load grades)"), "races": races}
    return cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db"); ap.add_argument("v3", nargs="+")
    ap.add_argument("--out", default=str(ROOT / "frontend" / "bc_tickets.html"))
    a = ap.parse_args()
    con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row
    data = {"cards": build(con, a.v3), "builtAt": datetime.now().strftime("%b %d, %Y %H:%M")}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    Path(a.out).write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))
    print("wrote", a.out, {k: sum(len(r["horses"]) for r in c["races"]) for k, c in data["cards"].items()})


if __name__ == "__main__":
    main()
