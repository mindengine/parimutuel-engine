-- Parimutuel Engine v2: handicapping + pool-efficiency database.
-- Follows docs/v2_spec.md. Deviations from the spec text are marked "DEVIATION".
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY,            -- 'YYYYMMDD_TRK_RR'
    track_code TEXT NOT NULL,
    race_date INTEGER NOT NULL,          -- YYYYMMDD
    race_num INTEGER NOT NULL,
    today_dist_yds INTEGER NOT NULL,
    today_surf TEXT NOT NULL,
    today_class TEXT NOT NULL,
    today_purse INTEGER NOT NULL,
    track_cond TEXT DEFAULT 'FT',
    par_e1 INTEGER, par_e2 INTEGER, par_lp INTEGER, par_spd INTEGER,
    active_starters INTEGER NOT NULL,
    active_qsp_sum INTEGER NOT NULL,
    pace_scenario TEXT NOT NULL,         -- 'Lone_Speed' | 'Honest' | 'Meltdown'
    est_dead_pool_share REAL,
    actual_e1_par_delta REAL
);

CREATE TABLE IF NOT EXISTS runners (
    runner_id TEXT PRIMARY KEY,          -- 'YYYYMMDD_TRK_RR_PP'
    race_id TEXT NOT NULL REFERENCES races(race_id) ON DELETE CASCADE,
    post_pos INTEGER NOT NULL,
    prog_num TEXT NOT NULL,              -- DEVIATION: TEXT, not INTEGER, so coupled entries like '1A' survive
    horse_name TEXT NOT NULL,
    is_scratched INTEGER DEFAULT 0,
    is_fts INTEGER DEFAULT 0,

    ml_odds REAL NOT NULL,
    ml_implied_prob REAL NOT NULL,
    weight INTEGER,
    jockey TEXT, jock_sts INTEGER, jock_win INTEGER, jock_win_pct REAL,
    trainer TEXT, trn_sts INTEGER, trn_win INTEGER, trn_win_pct REAL,
    valid_jock_pct REAL,                 -- ETL rule 2: NULL when jock_sts < 15
    valid_trn_pct REAL,                  -- ETL rule 2: NULL when trn_sts < 10
    sire TEXT, dam_sire TEXT,
    run_style TEXT, qsp INTEGER, days_off INTEGER, prime_power REAL,

    last_date INTEGER, last_track TEXT, last_dist_yds INTEGER, last_surface TEXT,
    last_cond TEXT, last_purse INTEGER, last_comment TEXT,
    last_e1 INTEGER, last_e2 INTEGER, last_lp INTEGER, last_fig INTEGER,
    fig_2back REAL, fig_3back REAL,

    work_1_date INTEGER, work_1_track TEXT, work_1_dist INTEGER,
    is_bullet_work INTEGER, work_1_seconds REAL, work_1_sec_per_furlong REAL, work_1_days_ago INTEGER,

    pp_rank INTEGER, pp_delta_to_1st REAL, pp_lead_over_2nd REAL,
    last_fig_delta REAL, best_3back_delta REAL, fig_within_2_of_top INTEGER,
    last_e1_rank INTEGER, last_e1_delta REAL, e1_lead_over_ep REAL,
    last_e2_delta REAL,
    last_lp_rank INTEGER, last_lp_percentile REAL, last_lp_delta REAL,
    purse_ratio REAL, dist_delta_yds INTEGER, is_turf_to_dirt INTEGER, has_trouble_comment INTEGER,

    drf_pick_rank INTEGER DEFAULT 0, beyer_edge_rank INTEGER DEFAULT 0,
    consensus_pts INTEGER DEFAULT 0, consensus_rank INTEGER DEFAULT 0,
    is_drf_best_bet INTEGER DEFAULT 0, is_live_longshot INTEGER DEFAULT 0,
    beyer_1 INTEGER, beyer_2 INTEGER, beyer_3 INTEGER,
    bris_vs_beyer_delta REAL, eq_change TEXT,
    has_herd_radar INTEGER DEFAULT 0,    -- DEVIATION: 1 once DRF data is loaded, so tag_unseen_retail isn't set from missing data

    primary_grade TEXT,                  -- DEVIATION: nullable; scratched runners are never graded
    grade_source TEXT,                   -- 'rules_v1' | 'grant' | ...
    grade_reasons TEXT,
    tag_w INTEGER DEFAULT 0, tag_e INTEGER DEFAULT 0,
    tag_retail_trap INTEGER DEFAULT 0, tag_unseen_retail INTEGER DEFAULT 0,
    est_true_prob_p REAL,

    final_tote_odds REAL, final_implied_prob_q REAL, clv_ratio REAL,
    finish_pos INTEGER, beaten_lengths REAL,
    win_pay REAL DEFAULT 0.0, place_pay REAL DEFAULT 0.0, show_pay REAL DEFAULT 0.0,
    hit_1st INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 1 THEN 1 ELSE 0 END) VIRTUAL,
    hit_2nd INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 2 THEN 1 ELSE 0 END) VIRTUAL,
    hit_3rd INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 3 THEN 1 ELSE 0 END) VIRTUAL,
    hit_4th INTEGER GENERATED ALWAYS AS (CASE WHEN finish_pos = 4 THEN 1 ELSE 0 END) VIRTUAL
);
CREATE INDEX IF NOT EXISTS ix_runners_race ON runners(race_id);

CREATE TABLE IF NOT EXISTS pools (
    pool_id TEXT PRIMARY KEY,            -- '20261026_MVR_R04_P5'
    track_code TEXT NOT NULL,
    race_date INTEGER NOT NULL,
    anchor_race_id TEXT NOT NULL REFERENCES races(race_id) ON DELETE CASCADE,  -- first leg for horizontals
    pool_type TEXT NOT NULL,             -- EXA TRI SUP DD P3 P4 P5 P6
    num_legs INTEGER NOT NULL,
    takeout_rate REAL NOT NULL,
    carryover_pct REAL DEFAULT 0.0,
    winning_prog_str TEXT,
    winning_grade_str TEXT,
    payout_1d_norm REAL,
    theo_win_parlay_1d REAL,
    parlay_leverage_ratio REAL,
    captured_by_playable INTEGER,
    lowest_tier_needed TEXT,
    combos_to_capture INTEGER,
    combos_to_capture_keyed INTEGER,     -- DEVIATION: verticals also get the A+/A-keyed cost alongside the box cost
    theo_tier_yield REAL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,          -- '20261026_FLX_P5_SLIP1'
    pool_id TEXT NOT NULL REFERENCES pools(pool_id) ON DELETE CASCADE,
    slip_tier TEXT NOT NULL,
    matrix_definition TEXT NOT NULL,     -- '1,4,6 / 1,3,4,5 / ...'
    base_wager REAL NOT NULL,
    unique_combos INTEGER NOT NULL,      -- recomputed, never trusted
    ticket_cost REAL NOT NULL,           -- base_wager * unique_combos
    is_winner INTEGER DEFAULT 0,
    gross_return REAL DEFAULT 0.0
);

-- Query A: grade & modifier calibration audit.
-- DEVIATION: NULLIF guards divide-by-zero when est_true_prob_p is not yet populated.
CREATE VIEW IF NOT EXISTS v_grade_calibration AS
SELECT primary_grade, tag_w, tag_e, tag_unseen_retail,
    COUNT(*) AS starters,
    ROUND(AVG(hit_1st) * 100, 1) AS win_pct,
    ROUND(SUM(hit_1st) / NULLIF(SUM(est_true_prob_p), 0), 2) AS actual_vs_expected_ae,
    ROUND(AVG(clv_ratio), 3) AS avg_clv_ratio,
    ROUND(SUM(win_pay) / (COUNT(*) * 2.0), 3) AS flat_win_roi,
    ROUND(SUM(place_pay) / (COUNT(*) * 2.0), 3) AS flat_place_roi,
    ROUND(SUM(show_pay) / (COUNT(*) * 2.0), 3) AS flat_show_roi,
    ROUND(AVG(hit_1st + hit_2nd + hit_3rd) * 100, 1) AS top3_tri_pct,
    ROUND(AVG(hit_1st + hit_2nd + hit_3rd + hit_4th) * 100, 1) AS top4_super_pct
FROM runners
WHERE is_scratched = 0 AND finish_pos IS NOT NULL
GROUP BY primary_grade, tag_w, tag_e, tag_unseen_retail;

-- Query B: pool efficiency & theoretical tier yield.
CREATE VIEW IF NOT EXISTS v_pool_efficiency AS
SELECT pool_type, lowest_tier_needed,
    COUNT(*) AS total_pools,
    ROUND(AVG(captured_by_playable) * 100, 1) AS playable_capture_pct,
    ROUND(AVG(payout_1d_norm), 2) AS avg_1d_payout,
    ROUND(AVG(parlay_leverage_ratio), 2) AS avg_parlay_leverage,
    ROUND(AVG(combos_to_capture), 1) AS avg_combos_needed,
    ROUND(SUM(payout_1d_norm) / NULLIF(SUM(combos_to_capture), 0), 3) AS theoretical_tier_roi
FROM pools
WHERE payout_1d_norm IS NOT NULL
GROUP BY pool_type, lowest_tier_needed;

-- Query C: parameter leak detector.
CREATE VIEW IF NOT EXISTS v_parameter_leaks AS
SELECT r.track_code, r.today_dist_yds, r.pace_scenario,
    n.primary_grade, n.tag_e, n.tag_retail_trap,
    COUNT(*) AS sample_size,
    ROUND(AVG(r.active_qsp_sum), 1) AS avg_qsp_sum,
    ROUND(AVG(n.e1_lead_over_ep), 1) AS avg_e1_lead,
    ROUND(AVG(n.purse_ratio), 2) AS avg_purse_ratio,
    ROUND(AVG(n.days_off), 1) AS avg_days_off,
    ROUND(AVG(n.hit_1st) * 100, 1) AS actual_win_pct
FROM runners n JOIN races r ON n.race_id = r.race_id
WHERE n.is_scratched = 0
  AND ((n.tag_retail_trap = 1 AND n.hit_1st = 1) OR (n.tag_e = 1 AND n.finish_pos > 3))
GROUP BY r.track_code, r.today_dist_yds, r.pace_scenario, n.primary_grade, n.tag_e, n.tag_retail_trap;
