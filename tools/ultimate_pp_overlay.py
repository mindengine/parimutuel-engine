"""Add Brisnet Ultimate PP (w/ QuickPlay Comments) data to a distilled v3 card.

usage: python tools/ultimate_pp_overlay.py --pdf kee1031y.pdf --card distilled_v3_KEE_20261031.csv \
           --output bc_v3_KEE_20261031.csv

Adds, per horse (matched on Race + normalized horse name):
  Bris_Current_Class   Brisnet current class rating (from the race summary; present for foreign shippers too)
  Bris_Avg_Class_L3    Brisnet average class rating, last three starts
  QP_Positive          Brisnet QuickPlay positive comments, joined with ' | '
  QP_Negative          Brisnet QuickPlay negative comments, joined with ' | '
  Last_Intl_Trip_Note  most recent race-reader trip comment Brisnet prints for a foreign start
                       (e.g. 'Hld up in rr: hdwy 3f out'), prefixed with that race's date

Nothing is imputed: a horse without a value keeps a blank. A report lists any horse
that could not be matched in either direction.
Needs `pdftotext` (poppler-utils).
"""
from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict

import pandas as pd

PAGE_RE = re.compile(r"Ultimate PP's w/ QuickPlay Comments.*?\bRace (\d{1,2})\b")
HORSE_RE = re.compile(r"^\s*(?:\d{1,2}[A-Z]?\s+)?([A-Z][A-Za-z0-9'’.&\- ]+?) \((E/P|E|P|S|NA)\s*\d*\)(?:\s+[^\x00-\x7F])?\s{2,}")
PAIR_RE = re.compile(r"(NA|\d+(?:\.\d+)?)\s+([A-Za-z][A-Za-z0-9'’.&\- ]*?)(?=\s+(?:NA|\d+(?:\.\d+)?)\s+[A-Za-z]|\s*$)")
TRIP_RE = re.compile(r"\s(\d\d-\d\d-\d\d) (\S.*?)\s*$")
COLS = ["Bris_Current_Class", "Bris_Avg_Class_L3", "QP_Positive", "QP_Negative", "Last_Intl_Trip_Note"]


def key(name: str) -> str:
    clean = re.sub(r"[\(\[][A-Z]{2,4}[\)\]]", "", str(name).upper())
    return re.sub(r"[^A-Z0-9]", "", clean)


def pdf_text(path: str) -> list[str]:
    return subprocess.run(["pdftotext", "-layout", path, "-"], check=True, capture_output=True,
                          text=True).stdout.splitlines()


def parse(lines: list[str]) -> dict:
    data = defaultdict(dict)          # (race, key) -> fields
    race, horse, in_class = None, None, False
    for line in lines:
        m = PAGE_RE.search(line)
        if m:
            race = int(m.group(1))
            in_class = False
            continue
        if race is None:
            continue
        if "Current Class" in line and "Class Last 3" in line:
            in_class, horse = True, None
            continue
        if in_class:
            pairs = PAIR_RE.findall(line.strip())
            if len(pairs) >= 4:
                cur_v, cur_n = pairs[2]
                avg_v, avg_n = pairs[3]
                if cur_v != "NA":
                    data[(race, key(cur_n))]["Bris_Current_Class"] = cur_v
                if avg_v != "NA":
                    data[(race, key(avg_n))]["Bris_Avg_Class_L3"] = avg_v
            elif not line.strip():
                pass
            continue
        h = HORSE_RE.match(line)
        if h:
            horse = (race, key(h.group(1)))
            data[horse].setdefault("_name", h.group(1).strip())
            continue
        if horse is None:
            continue
        s = line.strip()
        if s.startswith(("ñ", "×")):
            parts = re.split(r"\s*([ñ×])\s*", " " + s)
            for sym, txt in zip(parts[1::2], parts[2::2]):
                txt = re.sub(r"\s{2,}", " ", txt).strip()
                if txt:
                    col = "QP_Positive" if sym == "ñ" else "QP_Negative"
                    data[horse][col] = (data[horse].get(col, "") + " | " + txt).strip(" |")
            continue
        if "Last_Intl_Trip_Note" not in data[horse]:
            t = TRIP_RE.search(line)
            if t and re.search(r"[a-z]", t.group(2)):
                data[horse]["Last_Intl_Trip_Note"] = f"{t.group(1)} {re.sub(r'\s{2,}', ' ', t.group(2))}"
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--card", required=True, help="distilled v3 CSV for the same day")
    ap.add_argument("--output", required=True)
    a = ap.parse_args(argv)

    data = parse(pdf_text(a.pdf))
    df = pd.read_csv(a.card, dtype=str, keep_default_na=False)
    keys = list(zip(df["Race"].astype(int), df["Horse"].map(key)))
    for c in COLS:
        df[c] = [data.get(k, {}).get(c, "") for k in keys]
    df.to_csv(a.output, index=False)

    card_keys = set(keys)
    pdf_horses = {k for k, v in data.items() if "_name" in v}
    no_pdf = sorted(card_keys - pdf_horses)
    extra = sorted(pdf_horses - card_keys)
    print(f"{len(df)} horses; matched {len(card_keys & pdf_horses)} to the PDF; "
          f"class rating for {sum(df.Bris_Current_Class != '')}, QuickPlay comments for "
          f"{sum((df.QP_Positive != '') | (df.QP_Negative != ''))}, trip note for {sum(df.Last_Intl_Trip_Note != '')}")
    if no_pdf:
        print("in the card but not found in the PDF:", no_pdf)
    if extra:
        print("in the PDF but not in the card:", extra)


if __name__ == "__main__":
    main()
