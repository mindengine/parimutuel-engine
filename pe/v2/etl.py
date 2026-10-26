"""v2 ingestion pipeline: four stages per card (see docs/v2_spec.md §1).

    stage1_load      distilled CSV -> cleaned frame (ETL rules 1-3)
    stage2_scratch   mark scratches, then compute every race aggregate and rank on ACTIVE runners only
    stage3_grade     grades + tags (rules grader, or Grant's grades as an override), herd radar overlay
    stage4_settle    official results: CLV, WPS mutuels, pool capture, ticket settlement

`ingest_card()` runs 1-3 and writes races/runners. `settle_card()` runs 4.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from pe.grading import grade_race
from pe.v2 import pools as P

SCHEMA = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path("data/engine_v2.db")

# ETL rule 2 thresholds
MIN_JOCK_STS = 15
MIN_TRN_STS = 10
# ETL rule 3 floor
FIG_DELTA_FLOOR = -35.0
TROUBLE = re.compile(r"wide|[4-9]w|steadied|blocked|stumbled|bmp", re.I)  # spec regex, verbatim
PACE_LABEL = {"LONE_SPEED": "Lone_Speed", "HONEST": "Honest", "MELTDOWN": "Meltdown"}
HERD_COLS = ["drf_pick_rank", "beyer_edge_rank", "consensus_pts", "consensus_rank", "is_drf_best_bet",
             "is_live_longshot", "beyer_1", "beyer_2", "beyer_3", "eq_change"]


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text())
    return con


def race_id(date, track, race) -> str:
    return f"{int(date)}_{str(track).upper()}_{int(race):02d}"


def runner_id(date, track, race, prog) -> str:
    p = str(prog).strip().upper()
    num = re.match(r"\d+", p)
    pad = (num.group(0).zfill(2) + p[num.end():]) if num else p
    return f"{race_id(date, track, race)}_{pad}"


def _pct(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.rstrip("%").str.strip(), errors="coerce")


# ---- Stage 1 -------------------------------------------------------------------

def stage1_load(csv_path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df = raw.copy()
    num = ["Date", "Race", "Post", "ML_Odds", "Weight", "Today_Dist_Yds", "Today_Purse", "Jock_Sts", "Jock_Win",
           "Trn_Sts", "Trn_Win", "QSP", "Days_Off", "Prime_Power", "Par_E1", "Par_E2", "Par_LP", "Par_Spd",
           "Last_Date", "Last_Dist_Yds", "Last_Purse", "Last_E1", "Last_E2", "Last_LP", "Last_Fig",
           "Fig_2Back", "Fig_3Back", "Work_1_Date", "Work_1_Time", "Work_1_Dist"]
    for c in num:
        df[c] = pd.to_numeric(df[c].replace("", np.nan), errors="coerce")
    df["Prog"] = df["Prog"].str.strip().str.upper()
    df["jock_win_pct"] = _pct(raw["Jock_Win_Pct"])
    df["trn_win_pct"] = _pct(raw["Trn_Win_Pct"])
    df["ml_implied_prob"] = 1.0 / (df["ML_Odds"] + 1.0)

    # Rule 1: bullet works are stored as negative times.
    df["is_bullet_work"] = (df["Work_1_Time"] < 0).astype(int)
    df["work_1_seconds"] = df["Work_1_Time"].abs()
    df["work_1_sec_per_furlong"] = df["work_1_seconds"] / (df["Work_1_Dist"] / 220.0)
    days = (pd.to_datetime(df["Date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
            - pd.to_datetime(df["Work_1_Date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce"))
    df["work_1_days_ago"] = days.dt.days

    # Rule 2: connection percentages only count on a real sample.
    df["valid_jock_pct"] = np.where(df["Jock_Sts"] >= MIN_JOCK_STS, df["jock_win_pct"], np.nan)
    df["valid_trn_pct"] = np.where(df["Trn_Sts"] >= MIN_TRN_STS, df["trn_win_pct"], np.nan)

    # Rule 3: first-time starters. The distiller already labels them Run_Style 'FTS';
    # the spec's numeric test is the fallback.
    pp_missing = df["Prime_Power"].isna() | (df["Prime_Power"] == 0)
    df["is_fts"] = ((df["Run_Style"].str.upper() == "FTS") | (pp_missing & df["Last_Fig"].isna())).astype(int)

    # Context features that don't depend on the field.
    df["purse_ratio"] = df["Today_Purse"] / df["Last_Purse"].where(df["Last_Purse"] > 0)
    df["dist_delta_yds"] = df["Today_Dist_Yds"] - df["Last_Dist_Yds"]
    df["is_turf_to_dirt"] = ((df["Last_Surface"].str.upper() == "T") & (df["Today_Surf"].str.upper() == "D")).astype(int)
    df["has_trouble_comment"] = df["Last_Comment"].map(lambda s: int(bool(TROUBLE.search(s or ""))))
    df["last_fig_delta"] = (df["Last_Fig"] - df["Par_Spd"]).clip(lower=FIG_DELTA_FLOOR)
    df.loc[df["is_fts"] == 1, "last_fig_delta"] = np.nan
    df["best3"] = df[["Last_Fig", "Fig_2Back", "Fig_3Back"]].max(axis=1)
    df["best_3back_delta"] = df["best3"] - df["Par_Spd"]
    df["last_e1_delta"] = df["Last_E1"] - df["Par_E1"]
    df["last_e2_delta"] = df["Last_E2"] - df["Par_E2"]
    df["last_lp_delta"] = df["Last_LP"] - df["Par_LP"]
    df["_raw"] = raw.to_dict("records")  # kept for the v1 grader, which reads the raw strings
    return df


# ---- Stage 2 -------------------------------------------------------------------

def _rank_desc(s: pd.Series) -> pd.Series:
    return s.rank(ascending=False, method="min", na_option="keep").astype("Int64")


def stage2_scratch(df: pd.DataFrame, scratches: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """scratches: {race: [prog, ...]}. Returns (runner frame, {race: race-level dict})."""
    df = df.copy()
    sc = {int(k): {str(p).strip().upper() for p in v} for k, v in (scratches or {}).items()}
    df["is_scratched"] = [int(p in sc.get(int(r), set())) for r, p in zip(df["Race"], df["Prog"])]
    rank_cols = ["pp_rank", "pp_delta_to_1st", "pp_lead_over_2nd", "fig_within_2_of_top", "last_e1_rank",
                 "e1_lead_over_ep", "last_lp_rank", "last_lp_percentile"]
    for c in rank_cols:
        df[c] = np.nan
    races = {}
    for race, g in df.groupby("Race"):
        a = g[g["is_scratched"] == 0]
        n = len(a)
        ix = a.index
        df.loc[ix, "pp_rank"] = _rank_desc(a["Prime_Power"]).astype(float)
        pp_max = a["Prime_Power"].max()
        df.loc[ix, "pp_delta_to_1st"] = a["Prime_Power"] - pp_max
        pps = a["Prime_Power"].dropna().sort_values(ascending=False)
        if len(pps) > 1:
            df.loc[pps.index[0], "pp_lead_over_2nd"] = pps.iloc[0] - pps.iloc[1]
        top_fig = a["best3"].max()
        df.loc[ix, "fig_within_2_of_top"] = (a["best3"] >= top_fig - 2).astype(int)
        df.loc[ix, "last_e1_rank"] = _rank_desc(a["Last_E1"]).astype(float)
        speed = a[a["Run_Style"].str.upper().isin(["E", "E/P"])]
        for i, h in a.iterrows():
            rivals = speed.drop(index=i, errors="ignore")["Last_E1"].dropna()
            if pd.notna(h["Last_E1"]) and len(rivals):
                df.loc[i, "e1_lead_over_ep"] = h["Last_E1"] - rivals.max()
        lp_rank = _rank_desc(a["Last_LP"]).astype(float)
        df.loc[ix, "last_lp_rank"] = lp_rank
        df.loc[ix, "last_lp_percentile"] = (n - lp_rank) / n if n else np.nan
        first = g.iloc[0]
        qsp = int(a["QSP"].fillna(0).sum())
        races[int(race)] = dict(
            race_id=race_id(first["Date"], first["Track"], race), track_code=first["Track"].upper(),
            race_date=int(first["Date"]), race_num=int(race), today_dist_yds=int(first["Today_Dist_Yds"]),
            today_surf=first["Today_Surf"], today_class=first["Today_Class"], today_purse=int(first["Today_Purse"]),
            par_e1=first["Par_E1"], par_e2=first["Par_E2"], par_lp=first["Par_LP"], par_spd=first["Par_Spd"],
            active_starters=n, active_qsp_sum=qsp)
    return df, races


# ---- Stage 3 -------------------------------------------------------------------

def load_extras(path: str | Path | None) -> dict:
    """Card sidecar JSON: {"track_cond": "FT" | {race: cond}, "scratches": {race: [prog]},
    "herd_radar": [{race, prog, drf_pick_rank, ..., retail_trap}], "grades": [{race, prog, grade, tags, p}]}"""
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def stage3_grade(df: pd.DataFrame, races: dict, extras: dict | None = None) -> tuple[pd.DataFrame, dict]:
    extras = extras or {}
    df = df.copy()
    for c in HERD_COLS + ["has_herd_radar", "tag_retail_trap", "tag_w", "tag_e", "tag_unseen_retail"]:
        if c not in df:
            df[c] = 0 if c not in ("beyer_1", "beyer_2", "beyer_3", "eq_change") else None
    df["primary_grade"] = None
    df["grade_source"] = None
    df["grade_reasons"] = None
    df["est_true_prob_p"] = np.nan
    df["bris_vs_beyer_delta"] = np.nan
    key = {(int(r), p): i for i, (r, p) in enumerate(zip(df["Race"], df["Prog"]))}
    pos = lambda r, p: df.index[key[(int(r), str(p).strip().upper())]]

    # Herd radar overlay
    for h in extras.get("herd_radar", []):
        if (int(h["race"]), str(h["prog"]).upper()) not in key:
            continue
        i = pos(h["race"], h["prog"])
        df.loc[i, "has_herd_radar"] = 1
        for c in HERD_COLS:
            if c in h:
                df.at[i, c] = h[c]
        if h.get("retail_trap"):
            df.loc[i, "tag_retail_trap"] = 1
    for race in races:  # a race counts as radar-covered if any runner in it had DRF data
        m = df["Race"] == race
        if df.loc[m, "has_herd_radar"].any():
            df.loc[m, "has_herd_radar"] = 1
            b = pd.to_numeric(df.loc[m & (df["is_scratched"] == 0), "beyer_1"], errors="coerce")
            if b.notna().any():
                df.loc[m, "bris_vs_beyer_delta"] = (df.loc[m, "last_fig_delta"]
                                                     - (pd.to_numeric(df.loc[m, "beyer_1"], errors="coerce") - b.mean()))

    # Rules grader on active runners (v1 grader reads raw strings)
    for race, info in races.items():
        act = df[(df["Race"] == race) & (df["is_scratched"] == 0)]
        if act.empty:
            info["pace_scenario"] = "Honest"
            continue
        grades, ginfo = grade_race(pd.DataFrame(list(act["_raw"])))
        info["pace_scenario"] = PACE_LABEL[ginfo["pace_scenario"]]
        for g in grades:
            i = pos(race, g.pgm)
            df.loc[i, ["primary_grade", "grade_source", "grade_reasons"]] = [g.bucket, "rules_v1", "; ".join(g.reasons)]
            df.loc[i, "tag_w"] = int("W" in g.mods)
            df.loc[i, "tag_e"] = int("E" in g.mods)

    # Grant's grades override the rules grader where given
    for g in extras.get("grades", []):
        if (int(g["race"]), str(g["prog"]).upper()) not in key:
            continue
        i = pos(g["race"], g["prog"])
        tags = {t.strip().upper() for t in g.get("tags", [])}
        df.loc[i, ["primary_grade", "grade_source"]] = [g["grade"], g.get("source", "grant")]
        df.loc[i, "tag_w"] = int("W" in tags)
        df.loc[i, "tag_e"] = int("E" in tags)
        if "RETAIL_TRAP" in tags:
            df.loc[i, "tag_retail_trap"] = 1
        if g.get("p") is not None:
            df.loc[i, "est_true_prob_p"] = float(g["p"])

    act = df["is_scratched"] == 0
    df["tag_unseen_retail"] = ((df["has_herd_radar"] == 1) & act & (df["primary_grade"].notna())
                               & (pd.to_numeric(df["drf_pick_rank"]) == 0)
                               & (pd.to_numeric(df["consensus_rank"]) == 0)).astype(int)
    tc = extras.get("track_cond", "FT")
    for race, info in races.items():
        info["track_cond"] = tc.get(str(race), "FT") if isinstance(tc, dict) else tc
        m = (df["Race"] == race) & act & ((df["primary_grade"] == "F-") | (df["tag_retail_trap"] == 1))
        info["est_dead_pool_share"] = float(df.loc[m, "ml_implied_prob"].sum())
    return df, races


# ---- Write --------------------------------------------------------------------

RUNNER_MAP = {  # db column -> frame column
    "post_pos": "Post", "prog_num": "Prog", "horse_name": "Horse", "ml_odds": "ML_Odds", "weight": "Weight",
    "jockey": "Jockey", "jock_sts": "Jock_Sts", "jock_win": "Jock_Win", "trainer": "Trainer",
    "trn_sts": "Trn_Sts", "trn_win": "Trn_Win", "sire": "Sire", "dam_sire": "Dam_Sire", "run_style": "Run_Style",
    "qsp": "QSP", "days_off": "Days_Off", "prime_power": "Prime_Power", "last_date": "Last_Date",
    "last_track": "Last_Track", "last_dist_yds": "Last_Dist_Yds", "last_surface": "Last_Surface",
    "last_cond": "Last_Cond", "last_purse": "Last_Purse", "last_comment": "Last_Comment", "last_e1": "Last_E1",
    "last_e2": "Last_E2", "last_lp": "Last_LP", "last_fig": "Last_Fig", "fig_2back": "Fig_2Back",
    "fig_3back": "Fig_3Back", "work_1_date": "Work_1_Date", "work_1_track": "Work_1_Track", "work_1_dist": "Work_1_Dist",
}
SAME = ["is_scratched", "is_fts", "ml_implied_prob", "jock_win_pct", "trn_win_pct", "valid_jock_pct", "valid_trn_pct",
        "is_bullet_work", "work_1_seconds", "work_1_sec_per_furlong", "work_1_days_ago", "pp_rank", "pp_delta_to_1st",
        "pp_lead_over_2nd", "last_fig_delta", "best_3back_delta", "fig_within_2_of_top", "last_e1_rank",
        "last_e1_delta", "e1_lead_over_ep", "last_e2_delta", "last_lp_rank", "last_lp_percentile", "last_lp_delta",
        "purse_ratio", "dist_delta_yds", "is_turf_to_dirt", "has_trouble_comment", "bris_vs_beyer_delta",
        "has_herd_radar", "primary_grade", "grade_source", "grade_reasons", "tag_w", "tag_e", "tag_retail_trap",
        "tag_unseen_retail", "est_true_prob_p"] + HERD_COLS
INT_COLS = {"pp_rank", "last_e1_rank", "last_lp_rank", "fig_within_2_of_top", "work_1_days_ago", "post_pos", "weight",
            "jock_sts", "jock_win", "trn_sts", "trn_win", "qsp", "days_off", "last_date", "last_dist_yds", "last_purse",
            "last_e1", "last_e2", "last_lp", "last_fig", "work_1_date", "work_1_dist", "dist_delta_yds"}


def _clean(v, col=None):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v == "":
        return None
    if isinstance(v, (np.integer,)):
        v = int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
    if col in INT_COLS and isinstance(v, float):
        return int(v)
    return v


def write_card(con, df: pd.DataFrame, races: dict) -> list[str]:
    ids = [r["race_id"] for r in races.values()]
    con.executemany("DELETE FROM races WHERE race_id=?", [(i,) for i in ids])  # cascades to runners/pools
    rcols = ["race_id", "track_code", "race_date", "race_num", "today_dist_yds", "today_surf", "today_class",
             "today_purse", "track_cond", "par_e1", "par_e2", "par_lp", "par_spd", "active_starters",
             "active_qsp_sum", "pace_scenario", "est_dead_pool_share"]
    for r in races.values():
        con.execute(f"INSERT INTO races({','.join(rcols)}) VALUES ({','.join('?' * len(rcols))})",
                    [_clean(r.get(c), c) for c in rcols])
    cols = ["runner_id", "race_id"] + list(RUNNER_MAP) + SAME
    sql = f"INSERT INTO runners({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    for _, h in df.iterrows():
        vals = [runner_id(h["Date"], h["Track"], h["Race"], h["Prog"]), race_id(h["Date"], h["Track"], h["Race"])]
        vals += [_clean(h[src], c) for c, src in RUNNER_MAP.items()]
        vals += [_clean(h[c], c) for c in SAME]
        con.execute(sql, vals)
    con.commit()
    return ids


def ingest_card(con, csv_path, extras: dict | str | Path | None = None) -> list[str]:
    """Stages 1-3. Returns the race_ids written."""
    ex = extras if isinstance(extras, dict) else load_extras(extras)
    df = stage1_load(csv_path)
    df, races = stage2_scratch(df, ex.get("scratches"))
    df, races = stage3_grade(df, races, ex)
    return write_card(con, df, races)


# ---- Stage 3b: tickets -----------------------------------------------------------

def ensure_pool(con, track, date, first_race, pool_type, takeout=0.0, carryover=0.0) -> str:
    pool_type = pool_type.upper()
    pid = f"{int(date)}_{track.upper()}_R{int(first_race):02d}_{pool_type}"
    legs = P.HORIZONTAL_LEGS.get(pool_type, 1)
    con.execute("INSERT OR IGNORE INTO pools(pool_id,track_code,race_date,anchor_race_id,pool_type,num_legs,"
                "takeout_rate,carryover_pct) VALUES (?,?,?,?,?,?,?,?)",
                (pid, track.upper(), int(date), race_id(date, track, first_race), pool_type, legs, takeout, carryover))
    return pid


def log_slips(con, pool_id: str, slips: list[dict]) -> list[dict]:
    """slips: [{slip_tier, matrix, base}]. Asserts the slips are disjoint, recomputes combos and cost."""
    ptype = con.execute("SELECT pool_type FROM pools WHERE pool_id=?", (pool_id,)).fetchone()[0]
    vertical = ptype in P.VERTICAL_SLOTS
    legs = [P.parse_matrix(s["matrix"]) for s in slips]
    sets = P.assert_disjoint(legs, vertical)
    out = []
    for n, (s, l, combos) in enumerate(zip(slips, legs, sets), 1):
        tid = f"{pool_id}_SLIP{n}"
        row = dict(ticket_id=tid, pool_id=pool_id, slip_tier=s.get("slip_tier", f"Slip_{n}"),
                   matrix_definition=P.format_matrix(l), base_wager=float(s["base"]),
                   unique_combos=len(combos), ticket_cost=round(float(s["base"]) * len(combos), 2))
        con.execute("INSERT OR REPLACE INTO tickets(ticket_id,pool_id,slip_tier,matrix_definition,base_wager,"
                    "unique_combos,ticket_cost) VALUES (:ticket_id,:pool_id,:slip_tier,:matrix_definition,"
                    ":base_wager,:unique_combos,:ticket_cost)", row)
        out.append(row)
    con.commit()
    return out


# ---- Stage 4 -------------------------------------------------------------------

def settle_card(con, track: str, date, results_csv=None, pools_csv=None) -> dict:
    """results_csv: race,prog,finish_pos,final_odds[,beaten_lengths,win_pay,place_pay,show_pay]
    pools_csv: pool_type,first_race,winning_prog_str,base,payout[,takeout,carryover]
      winning_prog_str: '1-3-7' (slots or legs); dead heats as '1/4' within a slot or leg."""
    track, date = track.upper(), int(date)
    summary = {"runners": 0, "pools": 0, "tickets": 0}
    if results_csv:
        res = pd.read_csv(results_csv, dtype={"prog": str})
        for _, r in res.iterrows():
            rid = runner_id(date, track, r["race"], r["prog"])
            con.execute("UPDATE runners SET finish_pos=?, final_tote_odds=?, beaten_lengths=?, win_pay=?, "
                        "place_pay=?, show_pay=? WHERE runner_id=?",
                        (_clean(r.get("finish_pos")), _clean(r.get("final_odds")), _clean(r.get("beaten_lengths")),
                         _clean(r.get("win_pay")) or 0.0, _clean(r.get("place_pay")) or 0.0,
                         _clean(r.get("show_pay")) or 0.0, rid))
            summary["runners"] += 1
        # CLV and normalized closing probability, per race over active runners
        for (rid,) in con.execute("SELECT race_id FROM races WHERE track_code=? AND race_date=?", (track, date)).fetchall():
            rows = con.execute("SELECT runner_id, ml_odds, final_tote_odds FROM runners WHERE race_id=? AND "
                               "is_scratched=0 AND final_tote_odds IS NOT NULL", (rid,)).fetchall()
            tot = sum(1 / (r["final_tote_odds"] + 1) for r in rows)
            for r in rows:
                q = 1 / (r["final_tote_odds"] + 1)
                con.execute("UPDATE runners SET final_implied_prob_q=?, clv_ratio=? WHERE runner_id=?",
                            (q / tot if tot else None, q / (1 / (r["ml_odds"] + 1)), r["runner_id"]))
    if pools_csv:
        for _, p in pd.read_csv(pools_csv, dtype={"winning_prog_str": str}).iterrows():
            pid = ensure_pool(con, track, date, p["first_race"], p["pool_type"],
                              _clean(p.get("takeout")) or 0.0, _clean(p.get("carryover")) or 0.0)
            settle_pool(con, pid, p["winning_prog_str"], float(p["payout"]) / float(p["base"]))
            summary["pools"] += 1
    summary["tickets"] = settle_tickets(con, track, date)
    con.commit()
    return summary


def _grades(con, rid) -> dict:
    return {r["prog_num"]: r["primary_grade"] for r in con.execute(
        "SELECT prog_num, primary_grade FROM runners WHERE race_id=? AND is_scratched=0", (rid,))}


def settle_pool(con, pool_id: str, winning: str, payout_1d: float) -> dict:
    p = con.execute("SELECT * FROM pools WHERE pool_id=?", (pool_id,)).fetchone()
    first = con.execute("SELECT * FROM races WHERE race_id=?", (p["anchor_race_id"],)).fetchone()
    slots = [[w.strip().upper() for w in s.split("/")] for s in str(winning).split("-")]
    vertical = p["pool_type"] in P.VERTICAL_SLOTS
    rids = ([p["anchor_race_id"]] if vertical else
            [race_id(p["race_date"], p["track_code"], first["race_num"] + k) for k in range(p["num_legs"])])
    legs = [_grades(con, r) for r in rids]
    cap = P.capture(p["pool_type"], legs, slots)
    grade_str = " / ".join(_grade_label(con, rids[0] if vertical else rids[k], s[0]) for k, s in enumerate(slots))
    theo = lev = None
    if not vertical:
        odds = [con.execute("SELECT final_tote_odds FROM runners WHERE race_id=? AND prog_num=?", (rids[k], s[0]))
                .fetchone() for k, s in enumerate(slots)]
        if all(o and o[0] is not None for o in odds):
            theo = P.theo_win_parlay([o[0] for o in odds])
            lev = payout_1d / theo
    yld = payout_1d / cap["combos_to_capture"] if cap["combos_to_capture"] else None
    con.execute("UPDATE pools SET winning_prog_str=?, winning_grade_str=?, payout_1d_norm=?, theo_win_parlay_1d=?, "
                "parlay_leverage_ratio=?, captured_by_playable=?, lowest_tier_needed=?, combos_to_capture=?, "
                "combos_to_capture_keyed=?, theo_tier_yield=? WHERE pool_id=?",
                (winning, grade_str, payout_1d, theo, lev, cap["captured_by_playable"], cap["lowest_tier_needed"],
                 cap["combos_to_capture"], cap["combos_to_capture_keyed"], yld, pool_id))
    return cap


def _grade_label(con, rid, prog) -> str:
    r = con.execute("SELECT primary_grade, tag_w, tag_e FROM runners WHERE race_id=? AND prog_num=?", (rid, prog)).fetchone()
    if not r or not r["primary_grade"]:
        return "?"
    return r["primary_grade"] + ("[W]" if r["tag_w"] else "") + ("[E]" if r["tag_e"] else "")


def settle_tickets(con, track, date) -> int:
    rows = con.execute("SELECT t.*, p.pool_type, p.winning_prog_str, p.payout_1d_norm FROM tickets t "
                       "JOIN pools p USING (pool_id) WHERE p.track_code=? AND p.race_date=? "
                       "AND p.winning_prog_str IS NOT NULL", (track, date)).fetchall()
    for t in rows:
        legs = P.parse_matrix(t["matrix_definition"])
        slots = [set(s.split("/")) for s in t["winning_prog_str"].upper().split("-")]
        win = len(legs) == len(slots) and all(set(l) & s for l, s in zip(legs, slots))
        con.execute("UPDATE tickets SET is_winner=?, gross_return=? WHERE ticket_id=?",
                    (int(win), round(t["payout_1d_norm"] * t["base_wager"], 2) if win else 0.0, t["ticket_id"]))
    return len(rows)
