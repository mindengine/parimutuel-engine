import csv
import os
import re
from itertools import product
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

VALID_BET_TYPES = {
    "WIN", "PLACE", "SHOW", "WINPLACE", "WINSHOW", "PLACESHOW", "WINPLACESHOW",
    "EXACTA", "QUINELLA", "TRIFECTA", "SUPERFECTA", "EXACT 5", "QUADSUPERFECTA",
    "DOUBLE", "PICK3", "PICK4", "PICK5", "PICK6", "PICK7", "PICK8", "PICK9",
    "PICK10", "V75", "V86", "GRANDSLAM", "TRIPLEPLAY", "SWINGER", "TRIO",
    "DOUBLETRIO", "TRIPLETRIO"
}

BET_TYPE_ALIASES = {
    "TRI": "TRIFECTA",
    "EX": "EXACTA",
    "EXA": "EXACTA",
    "SUP": "SUPERFECTA",
    "SUPER": "SUPERFECTA",
    "DD": "DOUBLE",
    "P3": "PICK3",
    "P4": "PICK4",
    "P5": "PICK5",
    "P6": "PICK6",
}

VALID_COMBO_TYPES = {"WHEEL", "KEY", "BOX", "KEY-BOX", "POWER-BOX"}


def normalize_bet_type(bet_code):
    """Normalizes shorthand bet codes (e.g. 'TRI' -> 'TRIFECTA') and validates against Expressbet's list."""
    code = str(bet_code).strip().upper()
    code = BET_TYPE_ALIASES.get(code, code)
    if code not in VALID_BET_TYPES:
        raise ValueError(f"Invalid Expressbet bet type '{bet_code}'. Must be one of: {sorted(VALID_BET_TYPES)}")
    return code


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
    """WHEEL leg format: '3' for single runner, '(2;6;9)' for multiple runners."""
    sorted_pgms = sort_pgms(pgms)
    if not sorted_pgms:
        return ""
    return sorted_pgms[0] if len(sorted_pgms) == 1 else f"({';'.join(sorted_pgms)})"


def format_box_group(pgms):
    """Bracketed format '[2;3;4;5]' used by KEY, BOX, KEY-BOX, and POWER-BOX."""
    sorted_pgms = sort_pgms(pgms)
    if not sorted_pgms:
        return ""
    return f"[{';'.join(sorted_pgms)}]"


def format_wager_combination(legs, combo_type="WHEEL"):
    """
    Formats a list of runner legs according to the official Expressbet Combo Type rules:
      - WHEEL:     1-2-3 or 1-(2;3;4)-(5;6;7)
      - KEY:       1-[2;3;4;5] (legs[0] must be 1 runner, legs[1] is boxed across remaining legs)
      - BOX:       [2;3;4;5]   (legs[0] is the boxed set of runners)
      - KEY-BOX:   [1]-[2;3;4;5] (legs[0] must be 1 runner keyed in all positions)
      - POWER-BOX: [1]-[2;4]-[2;4;6] (legs[0] must be 1 runner)
    """
    ctype = str(combo_type).strip().upper()
    if ctype not in VALID_COMBO_TYPES:
        raise ValueError(f"Invalid Combo Type '{combo_type}'. Must be one of: {sorted(VALID_COMBO_TYPES)}")

    if ctype == "WHEEL":
        formatted_legs = [format_wheel_leg(leg) for leg in legs]
        if any(not leg_str for leg_str in formatted_legs):
            return ""
        return "-".join(formatted_legs)

    elif ctype == "BOX":
        pool = sort_pgms(legs[0] if isinstance(legs[0], (list, set, tuple)) else legs)
        return format_box_group(pool) if len(pool) >= 2 else ""

    elif ctype == "KEY":
        if len(legs) != 2:
            raise ValueError("KEY requires 2 elements: (key_runner_list, contenders_list)")
        key_runners = sort_pgms(legs[0])
        contenders = [p for p in sort_pgms(legs[1]) if p not in key_runners]
        if len(key_runners) != 1 or len(contenders) < 2:
            return ""
        return f"{key_runners[0]}-{format_box_group(contenders)}"

    elif ctype == "KEY-BOX":
        if len(legs) != 2:
            raise ValueError("KEY-BOX requires 2 elements: (key_runner_list, contenders_list)")
        key_runners = sort_pgms(legs[0])
        contenders = [p for p in sort_pgms(legs[1]) if p not in key_runners]
        if len(key_runners) != 1 or len(contenders) < 2:
            return ""
        return f"[{key_runners[0]}]-{format_box_group(contenders)}"

    elif ctype == "POWER-BOX":
        if len(legs) < 2:
            raise ValueError("POWER-BOX requires at least 2 groups: [key]-[group2]-[group3]...")
        key_runners = sort_pgms(legs[0])
        if len(key_runners) != 1:
            return ""
        formatted_groups = [f"[{key_runners[0]}]"]
        for grp in legs[1:]:
            g_sorted = sort_pgms(grp)
            if not g_sorted:
                return ""
            formatted_groups.append(format_box_group(g_sorted))
        return "-".join(formatted_groups)


def count_valid_trifecta_combos(leg1, leg2, leg3):
    """Counts unique ordered (i, j, k) combinations where i != j != k."""
    l1 = set(sort_pgms(leg1))
    l2 = set(sort_pgms(leg2))
    l3 = set(sort_pgms(leg3))
    return sum(1 for i in l1 for j in l2 if j != i for k in l3 if k != i and k != j)


def normalize_grades_dataframe(df_input, default_track=None, default_date=None):
    """
    Normalizes either a raw picks_and_tags CSV (Race, Post, Grade, Modifiers)
    or a pre-normalized grades DataFrame (track, race_date, scratched, race, pgm, bucket, modifiers).
    """
    df = df_input.copy()

    if "race" not in df.columns and "Race" in df.columns:
        df["race"] = df["Race"]
    if "pgm" not in df.columns and "Post" in df.columns:
        df["pgm"] = df["Post"]
    if "bucket" not in df.columns and "Grade" in df.columns:
        df["bucket"] = df["Grade"]

    if "modifiers" not in df.columns:
        if "Modifiers" in df.columns:
            df["modifiers"] = df["Modifiers"].fillna("")
        elif "Full_Tag" in df.columns:
            df["modifiers"] = df["Full_Tag"].fillna("")
        else:
            df["modifiers"] = ""

    if "track" not in df.columns:
        df["track"] = default_track if default_track else "IND"
    if "race_date" not in df.columns:
        df["race_date"] = str(default_date) if default_date else ""
    if "scratched" not in df.columns:
        df["scratched"] = 0

    df["race"] = pd.to_numeric(df["race"], errors="coerce")
    df = df[df["race"].notnull()].copy()
    df["race"] = df["race"].astype(int)

    df["scratched"] = pd.to_numeric(df["scratched"], errors="coerce").fillna(0).astype(int)
    df["race_date_norm"] = df["race_date"].apply(normalize_race_date)
    df["track_clean"] = df["track"].astype(str).str.strip().str.upper()
    df["pgm_str"] = df["pgm"].apply(clean_pgm)
    df["bucket"] = df["bucket"].fillna("").astype(str).str.strip().str.upper()
    df["has_w"] = df["modifiers"].fillna("").astype(str).str.upper().str.contains(r"\[W\]")

    return df


def resolve_track_keys(track_code):
    """Returns all valid Brisnet and AmTote aliases for a given track code."""
    raw = str(track_code).strip().upper()
    amtote = TRACK_CODE_MAP.get(raw, raw)
    keys = {raw, amtote}
    for k, v in TRACK_CODE_MAP.items():
        if v == amtote:
            keys.add(k)
    return keys, amtote


def export_trifecta_playbooks_csv(
    df_grades,
    output_csv_path="trifecta_wagers.csv",
    track="IND",
    race_date="2026-11-03",
    playbook="ALL_THREE_DISJOINTED",
    races=None,
    base_amount=0.50,
    bet_code="TRIFECTA",
    min_odds="",
    mtp="",
    write_mode="w"
):
    """
    Builds Expressbet / 1/ST BET batch-upload TRIFECTA WHEEL tickets with mapped
    AmTote track codes (e.g. IND -> IJN) and strict YYYY-MM-DD date formatting.

    Playbook Modes:
      - 'PLAYBOOK_1' or 'A_W_CD':
          1st: AA (A+ or A) | 2nd: All non-F [W] runners | 3rd: C + D
      - 'PLAYBOOK_2' or 'DISJOINTED_TRIO':
          Three 100% mutually exclusive D-Bomb templates:
            * Template 1 (AA / B / D): 1st: AA | 2nd: B  | 3rd: D
            * Template 2 (AA / D / B): 1st: AA | 2nd: D  | 3rd: B (Bomb Sandwich)
            * Template 3 (B / AA / D): 1st: B  | 2nd: AA | 3rd: D (B-Upset)
      - 'PLAYBOOK_3' or 'A_AB_CD':
          1st: AA (A+ or A) | 2nd: AA + B | 3rd: C + D
      - 'ALL_THREE_DISJOINTED' (Recommended default when combining all 3):
          Merges Playbooks 1, 2, and 3 without duplicate tickets on the same combination.
      - 'ALL_THREE_RAW' or 'ALL':
          Exports all rows from Playbook 1, Playbook 2, and Playbook 3 sequentially (allows overlaps).
    """
    valid_bet_code = normalize_bet_type(bet_code)
    target_date = normalize_race_date(race_date)
    valid_track_keys, upload_track = resolve_track_keys(track)

    df = normalize_grades_dataframe(df_grades, default_track=track, default_date=target_date)
    df = df[
        (df["track_clean"].isin(valid_track_keys)) &
        (df["race_date_norm"] == target_date) &
        (df["scratched"] == 0) &
        (df["pgm_str"] != "")
    ].copy()

    if races is None:
        races = sorted(df["race"].unique().tolist())
    else:
        races = [int(r) for r in races]

    pb_key = str(playbook).upper().strip()
    csv_rows = []

    for r_num in races:
        sub = df[df["race"] == r_num]
        if sub.empty:
            continue

        ap_pgms = sort_pgms(sub[sub["bucket"] == "A+"]["pgm_str"])
        a_pgms  = sort_pgms(sub[sub["bucket"] == "A"]["pgm_str"])
        b_pgms  = sort_pgms(sub[sub["bucket"] == "B"]["pgm_str"])
        c_pgms  = sort_pgms(sub[sub["bucket"] == "C"]["pgm_str"])
        d_pgms  = sort_pgms(sub[sub["bucket"] == "D"]["pgm_str"])

        # Single A+ card anchor when present; otherwise use A tier
        aa_pgms = ap_pgms if len(ap_pgms) > 0 else a_pgms
        ab_pgms = sort_pgms(aa_pgms + b_pgms)
        cd_pgms = sort_pgms(c_pgms + d_pgms)
        w_pgms  = sort_pgms(sub[(sub["bucket"].isin(["A+", "A", "B", "C", "D"])) & (sub["has_w"])]["pgm_str"])
        w_cd_only = sort_pgms([p for p in w_pgms if p not in set(ab_pgms)])

        # Skip ungraded races
        if not aa_pgms and not b_pgms and not cd_pgms:
            continue

        race_pool_specs = []
        if pb_key in ("PLAYBOOK_1", "A_W_CD"):
            race_pool_specs.append((aa_pgms, w_pgms, cd_pgms))
        elif pb_key in ("PLAYBOOK_2", "DISJOINTED_TRIO"):
            race_pool_specs.append((aa_pgms, b_pgms, d_pgms))
            race_pool_specs.append((aa_pgms, d_pgms, b_pgms))
            race_pool_specs.append((b_pgms, aa_pgms, d_pgms))
        elif pb_key in ("PLAYBOOK_3", "A_AB_CD"):
            race_pool_specs.append((aa_pgms, ab_pgms, cd_pgms))
        elif pb_key in ("ALL_THREE_DISJOINTED", "COMBINED_DISJOINTED"):
            # Disjointed union of Playbook 1, Playbook 2, and Playbook 3 (zero duplicate combos):
            # 1) Playbook 3 covers AA / AA+B / C+D (which already includes PB2's AA/B/D and PB1's AA/W_in_AB/CD)
            race_pool_specs.append((aa_pgms, ab_pgms, cd_pgms))
            # 2) Playbook 1's remaining [W] horses in C or D: AA / W_in_CD / C+D
            race_pool_specs.append((aa_pgms, w_cd_only, cd_pgms))
            # 3) Playbook 2's Bomb Sandwich: AA / D / B
            race_pool_specs.append((aa_pgms, d_pgms, b_pgms))
            # 4) Playbook 2's B-Upset: B / AA / D
            race_pool_specs.append((b_pgms, aa_pgms, d_pgms))
        elif pb_key in ("ALL_THREE_RAW", "ALL"):
            # Raw sequential export (preserves intentional double/triple weighting across playbooks)
            race_pool_specs.append((aa_pgms, w_pgms, cd_pgms))
            race_pool_specs.append((aa_pgms, b_pgms, d_pgms))
            race_pool_specs.append((aa_pgms, d_pgms, b_pgms))
            race_pool_specs.append((b_pgms, aa_pgms, d_pgms))
            race_pool_specs.append((aa_pgms, ab_pgms, cd_pgms))
        else:
            raise ValueError(f"Unknown playbook mode: {playbook}")

        seen_combo_strings = set()
        for (leg1, leg2, leg3) in race_pool_specs:
            if count_valid_trifecta_combos(leg1, leg2, leg3) > 0:
                combo_str = format_wager_combination([leg1, leg2, leg3], combo_type="WHEEL")
                if pb_key in ("ALL_THREE_DISJOINTED", "COMBINED_DISJOINTED") and combo_str in seen_combo_strings:
                    continue
                seen_combo_strings.add(combo_str)

                csv_rows.append([
                    target_date,
                    str(upload_track),
                    str(int(r_num)),
                    valid_bet_code,
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


if __name__ == "__main__":
    input_csv = "IND_20261103_picks_and_tags.csv"
    if os.path.exists(input_csv):
        raw_df = pd.read_csv(input_csv)
        track_code = "IND"  # Mapped automatically to 'IJN' via TRACK_CODE_MAP
        r_date = "2026-11-03"

        export_trifecta_playbooks_csv(
            raw_df,
            "IJN_20261103_trifecta_playbook1_A_W_CD.csv",
            track=track_code,
            race_date=r_date,
            playbook="PLAYBOOK_1"
        )
        export_trifecta_playbooks_csv(
            raw_df,
            "IJN_20261103_trifecta_playbook2_disjointed_trio.csv",
            track=track_code,
            race_date=r_date,
            playbook="PLAYBOOK_2"
        )
        export_trifecta_playbooks_csv(
            raw_df,
            "IJN_20261103_trifecta_playbook3_A_AB_CD.csv",
            track=track_code,
            race_date=r_date,
            playbook="PLAYBOOK_3"
        )
        export_trifecta_playbooks_csv(
            raw_df,
            "IJN_20261103_trifecta_all_three_disjointed.csv",
            track=track_code,
            race_date=r_date,
            playbook="ALL_THREE_DISJOINTED"
        )