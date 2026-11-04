import csv
import re
import pandas as pd

# Standardized AmTote / Xpressbet 3-Character Event Code Mapping
TRACK_CODE_MAP = {
    "BEL": "BED",  # Belmont Park / Belmont at the Big A
    "CD":  "CHD",  # Churchill Downs
    "CT":  "TWN",  # Charles Town
    "DED": "DTN",  # Delta Downs
    "GP":  "GPM",  # Gulfstream Park
    "IND": "IJN",  # Horseshoe Indianapolis (Indiana Downs)
    "INJ": "IJN",  # Horseshoe Indianapolis alias
    "IJN": "IJN",  # Horseshoe Indianapolis AmTote
    "KEE": "KED",  # Keeneland
    "MNR": "MNE",  # Mountaineer Park
    "MVR": "MVD",  # Mahoning Valley
    "PRX": "PHD",  # Parx Racing
    "RP":  "RED",  # Remington Park
    "SA":  "SAD",  # Santa Anita
    "WO":  "WOT",  # Woodbine Thoroughbred
    "ZIA": "NZM",  # Zia Park
    "AQU": "AQD",  # Aqueduct
    "DEL": "DLD",  # Delaware Park
    "DMR": "DMD",  # Del Mar
    "FG":  "JGD",  # Fair Grounds
    "FL":  "FIM",  # Finger Lakes
    "HAW": "HAD",  # Hawthorne
    "KD":  "KDM",  # Kentucky Downs
    "LRL": "LRM",  # Laurel Park
    "OP":  "OPM",  # Oaklawn Park
    "PEN": "PEN",  # Penn National
    "PID": "QIN",  # Presque Isle Downs
    "SAR": "STD",  # Saratoga
    "TAM": "TAM",  # Tampa Bay Downs
    "TP":  "TPD",  # Turfway Park
}


def normalize_race_date(val):
    """Forces any date input (20261103, '20261103.0', '11/03/2026', datetime) into strict YYYY-MM-DD."""
    s = re.sub(r"\.0$", "", str(val).strip())
    if not s or s.lower() == "nan":
        return ""
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return pd.to_datetime(s).strftime("%Y-%m-%d")


def clean_pgm(val):
    """Strips trailing '.0' from numeric program numbers and normalizes coupled entries (e.g. 1A)."""
    s = re.sub(r"\.0$", "", str(val).strip().upper())
    return "" if s in ("", "NAN", "NONE") else s


def sort_pgms(pgms):
    """Deduplicates and sorts program numbers numerically then alphabetically (1, 1A, 2, 10)."""
    cleaned = {clean_pgm(p) for p in pgms if clean_pgm(p)}
    return sorted(
        cleaned,
        key=lambda x: (int("".join(filter(str.isdigit, x)) or 0), x)
    )


def format_wheel_leg(pgms):
    """Formats a set/list of program numbers: '2' for single, '(2;6;10)' for multiple."""
    sorted_pgms = sort_pgms(pgms)
    if not sorted_pgms:
        return ""
    return sorted_pgms[0] if len(sorted_pgms) == 1 else f"({';'.join(sorted_pgms)})"


def count_valid_superfecta_combos(s1, s2, s3, s4):
    """Counts exact 4-horse permutations (a != b != c != d) across the 4 wheel slots."""
    l1, l2, l3, l4 = set(sort_pgms(s1)), set(sort_pgms(s2)), set(sort_pgms(s3)), set(sort_pgms(s4))
    combos = 0
    for a in l1:
        for b in l2:
            if b == a:
                continue
            for c in l3:
                if c == a or c == b:
                    continue
                for d in l4:
                    if d == a or d == b or d == c:
                        continue
                    combos += 1
    return combos


def export_disjointed_superfecta_csv(
    df_grades,
    output_csv_path="disjointed_superfecta_wagers.csv",
    min_qsp_sum=19.0,
    min_active_starters=8,
    base_amount=0.10,
    exclude_ml_fav_in_1st=True,
    include_d_top_saver=True,
    min_odds="",
    mtp="",
    write_mode="w"
):
    """
    Silo 2: Standalone Disjointed 5-Line Target-Zone Superfecta Generator.
    Outputs a headerless 9-column CSV file compliant with Expressbet / 1/ST BET specifications.
    Automatically normalizes dates to YYYY-MM-DD and translates Brisnet track codes to AmTote event codes.
    """
    df = df_grades.copy()
    if "scratched" not in df.columns:
        df["scratched"] = 0
    df["scratched"] = pd.to_numeric(df["scratched"], errors="coerce").fillna(0).astype(int)

    df = df[
        (df["scratched"] == 0) &
        (df["bucket"].notnull())
    ].copy()

    df["race"] = pd.to_numeric(df["race"], errors="coerce")
    df = df[df["race"].notnull()].copy()
    df["race"] = df["race"].astype(int)

    df["race_date_norm"] = df["race_date"].apply(normalize_race_date)
    df["track_clean"] = df["track"].astype(str).str.strip().str.upper()
    df["pgm_str"] = df["pgm"].apply(clean_pgm)
    df["bucket"] = df["bucket"].astype(str).str.strip().str.upper()
    df = df[df["pgm_str"] != ""].copy()

    csv_rows = []
    grouped = df.groupby(["race_date_norm", "track_clean", "race"], sort=True)

    for (race_date_norm, track_clean, race_num), grp in grouped:
        if len(grp) < min_active_starters:
            continue

        if min_qsp_sum is not None and "qsp_sum" in grp.columns:
            qsp_val = pd.to_numeric(grp["qsp_sum"], errors="coerce").iloc[0]
            if pd.isna(qsp_val) or float(qsp_val) < float(min_qsp_sum):
                continue

        if exclude_ml_fav_in_1st and "ml_odds" in grp.columns:
            ml_series = pd.to_numeric(grp["ml_odds"], errors="coerce")
            if ml_series.notnull().any():
                min_ml = ml_series.min()
                fav_pgms = set(grp[ml_series == min_ml]["pgm_str"])
            else:
                fav_pgms = set()
        else:
            fav_pgms = set()

        aa_set   = set(grp[grp["bucket"].isin(["A+", "A"])]["pgm_str"])
        b_set    = set(grp[grp["bucket"] == "B"]["pgm_str"])
        ab_set   = aa_set | b_set
        c_set    = set(grp[grp["bucket"] == "C"]["pgm_str"])
        d_set    = set(grp[grp["bucket"] == "D"]["pgm_str"])
        cd_set   = c_set | d_set
        play_set = ab_set | cd_set

        top_ab_nonfav = ab_set - fav_pgms
        upload_track = TRACK_CODE_MAP.get(track_clean, track_clean)

        wheel_lines = [
            (top_ab_nonfav, cd_set, play_set, play_set),
            (top_ab_nonfav, ab_set, cd_set,   play_set),
            (top_ab_nonfav, ab_set, ab_set,   cd_set),
        ]
        if include_d_top_saver:
            wheel_lines.extend([
                (d_set, ab_set,   play_set, aa_set),
                (d_set, play_set, aa_set,   b_set),
            ])

        for (s1, s2, s3, s4) in wheel_lines:
            if count_valid_superfecta_combos(s1, s2, s3, s4) > 0:
                combo_str = (
                    f"{format_wheel_leg(s1)}-{format_wheel_leg(s2)}-"
                    f"{format_wheel_leg(s3)}-{format_wheel_leg(s4)}"
                )
                csv_rows.append([
                    race_date_norm,
                    upload_track,
                    str(int(race_num)),
                    "SUPERFECTA",
                    combo_str,
                    f"{float(base_amount):.2f}",
                    str(min_odds),
                    str(mtp),
                    "WHEEL"
                ])

    with open(output_csv_path, mode=write_mode, newline="") as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)

    return csv_rows