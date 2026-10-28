"""Loader for Grant's CSV outputs.

Two layouts, one loader:
  * picks_and_tags:  Race, Post, Horse, ML_Odds, Grade, Modifiers, Full_Tag, Retail_Radar
  * master_merged:   the distilled columns (Track, Date, Race, Post, Prog, ... Fig_3Back, ...) + the four grade columns

Grades may be A+ A B C D F- F, or the Breeders' Cup split D-INT / D-DOM. Those are stored as
bucket 'D' with a 'D-INT' / 'D-DOM' tag, so every D-based report keeps working and the split
can still be scored separately. Track and date come from Track/Date columns when present,
otherwise from a file name like FL_20261028_picks_and_tags.csv, otherwise from --track/--date.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .db import flag
from .grading import pace_scenario

GRADE_RE = re.compile(r"^\s*(A\+|A|B|C|D-INT|D-DOM|D|F-|F)\s*$", re.I)


def _num(x):
    try:
        v = float(str(x).replace("%", "").strip())
        return v
    except ValueError:
        return None


def _date(s: str) -> str:
    s = re.sub(r"\D", "", str(s))[:8]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


def ingest_picks_csv(con, path, track: str | None = None, race_date: str | None = None,
                     source: str = "grant_csv") -> tuple[str, dict]:
    path = Path(path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    need = {"Race", "Horse", "Grade"}
    if not need <= set(df.columns):
        raise ValueError(f"{path.name}: missing columns {sorted(need - set(df.columns))}")
    m = re.match(r"([A-Za-z]{2,4})_(\d{8})_", path.name)
    if not track:
        track = df["Track"].iloc[0] if "Track" in df.columns and len(df) else (m.group(1) if m else None)
    if not race_date:
        race_date = _date(df["Date"].iloc[0]) if "Date" in df.columns and len(df) else (_date(m.group(2)) if m else None)
    if not track or not race_date:
        raise ValueError(f"{path.name}: track/date not in the file or its name; pass --track and --date")
    track = track.upper().strip()
    merged = "Prime_Power" in df.columns
    card_id = f"{track}-{race_date}-grant"
    con.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
    con.execute("DELETE FROM data_quality_flags WHERE card_id=?", (card_id,))
    con.execute("INSERT INTO cards(card_id,track,race_date,source,source_file,notes) VALUES (?,?,?,?,?,?)",
                (card_id, track, race_date, source, path.name,
                 "Grant's merged inputs + grades" if merged else "Grant's picks and tags"))
    pgm_col = "Prog" if "Prog" in df.columns else "Post"
    if pgm_col == "Post":
        flag(con, card_id, None, "info", "file has post positions only; post is used as the program number")

    n = 0
    for race, g in df.groupby(df["Race"].astype(int)):
        qsp = [_num(x) or 0 for x in g["QSP"]] if "QSP" in g.columns else None
        first = g.iloc[0]
        con.execute("INSERT INTO races(card_id,race,surface,distance_yds,race_type,field_size,qsp_sum,pace_scenario,"
                    "par_spd,par_lp,grades_complete) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                    (card_id, race, first.get("Today_Surf") or None, _num(first.get("Today_Dist_Yds", "")),
                     first.get("Today_Class") or None, len(g), int(sum(qsp)) if qsp else None,
                     pace_scenario(sum(qsp)) if qsp else None,
                     _num(first.get("Par_Spd", "")), _num(first.get("Par_LP", ""))))
        seen = set()
        for _, r in g.iterrows():
            pgm = str(r[pgm_col]).strip().upper()
            gm = GRADE_RE.match(r["Grade"])
            if not gm:
                flag(con, card_id, race, "warn", f"#{pgm} {r['Horse']}: grade {r['Grade']!r} not recognised; left ungraded")
            if pgm in seen:
                flag(con, card_id, race, "error", f"#{pgm} appears twice; keeping the first")
                continue
            seen.add(pgm)
            grade = gm.group(1).upper() if gm else None
            tags = set()
            if grade in ("D-INT", "D-DOM"):
                tags.add(grade)
                grade = "D"
            radar = (r.get("Retail_Radar") or "").upper()
            if "UNSEEN" in radar:
                tags.add("UNSEEN")
            if "TRAP" in radar:
                tags.add("RETAIL_TRAP")
            mods = sorted(set(re.findall(r"\[([WE])\]", r.get("Modifiers", "") + r.get("Full_Tag", ""))))
            con.execute("INSERT INTO pre_race_grades(card_id,race,pgm,horse_name,bucket,modifiers,tags,ml_odds,"
                        "prime_power,last_fig,last_e1,last_lp,qsp,grade_confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (card_id, race, pgm, r["Horse"].strip(), grade, ",".join(mods), ",".join(sorted(tags)),
                         _num(r.get("ML_Odds", "")), _num(r.get("Prime_Power", "")), _num(r.get("Last_Fig", "")),
                         _num(r.get("Last_E1", "")), _num(r.get("Last_LP", "")), _num(r.get("QSP", "")), "explicit"))
            n += 1
    from .breakdown import flags_vs_results
    flags_vs_results(con, card_id, track, race_date)
    con.commit()
    return card_id, {"races": df["Race"].nunique(), "runners": n, "date": race_date, "merged": merged}
