This technical specification provides Pete with the exact relational schema, ETL pre-processing formulas, combinatorial cost-to-capture algorithms, and diagnostic SQL views required to build the handicapping and pool-efficiency database.

1. Pipeline Architecture & Order of Operations
The ingestion pipeline must execute in four sequential stages per card so that scratches and post-time odds properly propagate into rank and pool calculations:

Stage 1 (Pre-Race Base Ingestion): Ingest the 47-column *_distilled.csv file and parse the <retail_herd_radar> and <track_conditions_and_changes> blocks.
Stage 2 (Scratch Filter & Dynamic Re-Ranking): Set is_scratched = 1 for all scratched runners (e.g., Race 6 #9 Couple Beers Deep at MVR) before computing race-level active_starters, active_qsp_sum, pp_rank, last_e1_rank, last_lp_rank, or [E] / [W] modifiers.
Stage 3 (Dossier & Ticket Logging): Store the mutually exclusive primary grade (A+, A, B, C, D, F-, F), orthogonal tags (tag_w, tag_e, tag_retail_trap, tag_unseen_retail), and the disjoint (T_i \cap T_j = \emptyset) ADW ticket matrices.
Stage 4 (Post-Race Chart Settlement): Ingest official Equibase chart results (closing tote odds, finish positions, $2.00 WPS mutuels, and $1.00-normalized vertical/horizontal pool payouts) to compute Closing Line Value (clv_ratio), Parlay Leverage Ratios, and Combinatorial Cost-to-Capture.
2. Relational Database Schema (PostgreSQL / SQLite DDL)
-- TABLE 1: RACES (Macro Topology, Par Figures & Pool Carryovers)
CREATE TABLE races (
    race_id TEXT PRIMARY KEY,            -- Format: 'YYYYMMDD_TRK_RR' (e.g., '20261026_MVR_01')
    track_code TEXT NOT NULL,            -- 'PRX', 'IND', 'MVR', 'FLX'
    race_date INTEGER NOT NULL,          -- YYYYMMDD (e.g., 20261026)
    race_num INTEGER NOT NULL,
    today_dist_yds INTEGER NOT NULL,     -- e.g., 1210, 1320, 1760
    today_surf TEXT NOT NULL,            -- 'D', 'T', 'A'
    today_class TEXT NOT NULL,
    today_purse INTEGER NOT NULL,
    track_cond TEXT DEFAULT 'FT',        -- 'FT', 'GD', 'MY', 'SY', 'FM'
    par_e1 INTEGER,
    par_e2 INTEGER,
    par_lp INTEGER,
    par_spd INTEGER,
    active_starters INTEGER NOT NULL,    -- Count where is_scratched = 0
    active_qsp_sum INTEGER NOT NULL,     -- SUM(qsp) where is_scratched = 0
    pace_scenario TEXT NOT NULL,         -- 'Lone_Speed', 'Honest', 'Meltdown'
    est_dead_pool_share REAL,            -- Sum of implied prob for [F-] and [RETAIL TRAP FADE]
    actual_e1_par_delta REAL             -- Post-race verification of pace collapse
);

-- TABLE 2: RUNNERS (47 CSV Columns + Computed Deltas + Herd Radar + Grades + Results)
CREATE TABLE runners (
    runner_id TEXT PRIMARY KEY,          -- Format: 'YYYYMMDD_TRK_RR_PP' (Prog # padded)
    race_id TEXT NOT NULL REFERENCES races(race_id),
    post_pos INTEGER NOT NULL,
    prog_num INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    is_scratched INTEGER DEFAULT 0,      -- 0 = Active, 1 = Scratched
    is_fts INTEGER DEFAULT 0,            -- 1 if Prime_Power == 0/NaN and Last_Fig == 0/NaN

    -- Raw & Cleaned CSV Features
    ml_odds REAL NOT NULL,
    ml_implied_prob REAL NOT NULL,       -- 1.0 / (ml_odds + 1.0)
    weight INTEGER,
    jockey TEXT,
    jock_sts INTEGER,
    jock_win INTEGER,
    jock_win_pct REAL,                   -- Cleaned float (e.g., '20.0%' -> 20.0)
    trainer TEXT,
    trn_sts INTEGER,
    trn_win INTEGER,
    trn_win_pct REAL,                    -- Cleaned float (e.g., '50.0%' -> 50.0)
    sire TEXT,
    dam_sire TEXT,
    run_style TEXT,                      -- 'E', 'E/P', 'P', 'S', 'NA'
    qsp INTEGER,
    days_off INTEGER,
    prime_power REAL,

    -- Last Race & Back Figures
    last_date INTEGER,
    last_track TEXT,
    last_dist_yds INTEGER,
    last_surface TEXT,
    last_cond TEXT,
    last_purse INTEGER,
    last_comment TEXT,
    last_e1 INTEGER,
    last_e2 INTEGER,
    last_lp INTEGER,
    last_fig INTEGER,
    fig_2back REAL,
    fig_3back REAL,

    -- Normalized Workout Features (Fixes Negative Float Encoding on Bullets)
    work_1_date INTEGER,
    work_1_track TEXT,
    work_1_dist INTEGER,
    is_bullet_work INTEGER,              -- 1 if raw Work_1_Time < 0 else 0
    work_1_seconds REAL,                 -- ABS(Work_1_Time)
    work_1_sec_per_furlong REAL,         -- ABS(Work_1_Time) / (Work_1_Dist / 220.0)
    work_1_days_ago INTEGER,             -- Calendar days between race_date and work_1_date

    -- Post-Scratch Computed Ranks & Feature Gate Deltas
    pp_rank INTEGER,                     -- Active field rank (1 = highest Prime_Power)
    pp_delta_to_1st REAL,                -- prime_power - MAX(active prime_power in race)
    pp_lead_over_2nd REAL,               -- Only for pp_rank = 1: (Rank 1 PP - Rank 2 PP)
    last_fig_delta REAL,                 -- last_fig - par_spd
    best_3back_delta REAL,               -- MAX(last_fig, fig_2back, fig_3back) - par_spd
    fig_within_2_of_top INTEGER,         -- 1 if MAX(last_fig, fig_2back, fig_3back) >= Race_Max_Fig - 2
    last_e1_rank INTEGER,                -- Active field rank (1 = highest Last_E1)
    last_e1_delta REAL,                  -- last_e1 - par_e1
    e1_lead_over_ep REAL,                -- Lead over next fastest active E or E/P runner
    last_e2_delta REAL,                  -- last_e2 - par_e2
    last_lp_rank INTEGER,                -- Active field rank (1 = highest Last_LP)
    last_lp_percentile REAL,             -- (active_starters - last_lp_rank) / active_starters
    last_lp_delta REAL,                  -- last_lp - par_lp
    purse_ratio REAL,                    -- today_purse * 1.0 / last_purse (< 0.75 = class drop)
    dist_delta_yds INTEGER,              -- today_dist_yds - last_dist_yds
    is_turf_to_dirt INTEGER,             -- 1 if last_surface = 'T' and today_surf = 'D'
    has_trouble_comment INTEGER,         -- Regex match: wide|4w|5w|6w|steadied|blocked|stumbled|bmp

    -- Retail Herd Radar Overlay (Parsed from DRF Text Block)
    drf_pick_rank INTEGER DEFAULT 0,     -- 1, 2, 3, or 0 (omitted)
    beyer_edge_rank INTEGER DEFAULT 0,   -- 1, 2, 3, or 0
    consensus_pts INTEGER DEFAULT 0,     -- Panel points (e.g., 15 for Best Bet unanimity)
    consensus_rank INTEGER DEFAULT 0,    -- 1, 2, 3, or 0
    is_drf_best_bet INTEGER DEFAULT 0,   -- 1 if listed in DRF Best Bets
    is_live_longshot INTEGER DEFAULT 0,  -- 1 if listed as DRF Live Longshot
    beyer_1 INTEGER,                     -- Most recent Beyer from DRF text
    beyer_2 INTEGER,
    beyer_3 INTEGER,
    bris_vs_beyer_delta REAL,            -- (last_fig - par_spd) - (beyer_1 - mean_race_beyer)
    eq_change TEXT,                      -- 'b-on', 'b-off', None

    -- Engine Classification Output
    primary_grade TEXT NOT NULL,         -- 'A+', 'A', 'B', 'C', 'D', 'F-', 'F'
    tag_w INTEGER DEFAULT 0,             -- 1 = Wise Guy [W]
    tag_e INTEGER DEFAULT 0,             -- 1 = Controlling Speed [E]
    tag_retail_trap INTEGER DEFAULT 0,   -- 1 = [RETAIL TRAP FADE]
    tag_unseen_retail INTEGER DEFAULT 0, -- 1 = [UNSEEN BY RETAIL] (drf_pick_rank = 0 & consensus_rank = 0)
    est_true_prob_p REAL,                -- Model conditional win prob p_i

    -- Post-Race Official Chart Results
    final_tote_odds REAL,
    final_implied_prob_q REAL,           -- (1/(final_tote_odds+1)) / SUM_race(1/(final_tote_odds+1))
    clv_ratio REAL,                      -- (1/(final_tote_odds+1)) / (1/(ml_odds+1)) (>1.0 = steamed)
    finish_pos INTEGER,
    beaten_lengths REAL,
    win_pay REAL DEFAULT 0.0,            -- Official $2.00 Win mutuel
    place_pay REAL DEFAULT 0.0,          -- Official $2.00 Place mutuel
    show_pay REAL DEFAULT 0.0,           -- Official $2.00 Show mutuel
    hit_1st INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END) VIRTUAL,
    hit_2nd INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 2 THEN 1 ELSE 0 END) VIRTUAL,
    hit_3rd INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 3 THEN 1 ELSE 0 END) VIRTUAL,
    hit_4th INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 4 THEN 1 ELSE 0 END) VIRTUAL
);

-- TABLE 3: POOLS (Vertical & Horizontal Theoretical Maximums & Efficiency Ratios)
CREATE TABLE pools (
    pool_id TEXT PRIMARY KEY,            -- Format: '20261026_MVR_R04_P5' or '20261026_IND_R07_TRI'
    track_code TEXT NOT NULL,
    race_date INTEGER NOT NULL,
    anchor_race_id TEXT NOT NULL REFERENCES races(race_id),
    pool_type TEXT NOT NULL,             -- 'EXA', 'TRI', 'SUP', 'DD', 'P3', 'P4', 'P5', 'P6'
    num_legs INTEGER NOT NULL,           -- 1 for EXA/TRI/SUP; 2 for DD; 4 for P4; 5 for P5
    takeout_rate REAL NOT NULL,          -- Track statutory takeout (e.g., 0.15 to 0.22)
    carryover_pct REAL DEFAULT 0.0,      -- Parsed from DRF header (e.g., 0.50 for MVR P5)
    winning_prog_str TEXT,               -- e.g., '1-3-7-8-6'
    winning_grade_str TEXT,              -- e.g., 'A[W] / A[W] / B[W] / A[E] / A[W]'
    payout_1d_norm REAL,                 -- Official payout normalized to a $1.00 base wager
    theo_win_parlay_1d REAL,             -- Horizontal only: PRODUCT(final_tote_odds_k + 1.0)
    parlay_leverage_ratio REAL,          -- payout_1d_norm / theo_win_parlay_1d (>1.0 = pool overlay)
    captured_by_playable INTEGER,        -- 1 if zero 'F' or 'F-' runners in winning sequence
    lowest_tier_needed TEXT,             -- 'A+', 'A', 'B', 'C', 'D', or 'F_MISS'
    combos_to_capture INTEGER,           -- Total combinations required to cover down to lowest_tier_needed
    theo_tier_yield REAL                 -- payout_1d_norm / combos_to_capture
);

-- TABLE 4: TICKETS (Disjoint Zero-Overlap ADW Slip Execution Log)
CREATE TABLE tickets (
    ticket_id TEXT PRIMARY KEY,          -- Format: '20261026_FLX_P5_SLIP1'
    pool_id TEXT NOT NULL REFERENCES pools(pool_id),
    slip_tier TEXT NOT NULL,             -- 'Slip_1_Primary', 'Slip_2_Backup', 'Slip_3_Saver'
    matrix_definition TEXT NOT NULL,     -- e.g., '1,4,6 / 1,3,4,5 / 1,2,3,4,5 / 2 / 1,3,4,5,8'
    base_wager REAL NOT NULL,            -- e.g., 0.50, 1.00, 2.00
    unique_combos INTEGER NOT NULL,
    ticket_cost REAL NOT NULL,           -- base_wager * unique_combos
    is_winner INTEGER DEFAULT 0,
    gross_return REAL DEFAULT 0.0
);
3. Five Hard-Coded ETL Rules Pete Needs in Python
Negative Float Bullet Workout Decoder: In the distilled Brisnet CSV, bullet workouts are stored as negative floats (e.g., Work_1_Time = -36.0 on MVR R7 #5 Mr Loooch and -48.2 on R3 #2 Angel Wings). Pete’s parser must transform Work_1_Time before running any math:
df['is_bullet_work'] = (df['Work_1_Time'] < 0).astype(int)
df['work_1_seconds'] = df['Work_1_Time'].abs()
df['work_1_sec_per_furlong'] = df['work_1_seconds'] / (df['Work_1_Dist'] / 220.0)
df['work_1_days_ago'] = (
    pd.to_datetime(df['Date'].astype(str), format='%Y%m%d') -
    pd.to_datetime(df['Work_1_Date'].astype(str), format='%Y%m%d')
).dt.days
Early-Meet Connection Sample-Size Filter (jock_sts & trn_sts): As shown in MVR_20261026_distilled.csv, early-meet starts average just 2.68 for jockeys and 1.46 for trainers, creating artificial 0.0% and 100.0% rates. Pete should apply a Bayesian shrinkage or threshold gate in Python before passing connection percentages to the grading rules:
# Require >= 15 Jockey starts and >= 10 Trainer starts before triggering connection rules
df['valid_jock_pct'] = np.where(df['Jock_Sts'] >= 15, df['jock_win_pct'], np.nan)
df['valid_trn_pct']  = np.where(df['Trn_Sts'] >= 10, df['trn_win_pct'], np.nan)
Outlier & Missing Speed Figure Guardrail: In MVR_20261026_distilled.csv, beaten-off runners can post negative speed figures (e.g., R3 #3 Lady Loves Money has Last_Fig = -3), while First-Time Starters have 0 or NaN. Flag is_fts = 1 strictly when Prime_Power == 0 (or NaN) AND Last_Fig.isna(); for experienced starters with negative or < 20 speed figures, cap last_fig_delta at -35.0 so extreme outliers do not distort linear regression or Z-score diagnostics.
Cumulative Tier Depth & Combinatorial Cost-to-Capture (combos_to_capture): To evaluate whether a pool is profitable at each tier depth, define the cumulative tier sets S_1 = \{A+\}, S_2 = \{A+, A\}, S_3 = \{A+, A, B\}, S_4 = \{A+, A, B, C\}, S_5 = \{A+, A, B, C, D\}. When a pool finishes:
Identify the smallest set S_m that contains all winning runners in that pool (lowest_tier_needed). If any winner in the sequence is graded F or F-, set captured_by_playable = 0 and lowest_tier_needed = 'F_MISS'.
For Horizontal Pools (K legs): If n_{k,m} is the count of runners in leg k belonging to S_m, then: \text{combos\_to\_capture} = \prod_{k=1}^{K} n_{k,m}
For Vertical Box Pools (r slots: r=2 Exacta, r=3 Trifecta, r=4 Superfecta): If N_m is the count of runners in that race belonging to S_m, the exact box cost to capture is the falling factorial: \text{combos\_to\_capture} = \frac{N_m!}{(N_m - r)!}
For Tier-Keyed Verticals (1st place restricted to S_{\text{top}} = \{A+, A\}, 2nd–rth places open to S_m): \text{combos\_to\_capture} = \vert{}S_{\text{top}}\vert{} \times \frac{(N_m - 1)!}{(N_m - r)!}
Disjoint Ticket Verification Assertion: Whenever generating multi-ticket ADW slips (tickets table), Pete’s ticket builder should run a fast set-intersection assertion across the Cartesian products of each slip before outputting:
from itertools import product
slips = [set(product(*leg_lists)) for leg_lists in ticket_definitions]
assert sum(len(s) for s in slips) == len(set.union(*slips)), "OVERLAP DETECTED!"
4. Three Diagnostic SQL Queries for Pete to Wire into Your Dashboard
Query A: Grade & Modifier Calibration Audit (WPS ROI + Exact Exotic Slot Impact Value)
Reveals which grades (A+ through F-) and modifiers ([W], [E], [UNSEEN BY RETAIL]) are beating the takeout in Win, Place, Show, and 2nd/3rd/4th Trifecta/Superfecta slots:

SELECT 
    primary_grade,
    tag_w,
    tag_e,
    tag_unseen_retail,
    COUNT(*) AS starters,
    ROUND(AVG(hit_1st) * 100, 1) AS win_pct,
    ROUND(SUM(hit_1st) / SUM(est_true_prob_p), 2) AS actual_vs_expected_ae,
    ROUND(AVG(clv_ratio), 3) AS avg_clv_ratio,
    ROUND(SUM(win_pay) / (COUNT(*) * 2.0), 3) AS flat_win_roi,
    ROUND(SUM(place_pay) / (COUNT(*) * 2.0), 3) AS flat_place_roi,
    ROUND(SUM(show_pay) / (COUNT(*) * 2.0), 3) AS flat_show_roi,
    ROUND(AVG(hit_1st + hit_2nd + hit_3rd) * 100, 1) AS top3_tri_pct,
    ROUND(AVG(hit_1st + hit_2nd + hit_3rd + hit_4th) * 100, 1) AS top4_super_pct
FROM runners
WHERE is_scratched = 0 AND finish_pos IS NOT NULL
GROUP BY primary_grade, tag_w, tag_e, tag_unseen_retail
ORDER BY flat_win_roi DESC;
Query B: Pool Efficiency & Theoretical Tier Yield Ranking
Identifies which pool (P5, P4, DD, TRI, SUP) delivers the highest capture rate, the highest Parlay Leverage over Win odds, and the highest theoretical return per combination at each tier cut-off:

SELECT 
    pool_type,
    lowest_tier_needed,
    COUNT(*) AS total_pools,
    ROUND(AVG(captured_by_playable) * 100, 1) AS playable_capture_pct,
    ROUND(AVG(payout_1d_norm), 2) AS avg_1d_payout,
    ROUND(AVG(parlay_leverage_ratio), 2) AS avg_parlay_leverage,
    ROUND(AVG(combos_to_capture), 1) AS avg_combos_needed,
    ROUND(SUM(payout_1d_norm) / SUM(combos_to_capture), 3) AS theoretical_tier_roi
FROM pools
GROUP BY pool_type, lowest_tier_needed
ORDER BY pool_type, theoretical_tier_roi DESC;
Query C: Parameter Leak Detector (Auditing [RETAIL TRAP FADE] Winners & Failed A[E] Locks)
Flags the exact CSV thresholds (active_qsp_sum, purse_ratio, e1_lead_over_ep, days_off) when a faded favorite wins or a lone-speed [E] gets cooked:

SELECT 
    r.track_code,
    r.today_dist_yds,
    r.pace_scenario,
    n.primary_grade,
    n.tag_e,
    n.tag_retail_trap,
    COUNT(*) AS sample_size,
    ROUND(AVG(r.active_qsp_sum), 1) AS avg_qsp_sum,
    ROUND(AVG(n.e1_lead_over_ep), 1) AS avg_e1_lead,
    ROUND(AVG(n.purse_ratio), 2) AS avg_purse_ratio,
    ROUND(AVG(n.days_off), 1) AS avg_days_off,
    ROUND(AVG(n.hit_1st) * 100, 1) AS actual_win_pct
FROM runners n
JOIN races r ON n.race_id = r.race_id
WHERE (n.tag_retail_trap = 1 AND n.hit_1st = 1)
   OR (n.tag_e = 1 AND n.finish_pos > 3)
GROUP BY r.track_code, r.today_dist_yds, r.pace_scenario, n.primary_grade, n.tag_e, n.tag_retail_trap;
Would you like me to package the complete Python ingestion script (ingest_card.py) that Pete can drop directly into your repository to populate races and runners from any *_distilled.csv and DRF text block?




