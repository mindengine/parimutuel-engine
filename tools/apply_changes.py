"""Apply scratch / also-eligible changes (by horse NAME, never by number) to bc_v3 cards.

usage: python tools/apply_changes.py CHANGES.json V3_CSV... --outdir DIR

Adds a Status column to each card: RUNNING, SCRATCHED, AE_OUT (also-eligible not drawn in),
or EXPECTED_SCR (flagged, still included). Writes DIR/<card>_status.csv (everyone, with Status)
and DIR/<card>_final.csv (runners only, i.e. RUNNING + EXPECTED_SCR), and prints a report of
every name it could not place, so nothing is applied silently.
"""
import argparse
import json
import re
from pathlib import Path

import pandas as pd


def key(s):
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"[\(\[][A-Z]{2,4}[\)\]]", "", str(s).upper()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("changes"); ap.add_argument("cards", nargs="+"); ap.add_argument("--outdir", required=True)
    a = ap.parse_args()
    ch = json.load(open(a.changes))["races"]
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    for p in a.cards:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        day = "Fri" if df.Date.iloc[0].endswith("30") else "Sat"
        df["Status"] = "RUNNING"
        df["_k"] = df.Horse.map(key)
        df["_bc"] = df.Today_Class.str.extract(r"^(BC[A-Za-z]+)-G1")[0]
        print(f"== {Path(p).name}")
        for race_key, c in ch.items():
            d, bc = race_key.split()
            if d != day:
                continue
            in_race = (df.Race.astype(int) == int(bc[1:])) if re.fullmatch(r"R\d+", bc) else (df._bc == bc)  # "Sat R3" for undercard races
            if not in_race.any():
                print(f"  {bc}: race not on this card"); continue
            rnum = int(df[in_race].Race.iloc[0])
            for kind, status in (("out", "SCRATCHED"), ("next_in", "AE_OUT"), ("expected_out", "EXPECTED_SCR")):
                for n in c.get(kind, []):
                    m = in_race & (df._k == key(n))
                    if m.any():
                        df.loc[m, "Status"] = status
                        print(f"  R{rnum} {bc}: {n} #{df[m].Prog.iloc[0]} -> {status}")
                    else:
                        other = df[df._k == key(n)]
                        where = f" (on this card in R{other.Race.iloc[0]} #{other.Prog.iloc[0]}, left as is)" if len(other) else " (not on Brisnet's card: already out before the draw)"
                        print(f"  R{rnum} {bc}: {n} [{kind}] not in this race{where}")
            for n in c.get("in", []):
                m = in_race & (df._k == key(n))
                print(f"  R{rnum} {bc}: {n} [in] " + (f"#{df[m].Prog.iloc[0]} running" if m.any() else "NOT on this race's card in Brisnet's file"))
            n_run = (in_race & df.Status.isin(["RUNNING", "EXPECTED_SCR"])).sum()
            print(f"  R{rnum} {bc}: {n_run} runners after changes" + ("  <-- more than 14: some also-eligibles' status unknown" if n_run > 14 else ""))
        stem = Path(p).stem
        df.drop(columns=["_k", "_bc"]).to_csv(out / f"{stem}_status.csv", index=False)
        df[df.Status.isin(["RUNNING", "EXPECTED_SCR"])].drop(columns=["_k", "_bc"]).to_csv(out / f"{stem}_final.csv", index=False)


if __name__ == "__main__":
    main()
