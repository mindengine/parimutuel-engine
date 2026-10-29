"""Build frontend/bc_explorer.html: the Breeders' Cup cards (v3 data + Grant's grades) and the latest scorecard.

usage: python tools/build_bc_explorer.py DB SCORE_DATE V3_CSV... [--out frontend/bc_explorer.html]

  DB          sqlite DB holding Grant's grades (cards source grant_csv / grant_docx) and Brisnet results
  SCORE_DATE  race date to show in the scorecard section (e.g. 2026-10-28)
  V3_CSV      bc_v3_KEE_YYYYMMDD.csv files (tools/distill_v3.py + tools/ultimate_pp_overlay.py)

Self-contained output: data is embedded, so the file can be served from any web server.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "frontend" / "bc_explorer_template.html"
BC_NAMES = {"BCJvTfSp": "Juvenile Turf Sprint", "BCJuvFil": "Juvenile Fillies", "BCJvFlTf": "Juvenile Fillies Turf",
            "BCJuvnle": "Juvenile", "BCJuvTrf": "Juvenile Turf", "BCFMSprt": "Filly & Mare Sprint",
            "BCTrfSpt": "Turf Sprint", "BCDirtMi": "Dirt Mile", "BCFMTrf": "Filly & Mare Turf", "BCSprint": "Sprint",
            "BCMile": "Mile", "BCDistaf": "Distaff", "BCTurf": "Turf", "BCClasic": "Classic"}
ORDER = ["A+", "A", "B", "C", "D", "F/F-"]


def num(x):
    try:
        v = float(str(x).replace("%", "").strip())
        return None if v != v else v
    except ValueError:
        return None


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def pace(q):
    return None if q is None else "LONE_SPEED" if q <= 15 else "MELTDOWN" if q >= 30 else "HONEST"


def bc_cards(con, paths):
    races, runners, names = [], [], {}
    for p in paths:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        date = df.Date.iloc[0]
        iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        key = f"KEE_{date}"
        names[key] = f"Breeders' Cup {'Friday' if date.endswith('30') else 'Saturday'} · Keeneland {int(date[4:6])}/{int(date[6:])}"
        cid = f"KEE-{iso}-grant"
        gr = {(int(r["race"]), str(r["pgm"]).upper()): dict(r) for r in con.execute(
            "SELECT race, pgm, bucket, modifiers, tags, reason, scratched FROM pre_race_grades WHERE card_id=?", (cid,))}
        grace = {r["race"]: dict(r) for r in con.execute("SELECT race, qsp_sum, pace_scenario FROM races WHERE card_id=?", (cid,))}
        for race, g in df.groupby(df.Race.astype(int)):
            rid = f"{date}_KEE_{race:02d}"
            first = g.iloc[0]
            cls = first.Today_Class
            m = re.match(r"(BC[A-Za-z]+)-G1", cls)
            qsp = sum(num(x) or 0 for x in g.QSP)
            gi = grace.get(race, {})
            if "Status" in g:
                g = g.copy()
            act = g[~g.Status.isin(["SCRATCHED", "AE_OUT"])] if "Status" in g else g
            qsp = sum(num(x) or 0 for x in act.QSP)
            pp = {r.Prog: num(r.Prime_Power) for r in g.itertuples()}
            ranked = sorted([num(x) for x in act.Prime_Power if num(x)], reverse=True)
            par = num(first.Par_Spd)
            races.append({"race_id": rid, "track_code": "KEE", "race_date": int(date), "race_num": race,
                          "today_dist_yds": num(first.Today_Dist_Yds), "today_surf": first.Today_Surf, "today_class": cls,
                          "today_purse": num(first.Today_Purse), "active_starters": len(act),
                          "active_qsp_sum": int(qsp),
                          "pace_scenario": pace(qsp), "par_spd": par,
                          "name": f"BC {BC_NAMES[m.group(1)]}" if m and m.group(1) in BC_NAMES else None, "settled": False})
            for r in g.itertuples():
                k = (race, r.Prog.strip().upper())
                G = gr.get(k, {})
                tags = (G.get("tags") or "").split(",")
                mods = (G.get("modifiers") or "").split(",")
                fig = num(r.Last_Fig)
                last = []
                if r.Last_Fin:
                    last.append(ordinal(float(r.Last_Fin)) + (f", btn {r.Last_Btn_Len}" if r.Last_Btn_Len not in ("", "0.0") else ""))
                last += [x for x in (r.Last_Track if r.Last_Track not in ("N/A", "FTS") else "", r.Last_Class_Raw) if x]
                qp = lambda s: " | ".join(s.split(" | ")[:2])
                runners.append({
                    "race_id": rid, "prog_num": r.Prog, "horse_name": r.Horse.title(),
                    "is_scratched": int(bool(G.get("scratched")) or getattr(r, "Status", "RUNNING") in ("SCRATCHED", "AE_OUT")), "is_fts": int(r.Run_Style == "FTS"),
                    "ml_odds": num(r.ML_Odds), "run_style": None if r.Run_Style in ("N/A", "FTS", "NA", "") else r.Run_Style,
                    "qsp": num(r.QSP), "prime_power": pp[r.Prog], "pp_rank": ranked.index(pp[r.Prog]) + 1 if pp[r.Prog] and pp[r.Prog] in ranked and r.Prog in set(act.Prog) else None,
                    "last_fig_delta": fig - par if fig is not None and par else None,
                    "bris_class": num(r.Bris_Current_Class), "form_sig": r.Form_Sig, "last_out": " · ".join(last),
                    "trip_note": r.Last_Intl_Trip_Note or None, "qp_pos": qp(r.QP_Positive) or None, "qp_neg": qp(r.QP_Negative) or None,
                    "primary_grade": G.get("bucket"), "grade_reasons": G.get("reason"),
                    "tag_w": "W" in mods, "tag_e": "E" in mods, "tag_retail_trap": "RETAIL_TRAP" in tags,
                    "tag_unseen_retail": "UNSEEN" in tags, "tag_dint": "D-INT" in tags, "tag_ddom": "D-DOM" in tags})
    return races, runners, names


def scorecard(con, date):
    d = pd.read_sql_query(
        """SELECT c.track, g.race, g.pgm, g.bucket, g.modifiers, g.tags, o.finish_pos, o.final_odds
           FROM pre_race_grades g JOIN cards c USING(card_id)
           JOIN post_race_official o ON o.track=c.track AND o.race_date=c.race_date AND o.race=g.race AND o.pgm=g.pgm
           WHERE c.race_date=? AND c.source IN ('grant_csv','grant_docx') AND g.scratched=0 AND o.scratched=0
             AND o.finish_pos IS NOT NULL AND g.bucket IS NOT NULL""", con, params=(date,))
    o = pd.read_sql_query("SELECT track, race, pgm, final_odds FROM post_race_official WHERE race_date=? AND scratched=0",
                          con, params=(date,))
    o["p"] = 1 / (o.final_odds + 1)
    o["p"] = o.p / o.groupby(["track", "race"]).p.transform("sum")
    d = d.merge(o[["track", "race", "pgm", "p"]], on=["track", "race", "pgm"], how="left")
    d["win"] = d.finish_pos == 1
    d["top3"] = d.finish_pos <= 3
    d["ret"] = d.win * (d.final_odds.fillna(0) + 1) * 2
    d["grp"] = d.bucket.map(lambda b: "F/F-" if b in ("F", "F-") else b)
    d["tags"] = d.tags.fillna("")
    d["modifiers"] = d.modifiers.fillna("")

    def row(x, **kw):
        n = len(x)
        return {**kw, "n": n, "wins": int(x.win.sum()), "win": 100 * x.win.mean() if n else None,
                "top3": 100 * x.top3.mean() if n else None, "roi": 100 * (x.ret.sum() / (2 * n) - 1) if n else None,
                "exp": float(x.p.sum())}
    grades = [row(d[d.grp == g], g=g) for g in ORDER if (d.grp == g).any()]
    tags = [row(d[d.tags.str.contains("RETAIL_TRAP")], label="Retail trap fades"),
            row(d[d.tags.str.contains("UNSEEN")], label="Unseen by retail"),
            row(d[(d.bucket == "A") & d.modifiers.str.contains("W")], label="A[W] wise guys"),
            row(d[(d.bucket == "A") & ~d.modifiers.str.contains("W")], label="A without [W]")]
    winners = d[d.win]
    note = (f"{int(winners.grp.isin(['A+','A']).sum())} of {len(winners)} winners were graded A+ or A, and "
            f"{int(winners.grp.isin(['A+','A','B']).sum())} were A+ through B. The grades sort the field in the right order, but "
            "on this day A+ and A won about as often as their odds predicted (the vs-odds column), so the crowd mostly agreed. "
            "B was the strongest group against the odds. One day is far too small a sample to set betting multipliers; "
            "every new card scored adds to this.")
    return {"tracks": sorted(d.track.unique().tolist()), "races": int(d.groupby(["track", "race"]).ngroups),
            "starters": len(d), "grades": grades, "tags": tags, "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db"); ap.add_argument("score_date"); ap.add_argument("v3", nargs="+")
    ap.add_argument("--out", default=str(ROOT / "frontend" / "bc_explorer.html"))
    a = ap.parse_args()
    con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row
    races, runners, names = bc_cards(con, a.v3)
    data = {"races": races, "runners": runners, "cardNames": names, "score": scorecard(con, a.score_date),
            "builtAt": datetime.now().strftime("%b %d, %Y %H:%M")}
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    Path(a.out).write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))
    print(f"wrote {a.out}: {len(races)} races, {len(runners)} runners, "
          f"{sum(1 for r in runners if r['primary_grade'])} graded")


if __name__ == "__main__":
    main()
