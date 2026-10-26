"""Deterministic rules grader (first pass), built from the dossier's bucket definitions.

Input: a distilled card (Grant's distill.py output). Output: one grade per active
runner: bucket (A+ A B C D F- F) plus W/E modifiers, with the reasons recorded.

This is an approximation of Grant's LLM grading, not a copy of it. Every threshold
lives in RULES so it can be tuned against his real grades (never against results).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

RULES = {
    # Pace scenario from the Active (post-scratch) QSP sum.
    "lone_speed_max": 15,      # <= this: LONE_SPEED
    "meltdown_min": 30,        # >= this: MELTDOWN, else HONEST
    # A+ (card anchor)
    "aplus_pp_gap": 4.0,       # Prime Power lead over 2nd
    "aplus_ml_max": 4.0,       # anchors in the cards are all <= 4-1 ML (except one 6-1)
    # A
    "a_pp_rank_max": 2,
    # B
    "b_pp_rank_max": 5,
    "b_fig_rank_max": 3,
    # C (meltdown closers only)
    "c_lp_rank_max": 3,
    # D (smell test)
    "d_purse_drop": 0.75,      # Today_Purse < 0.75 * Last_Purse
    "d_rank_share": 0.75,      # D needs PP or fig rank in the top 75% of the field
    "d_fts_ml_min": 4.5,
    "d_fts_work_max_4f": 48.6, # 4f work this fast (or a bullet) counts as "elite"
    # F- (nuclear bet-against): ML <= 2.00 and all four gates
    "fminus_ml_max": 2.0,
    # Modifiers
    "e_e1_margin": 3,          # E1 lead needed for [E]
    "w_ml_min": 4.0,           # Grant's ledger: "Wise Guy A >= 4-1"
}

TROUBLE = re.compile(r"bump|stumbl|check|steadi|traffic|alter|block|shuffl|off slow|slow st|off step|"
                     r"bit slow|brsh|brush|wide|[4-9]w|[4-9]p|squeez|pinch|clip|lost|unprepared|bobbl|"
                     r"tight|crowd|hit gate", re.I)


def _num(v):
    try:
        x = float(str(v).strip())
        return x
    except ValueError:
        return None


@dataclass
class Grade:
    race: int
    pgm: str
    horse: str
    ml: float | None
    bucket: str
    mods: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def pace_scenario(qsp_sum: float, rules=RULES) -> str:
    if qsp_sum <= rules["lone_speed_max"]:
        return "LONE_SPEED"
    if qsp_sum >= rules["meltdown_min"]:
        return "MELTDOWN"
    return "HONEST"


def _rank(series: pd.Series, ascending=False) -> pd.Series:
    return series.rank(ascending=ascending, method="min", na_option="bottom")


def grade_race(df: pd.DataFrame, rules=RULES) -> tuple[list[Grade], dict]:
    """df: distilled rows for ONE race, active runners only."""
    d = df.copy()
    for c in ["ML_Odds", "QSP", "Prime_Power", "Par_Spd", "Par_LP", "Par_E1", "Last_E1", "Last_LP",
              "Last_Fig", "Fig_2Back", "Fig_3Back", "Today_Purse", "Last_Purse", "Work_1_Time"]:
        d[c + "_n"] = d[c].map(_num)
    d["QSP_n"] = d["QSP_n"].fillna(0)
    qsp_sum = d["QSP_n"].sum()
    scen = pace_scenario(qsp_sum, rules)
    par = float(d["Par_Spd_n"].dropna().iloc[0]) if d["Par_Spd_n"].notna().any() else None
    d["best2"] = d[["Last_Fig_n", "Fig_2Back_n"]].max(axis=1)
    d["best3"] = d[["Last_Fig_n", "Fig_2Back_n", "Fig_3Back_n"]].max(axis=1)
    d["pp_rank"] = _rank(d["Prime_Power_n"])
    d["fig_rank"] = _rank(d["best2"])
    d["lp_rank"] = _rank(d["Last_LP_n"])
    d["e1_rank"] = _rank(d["Last_E1_n"])
    pp_sorted = d["Prime_Power_n"].dropna().sort_values(ascending=False).tolist()
    pp_gap = (pp_sorted[0] - pp_sorted[1]) if len(pp_sorted) > 1 else 0
    e1_sorted = d["Last_E1_n"].dropna().sort_values(ascending=False).tolist()
    e1_gap = (e1_sorted[0] - e1_sorted[1]) if len(e1_sorted) > 1 else 0
    qsp_max = d["QSP_n"].max()
    lp_median = d["Last_LP_n"].median()
    n_measured = d["Last_LP_n"].notna().sum()

    grades = []
    for _, h in d.iterrows():
        why = []
        fts = h["Run_Style"] == "FTS"
        ml = h["ML_Odds_n"]
        # Grant grades relative to the field; par only gates A+ and F-, and is skipped when missing.
        near = lambda x, tol: (par is None or (x is not None and not pd.isna(x) and x >= par - tol))
        n = len(d)
        d_ok = h["pp_rank"] <= max(2, round(n * rules["d_rank_share"])) or h["fig_rank"] <= max(2, round(n / 2))

        # [E]: clear E1 leader who also owns the top QSP
        is_e = (not fts and h["e1_rank"] == 1 and e1_gap >= rules["e_e1_margin"] and h["QSP_n"] >= qsp_max
                and h["Run_Style"] in ("E", "E/P"))

        # D triggers
        triggers = []
        tp, lp = h["Today_Purse_n"], h["Last_Purse_n"]
        if tp and lp and tp < rules["d_purse_drop"] * lp:
            triggers.append(f"purse drop {tp/lp:.2f}x")
        if str(h["Last_Surface"]).upper() == "T" and str(h["Today_Surf"]).upper() == "D":
            triggers.append("turf-to-dirt")
        if TROUBLE.search(str(h["Last_Comment"])):
            triggers.append("trouble last out")

        if fts:
            # Law 4: unproven first-time starters are D or F only.
            wt = h["Work_1_Time_n"]
            bullet = wt is not None and wt < 0
            fast = wt is not None and str(h["Work_1_Dist"]) == "880" and abs(wt) <= rules["d_fts_work_max_4f"]
            if ml and ml >= rules["d_fts_ml_min"] and (bullet or fast):
                bucket = "D"; why.append("FTS with elite work at a price")
            else:
                bucket = "F"; why.append("FTS (Law 4)")
        else:
            pace_fit_closer = h["Run_Style"] in ("P", "S") and h["lp_rank"] <= rules["c_lp_rank_max"]
            # F- gates
            g1 = h["Run_Style"] == "E" and not is_e
            g2 = scen == "MELTDOWN" and h["e1_rank"] > 1
            g3 = lp_median is not None and h["Last_LP_n"] is not None and h["Last_LP_n"] < lp_median
            g4 = (par is not None and h["Last_Fig_n"] is not None and not pd.isna(h["Last_Fig_n"])
                  and h["Last_Fig_n"] < par
                  and not (tp and lp and tp < rules["d_purse_drop"] * lp))
            if ml is not None and ml <= rules["fminus_ml_max"] and g1 and g2 and g3 and g4:
                bucket = "F-"; why.append("ML fav, one-dimensional E, out-footed in meltdown, weak LP, sub-par fig")
            elif (h["pp_rank"] == 1 and pp_gap >= rules["aplus_pp_gap"] and near(h["best2"], 0)
                  and h["lp_rank"] <= max(3, -(-n // 4)) and (ml is None or ml <= rules["aplus_ml_max"])):
                bucket = "A+"; why.append(f"PP lead {pp_gap:.1f}, at/above par, top-3 LP")
            elif (((h["pp_rank"] <= rules["a_pp_rank_max"] and h["fig_rank"] <= 3) or h["fig_rank"] == 1
                   or (h["lp_rank"] == 1 and h["pp_rank"] <= 3))
                  and not (scen == "MELTDOWN" and h["Run_Style"] == "E" and not is_e)):
                bucket = "A"; why.append("top PP/fig and near par, fits pace")
            elif scen == "MELTDOWN" and pace_fit_closer:
                bucket = "C"; why.append("meltdown closer, top-3 LP")
            elif triggers and d_ok and h["pp_rank"] > 3 and h["fig_rank"] > 3:
                bucket = "D"; why.append("smell test: " + ", ".join(triggers))
            elif h["pp_rank"] <= rules["b_pp_rank_max"] or h["fig_rank"] <= rules["b_fig_rank_max"]:
                bucket = "B"; why.append("secondary PP and figs")
            elif triggers and d_ok:
                bucket = "D"; why.append("smell test: " + ", ".join(triggers))
            else:
                bucket = "F"; why.append("non-competitive on numbers")

        mods = []
        if is_e and bucket not in ("F", "F-"):
            mods.append("E")
        if ml is not None and ml >= rules["w_ml_min"] and bucket in ("A+", "A", "B", "C", "D"):
            mods.append("W")
        grades.append(Grade(int(h["Race"]), str(h["Prog"]).strip().upper(), h["Horse"], ml, bucket, mods, why))
    info = {"qsp_sum": int(qsp_sum), "pace_scenario": scen, "field_size": len(d),
            "par_spd": par, "par_lp": d["Par_LP_n"].dropna().iloc[0] if d["Par_LP_n"].notna().any() else None,
            "measured": int(n_measured)}
    return grades, info


def grade_card(distilled_csv: str, active: dict[int, set[str]] | None = None, rules=RULES):
    """active: {race: {pgm,...}} of runners that actually started (drops scratches)."""
    df = pd.read_csv(distilled_csv, dtype=str, keep_default_na=False)
    out, races = [], {}
    for race, g in df.groupby(df["Race"].astype(int)):
        if active is not None:
            keep = active.get(race)
            if not keep:
                continue
            g = g[g["Prog"].str.strip().str.upper().isin(keep)]
        grades, info = grade_race(g, rules)
        out.extend(grades)
        races[race] = info
    return out, races, df


def load_graded_card(con, distilled_csv: str, track: str, race_date: str, rules=RULES,
                     source="rules_v1") -> str:
    """Grade every starter (scratches taken from official results) and store it."""
    rows = con.execute("SELECT race, pgm FROM post_race_official WHERE track=? AND race_date=? AND scratched=0",
                       (track, race_date)).fetchall()
    active: dict[int, set[str]] = {}
    for r in rows:
        active.setdefault(r[0], set()).add(str(r[1]).upper())
    grades, races, _ = grade_card(distilled_csv, active or None, rules)
    card_id = f"{track}-{race_date}-{source}"
    con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,source_file,notes) VALUES (?,?,?,?,?,?)",
                (card_id, track, race_date, source, distilled_csv, "deterministic rules grader"))
    for race, i in races.items():
        con.execute("INSERT INTO races(card_id,race,field_size,qsp_sum,pace_scenario,par_spd,par_lp,grades_complete)"
                    " VALUES (?,?,?,?,?,?,?,1)", (card_id, race, i["field_size"], i["qsp_sum"],
                                                   i["pace_scenario"], i["par_spd"], i["par_lp"]))
    for g in grades:
        con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,ml_odds,"
                    "grade_confidence,tags) VALUES (?,?,?,?,?,?,?,?,?)",
                    (card_id, g.race, g.pgm, g.horse, g.bucket, ",".join(sorted(g.mods)), g.ml, "rules",
                     "; ".join(g.reasons)))
    con.commit()
    return card_id
