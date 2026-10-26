import csv
import glob
import os
import argparse

def parse_race_range(range_str):
    if not range_str:
        return None
    start, end = map(int, range_str.split('-'))
    return list(range(start, end + 1))

def auto_detect_input_file(default_name="raw_card.csv"):
    if os.path.exists(default_name):
        return default_name
    candidates = [
        f for f in glob.glob("*")
        if f.lower().endswith((".csv", ".drf", ".txt"))
        and not f.startswith("distilled_card")
    ]
    return candidates[0] if candidates else default_name

def format_work_dist(yds_str):
    """Converts Brisnet workout yards into furlongs (e.g., 880 -> 4f)."""
    if not yds_str or not yds_str.isdigit():
        return "N/A"
    yds = int(yds_str)
    if yds % 220 == 0:
        return f"{yds // 220}f"
    return f"{yds}y"

def get_fts_trainer_win_pct(row):
    """
    Extracts Trainer 1st-Time Starter Win %.
    Checks Field #224 first, then scans Brisnet Key Trainer Stat blocks (indices 1336-1365)
    for the '1st time str' category.
    """
    for idx in (223, 224):
        if idx < len(row):
            val = row[idx].strip().replace('%', '')
            try:
                float(val)
                if len(val) <= 6 and not val.isdigit():
                    return f"{float(val):.0f}%"
            except ValueError:
                pass

    for idx in range(1336, min(1366, len(row) - 2), 5):
        if "1st time" in row[idx].lower():
            win_pct = row[idx + 2].strip()
            try:
                return f"{float(win_pct):.0f}%"
            except ValueError:
                pass
    return "0%"

def clean_token(val, default="N/A"):
    """Ensures no cell is ever exported as an empty string."""
    val = str(val).strip() if val is not None else ""
    return val if val != "" else default

def main():
    parser = argparse.ArgumentParser(description="Distill raw Brisnet PP text into a single bulletproof handicapping CSV.")
    parser.add_argument("--file", default=None, help="Input Brisnet text/CSV file (auto-detects if omitted)")
    parser.add_argument("--races", default=None, help="Race range (e.g., '1-9'). Omit for all races.")
    parser.add_argument("--output", default="distilled_card.csv", help="Output CSV filename")
    args, _ = parser.parse_known_args()

    input_file = args.file or auto_detect_input_file()
    target_races = parse_race_range(args.races)

    headers = [
        "Track", "Date", "Race", "Post", "Prog", "Horse", "ML_Odds", "Weight",
        "Today_Dist_Yds", "Today_Surf", "Today_Class", "Today_Purse",
        "Jockey", "Jock_Sts", "Jock_Win", "Jock_Win_Pct",
        "Trainer", "Trn_Sts", "Trn_Win", "Trn_Win_Pct",
        "Sire", "Dam_Sire",
        "Run_Style", "QSP", "Days_Off", "Prime_Power",
        "Par_E1", "Par_E2", "Par_LP", "Par_Spd",
        "Last_Date", "Last_Track", "Last_Dist_Yds", "Last_Surface", "Last_Cond", "Last_Purse", "Last_Comment",
        "Last_E1", "Last_E2", "Last_LP", "Last_Fig", "Fig_2Back", "Fig_3Back",
        "Work_1_Date", "Work_1_Time", "Work_1_Track", "Work_1_Dist"
    ]

    rows_out = [headers]

    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 700:
                continue

            race_num = row[2].strip()
            if target_races and int(race_num) not in target_races:
                continue

            # Detect whether horse has Past Performances (Starts at index 255 in standard DRF)
            d_idx = next((i for i in range(200, 350) if len(row[i]) == 8 and row[i].startswith("20") and row[i].isdigit()), -1)
            is_fts = (d_idx == -1 or (d_idx + 610) >= len(row))

            # PHASE 1: FIXED ABSOLUTE INDICES (Brisnet Single-File Spec)
            track = clean_token(row[0])
            date = clean_token(row[1])
            post = clean_token(row[3])
            today_dist = clean_token(row[5])
            today_surf = clean_token(row[6])
            today_class = clean_token(row[10])
            today_purse = clean_token(row[11])

            trainer = clean_token(row[27])
            trn_sts = clean_token(row[28], "0")
            trn_win = clean_token(row[29], "0")
            trn_pct = f"{(int(trn_win)/int(trn_sts)*100):.1f}%" if trn_sts.isdigit() and int(trn_sts) > 0 and trn_win.isdigit() else "0.0%"

            jockey = clean_token(row[32])
            jock_sts = clean_token(row[34], "0")
            jock_win = clean_token(row[35], "0")
            jock_pct = f"{(int(jock_win)/int(jock_sts)*100):.1f}%" if jock_sts.isdigit() and int(jock_sts) > 0 and jock_win.isdigit() else "0.0%"

            prog = clean_token(row[42])
            ml_odds = clean_token(row[43])
            horse = clean_token(row[44])
            weight = clean_token(row[50])
            sire = clean_token(row[51])
            dam_sire = clean_token(row[54])

            w1_date = clean_token(row[101], "N/A")
            w1_time = clean_token(row[113], "N/A")
            w1_track = clean_token(row[125], "N/A")
            w1_dist = clean_token(row[137], "N/A")
            w1_desc = row[161].strip() if len(row) > 161 else ""

            raw_style = row[209].strip()
            run_style = "FTS" if is_fts else (raw_style if raw_style and raw_style != "NA" else "N/A")
            qsp = clean_token(row[210], "0")

            # Today's Par Figures: Sprints (< 1760 yds) use 2f/4f (213/214); Routes (>= 1760 yds) use 4f/6f (214/215)
            is_today_route = today_dist.isdigit() and int(today_dist) >= 1760
            par_e1 = clean_token(row[214] if is_today_route else row[213], "N/A")
            par_e2 = clean_token(row[215] if is_today_route else row[214], "N/A")
            par_spd = clean_token(row[216], "N/A")
            par_lp = clean_token(row[217], "N/A")

            raw_days = row[223].strip()
            days_off = "FTS" if is_fts else (raw_days if raw_days else "N/A")

            raw_pp = row[250].strip()
            if is_fts or not raw_pp or raw_pp in ("0", "0.0", "0.00"):
                prime_power = "FTS_0" if is_fts else "N/A"
            else:
                prime_power = raw_pp

            base_info = [
                track, date, race_num, post, prog, horse, ml_odds, weight,
                today_dist, today_surf, today_class, today_purse,
                jockey, jock_sts, jock_win, jock_pct,
                trainer, trn_sts, trn_win, trn_pct,
                sire, dam_sire,
                run_style, qsp, days_off, prime_power,
                par_e1, par_e2, par_lp, par_spd
            ]
            work_info = [w1_date, w1_time, w1_track, w1_dist]

            # PHASE 2: DYNAMIC PP INDICES OR EXPLICIT FTS TOKENS
            if is_fts:
                fts_win_pct = get_fts_trainer_win_pct(row)
                work_str = f"{format_work_dist(w1_dist)}_{w1_time}{w1_desc}" if w1_time != "N/A" else "No_Work"
                fts_comment = f"1st_Str_Win:{fts_win_pct}_Work:{work_str}"

                fts_pp_cols = [
                    "FTS", "FTS", "FTS", "FTS", "FTS", "FTS", fts_comment,
                    "FTS", "FTS", "FTS", "FTS", "FTS", "FTS"
                ]
                rows_out.append(base_info + fts_pp_cols + work_info)
                continue

            def get_val(offset, default="N/A"):
                if (d_idx + offset) < len(row):
                    val = row[d_idx + offset].strip()
                    if val != "":
                        return val
                return default

            last_date = get_val(0)
            last_track = get_val(20)
            last_cond = get_val(50)       # Track Condition (FT, FM, SY, etc.)
            last_dist = get_val(60)       # Last Distance in yards
            last_surface = get_val(70)    # Surface (D, T, A, etc.)
            last_comment = get_val(140, "No_Comment")
            last_purse = get_val(300)

            # Last Race Pace Calls: Sprints (< 1760 yds) use 2f/4f (510/520); Routes (>= 1760 yds) use 4f/6f (520/530)
            is_last_route = last_dist.isdigit() and int(last_dist) >= 1760
            last_e1 = get_val(520) if is_last_route else get_val(510)
            last_e2 = get_val(530) if is_last_route else get_val(520)
            last_lp = get_val(560)
            last_fig = get_val(590)
            fig_2back = get_val(591)
            fig_3back = get_val(592)

            exp_row = base_info + [
                last_date, last_track, last_dist, last_surface, last_cond, last_purse, last_comment,
                last_e1, last_e2, last_lp, last_fig, fig_2back, fig_3back
            ] + work_info

            rows_out.append(exp_row)

    with open(args.output, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(rows_out)

    print(f"Exported {len(rows_out)-1} total entries to {args.output}")

if __name__ == "__main__":
    main()