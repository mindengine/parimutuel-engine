"""distill v3: Grant's distill.py output (47 columns, unchanged) plus the v3 columns.

usage: python tools/distill_v3.py --file KEE1031.DRF --output distilled_v3_KEE_20261031.csv

Runs the original distill.py first, then re-reads the same Brisnet single-file PP data
and appends, per horse (matched on Race + Prog):

  Form_Sig          DOMESTIC | NA_PREP_1X | DOM_BLANK_LAST | INTL_RET_VET | INTL_PURE | FTS
                    (from the raw Brisnet values, before anything is filled in)
  Work_1_Bullet     True when Brisnet marked the latest work as a bullet (stored as a negative time)
  Work_1_Time_Abs   the work time without the bullet sign
  Last_Fin, Fin_2Back, Fin_3Back   official finish position, last three starts
  Last_Btn_Len      lengths beaten last start (0.0 for a winner)
  Last_Win_Margin   winning margin last start (blank unless it won)
  Last_Odds         final odds last start (to-1)
  Last_Class_Raw    Brisnet race-class text for the last start (e.g. 'Elm S.-G3', 'OC80000n2x')
  Last_Group_Class  G1 | G2 | G3 | L | Stk | Hcp | Alw | Clm | MSW | Mdn
  Last_Foreign      True when the last start was outside North America (no Brisnet figures)
  Trn_Yr_Sts, Trn_Yr_Win_Pct, Jock_Yr_Sts, Jock_Yr_Win_Pct   current-year totals (all tracks),
                    not the current-meet numbers that read 0% on opening week

v3.1 additions (Grant, Nov 1). Field numbers checked against the Keeneland 10/30-31 files;
where his list differed from the actual file layout, the file wins:
  Med_Code, Med             today's medication, field 62 (codes below; Breeders' Cup files are all 9)
  Equip_Chg                 field 64: BLINKERS_ON (1, verified: Dormie, Avec Toi 10/30) | BLINKERS_OFF (2)
  Claimed_Last              True when the last start was a claim ('c' in field 1046)
  Days_2Back                days between the last start and the one before (field 266)
  Trn_Angle_1..3            Brisnet key trainer stats, fields 1337-1366: "category sts win% roi"
  Dist_Rec / Trk_Rec / Turf_Rec / Wet_Rec   starts-wins-2nds-3rds (fields 65-84)
  Trk_Wins                  wins at today's track (field 71)
  Best_Fast / Best_Turf / Best_Wet / Best_Dist   career-best Brisnet speed figures (fields 1178-1181)
  Ped_Dirt / Ped_Mud / Ped_Turf / Ped_Dist      Brisnet pedigree ratings, * = estimated (fields 1264-1267)
  E1_Last3 / E1_Avg3 / E1_Best3, LP_Last3 / LP_Best3   early pace and late pace for each of the
                            last 3 starts (each race's own sprint/route call), so one bad break
                            does not hide a speed horse
  Race_QSP_Sum, Race_EEP, Race_Pace   whole-field QSP total, count of E and E/P runners, and
                            LONE_SPEED (<=15) / HONEST / MELTDOWN (>=30) for the entered field
                            (tools/apply_changes.py recomputes these after scratches)

Nothing in the original 47 columns is changed; the new columns sit to the right.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PP = 255   # first past-performance field (0-based) in the Brisnet single-file layout
OFF = {"date": 0, "track": 20, "odds": 260, "class": 280, "fin": 360, "margin": 480, "btn": 490,
       "fig": 590, "grade": 830}
NEW = ["Form_Sig", "Work_1_Bullet", "Work_1_Time_Abs", "Last_Fin", "Fin_2Back", "Fin_3Back",
       "Last_Btn_Len", "Last_Win_Margin", "Last_Odds", "Last_Class_Raw", "Last_Group_Class", "Last_Foreign",
       "Trn_Yr_Sts", "Trn_Yr_Win_Pct", "Jock_Yr_Sts", "Jock_Yr_Win_Pct",
       # v3.1
       "Med_Code", "Med", "Equip_Chg", "Claimed_Last", "Days_2Back",
       "Trn_Angle_1", "Trn_Angle_2", "Trn_Angle_3",
       "Dist_Rec", "Trk_Rec", "Trk_Wins", "Turf_Rec", "Wet_Rec",
       "Best_Fast", "Best_Turf", "Best_Wet", "Best_Dist",
       "Ped_Dirt", "Ped_Mud", "Ped_Turf", "Ped_Dist",
       "E1_Last3", "E1_Avg3", "E1_Best3", "LP_Last3", "LP_Best3",
       "Race_QSP_Sum", "Race_EEP", "Race_Pace"]
# Brisnet single-file medication codes (field 62). 9 = not reported (e.g. Breeders' Cup, no race-day Lasix).
MED = {"0": "NONE", "1": "LASIX", "2": "BUTE", "3": "BUTE_LASIX", "4": "LASIX_1ST",
       "5": "BUTE_LASIX_1ST", "9": "NOT_REPORTED"}
EQUIP = {"1": "BLINKERS_ON", "2": "BLINKERS_OFF"}


def f(row, i):
    return row[i].strip() if i < len(row) else ""


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def group_class(code: str, text: str) -> str:
    code = code.upper()
    if code in ("G1", "G2", "G3"):
        return code
    if code == "N":                      # non-graded stakes: 'ExcelsiorL150k' = Listed, 'YorktownB100k' = black-type stakes
        return "L" if re.search(r"L\d+k$", text) else "Stk"
    if code == "HR" or re.search(r"\bH(cp|andicap)\b|Handicap", text):
        return "Hcp"
    if code in ("A", "AO", "R", "CO", "MO"):
        return "Alw"
    if code == "C":
        return "Clm"
    if code == "S":
        return "MSW"
    if code == "M":
        return "Mdn"
    return code or ""


def form_sig(row, has_pp: bool) -> str:
    if not has_pp:
        return "FTS"
    pp = num(f(row, 250))
    pp = pp if pp else None
    last_fig = num(f(row, PP + OFF["fig"]))
    fig2 = num(f(row, PP + OFF["fig"] + 1))
    style = f(row, 209)
    qsp = num(f(row, 210)) or 0
    if pp is not None and last_fig is not None and fig2 is not None:
        return "DOMESTIC"
    if pp is not None and last_fig is not None:
        return "NA_PREP_1X"
    if pp is not None:
        return "DOM_BLANK_LAST"
    if (style and style != "NA") or qsp > 0:
        return "INTL_RET_VET"
    return "INTL_PURE"


def pct(w, s):
    w, s = num(w), num(s)
    return f"{100 * w / s:.1f}" if s else ""


def extras(row) -> dict:
    d_idx = next((i for i in range(200, 350) if len(row[i]) == 8 and row[i].startswith("20") and row[i].isdigit()), -1)
    has_pp = d_idx != -1 and d_idx + 610 < len(row)
    out = {k: "" for k in NEW}
    out["Form_Sig"] = form_sig(row, has_pp)
    wt = num(f(row, 113))
    if wt is not None:
        out["Work_1_Bullet"] = str(wt < 0)
        out["Work_1_Time_Abs"] = f"{abs(wt):g}"
    out["Trn_Yr_Sts"], out["Trn_Yr_Win_Pct"] = f(row, 1146), pct(f(row, 1147), f(row, 1146))
    out["Jock_Yr_Sts"], out["Jock_Yr_Win_Pct"] = f(row, 1156), pct(f(row, 1157), f(row, 1156))
    # --- v3.1 (0-based index = Brisnet field number - 1) ---
    out["Med_Code"] = f(row, 61)
    out["Med"] = MED.get(f(row, 61), f(row, 61))
    out["Equip_Chg"] = EQUIP.get(f(row, 63), "")
    rec = lambda i: "-".join(f(row, i + k) or "0" for k in range(4))
    out["Dist_Rec"], out["Trk_Rec"], out["Turf_Rec"], out["Wet_Rec"] = rec(64), rec(69), rec(74), rec(79)
    out["Trk_Wins"] = f(row, 70)
    for k, name in enumerate(("Best_Fast", "Best_Turf", "Best_Wet", "Best_Dist")):
        v = f(row, 1177 + k)
        out[name] = "" if v in ("", "0") else v
    for k, name in enumerate(("Ped_Dirt", "Ped_Mud", "Ped_Turf", "Ped_Dist")):
        out[name] = f(row, 1263 + k)
    angles = []
    for i in range(1336, 1366, 5):
        cat = f(row, i)
        if cat:
            angles.append(f"{cat} {f(row, i + 1)} {num(f(row, i + 2)) or 0:.0f}% roi {f(row, i + 4)}")
    for k in range(3):
        out[f"Trn_Angle_{k + 1}"] = angles[k] if k < len(angles) else ""
    if not has_pp:
        return out
    b = d_idx
    fins = [f(row, b + OFF["fin"] + k) for k in range(3)]
    out["Last_Fin"], out["Fin_2Back"], out["Fin_3Back"] = fins
    if fins[0] == "1":
        out["Last_Btn_Len"] = "0.0"
        out["Last_Win_Margin"] = f(row, b + OFF["margin"])
    else:
        out["Last_Btn_Len"] = f(row, b + OFF["btn"])
    odds = num(f(row, b + OFF["odds"]))
    out["Last_Odds"] = f"{odds:g}" if odds else ""      # Brisnet writes 0.00 when a foreign race has no odds
    raw = f(row, b + OFF["class"])
    out["Last_Class_Raw"] = raw
    out["Last_Group_Class"] = group_class(f(row, b + OFF["grade"]), raw)
    # A start with no Brisnet speed figure and no pace figures was run abroad.
    out["Last_Foreign"] = str(f(row, b + OFF["fig"]) == "" and f(row, b + 560) == "" and f(row, b + 510) == "")
    out["Claimed_Last"] = str(f(row, 1045).lower() == "c")
    out["Days_2Back"] = f(row, b + 10)
    e1s, lps = [], []
    for k in range(3):
        if not f(row, b + k):
            break
        dist = num(f(row, b + 60 + k)) or 0
        e1s.append(f(row, b + (520 if abs(dist) >= 1760 else 510) + k))
        lps.append(f(row, b + 560 + k))
    out["E1_Last3"], out["LP_Last3"] = "/".join(e1s), "/".join(lps)
    e1n = [num(x) for x in e1s if num(x)]
    lpn = [num(x) for x in lps if num(x)]
    out["E1_Avg3"] = f"{sum(e1n) / len(e1n):.0f}" if e1n else ""
    out["E1_Best3"] = f"{max(e1n):g}" if e1n else ""
    out["LP_Best3"] = f"{max(lpn):g}" if lpn else ""
    return out


def pace_label(q):
    return "LONE_SPEED" if q <= 15 else "MELTDOWN" if q >= 30 else "HONEST"


def race_pace(rows):
    """Race_QSP_Sum / Race_EEP / Race_Pace for one race's horses (dicts with QSP and Run_Style)."""
    q = sum(num(r["QSP"]) or 0 for r in rows)
    eep = sum(1 for r in rows if r["Run_Style"] in ("E", "E/P"))
    return {"Race_QSP_Sum": f"{q:g}", "Race_EEP": str(eep), "Race_Pace": pace_label(q)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="Brisnet single-file PP data (.DRF)")
    ap.add_argument("--output", required=True)
    ap.add_argument("--races", default=None)
    a = ap.parse_args(argv)

    v2 = Path(a.output).with_suffix(".v2.tmp.csv")
    cmd = [sys.executable, str(HERE / "distill.py"), "--file", a.file, "--output", str(v2)]
    if a.races:
        cmd += ["--races", a.races]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    ext = {}
    with open(a.file, encoding="utf-8", errors="ignore") as fh:
        for row in csv.reader(fh):
            if not row or len(row) < 700:
                continue
            key = (row[2].strip(), row[42].strip().upper())
            if key in ext:
                raise SystemExit(f"duplicate horse key {key} in {a.file}")
            ext[key] = extras(row)

    with open(v2, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    head, body = rows[0], rows[1:]
    ri, pi = head.index("Race"), head.index("Prog")
    missing = 0
    qi, si = head.index("QSP"), head.index("Run_Style")
    by_race = {}
    for r in body:
        by_race.setdefault(r[ri].strip(), []).append({"QSP": r[qi], "Run_Style": r[si]})
    pace = {k: race_pace(v) for k, v in by_race.items()}
    with open(a.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head + NEW)
        for r in body:
            e = ext.get((r[ri].strip(), r[pi].strip().upper()))
            if e is None:
                missing += 1
                e = {k: "" for k in NEW}
            e = {**e, **pace[r[ri].strip()]}
            w.writerow(r + [e[k] for k in NEW])
    v2.unlink()
    if missing:
        raise SystemExit(f"{missing} rows could not be matched back to the PP file")
    print(f"Exported {len(body)} entries with {len(NEW)} v3 columns to {a.output}")


if __name__ == "__main__":
    main()
