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
       "Trn_Yr_Sts", "Trn_Yr_Win_Pct", "Jock_Yr_Sts", "Jock_Yr_Win_Pct"]


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
    return out


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
    with open(a.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head + NEW)
        for r in body:
            e = ext.get((r[ri].strip(), r[pi].strip().upper()))
            if e is None:
                missing += 1
                e = {k: "" for k in NEW}
            w.writerow(r + [e[k] for k in NEW])
    v2.unlink()
    if missing:
        raise SystemExit(f"{missing} rows could not be matched back to the PP file")
    print(f"Exported {len(body)} entries with {len(NEW)} v3 columns to {a.output}")


if __name__ == "__main__":
    main()
