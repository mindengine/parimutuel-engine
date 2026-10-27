-- Parimutuel Engine forensics database (SQLite; portable to Postgres).
PRAGMA foreign_keys = ON;

-- One handicapping run of one track's card.
CREATE TABLE IF NOT EXISTS cards (
    card_id        TEXT PRIMARY KEY,          -- e.g. BEL-2026-10-25
    track          TEXT NOT NULL,             -- BEL, CD, LRL, GP, PRX ...
    race_date      TEXT,                      -- ISO date; NULL if unknown (legacy)
    source         TEXT NOT NULL,             -- 'tail_block' | 'legacy_text'
    source_file    TEXT,
    schema_version TEXT,
    ingested_at    TEXT NOT NULL DEFAULT (datetime('now')),
    notes          TEXT
);

-- Race-level state at handicapping time.
CREATE TABLE IF NOT EXISTS races (
    card_id       TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    race          INTEGER NOT NULL,
    surface       TEXT,                       -- D / T / AW
    distance_yds  INTEGER,
    race_type     TEXT,                       -- MSW, CLM, ALW, STK ...
    field_size    INTEGER,                    -- active runners post-scratch
    qsp_sum       INTEGER,
    pace_scenario TEXT,                       -- LONE_SPEED | HONEST | MELTDOWN
    par_spd       REAL,
    par_lp        REAL,
    grades_complete INTEGER NOT NULL DEFAULT 0, -- 1 = every active runner graded
    PRIMARY KEY (card_id, race)
);

-- Module 1: pre_race_grades (one row per runner).
CREATE TABLE IF NOT EXISTS pre_race_grades (
    card_id     TEXT NOT NULL,
    race        INTEGER NOT NULL,
    pgm         TEXT NOT NULL,                -- program number as printed (1, 1A)
    horse_name  TEXT,
    bucket      TEXT,                         -- A+ A B C D F- F ; NULL = ungraded
    modifiers   TEXT NOT NULL DEFAULT '',     -- e.g. 'W,E'
    tags        TEXT NOT NULL DEFAULT '',     -- e.g. 'UNSEEN,RETAIL_TRAP'
    ml_odds     REAL,
    scratched   INTEGER NOT NULL DEFAULT 0,
    p_est       REAL,                         -- model-stated probability (logged, not trusted)
    prime_power REAL, last_fig REAL, last_e1 REAL, last_lp REAL, qsp INTEGER,
    grade_confidence TEXT NOT NULL DEFAULT 'explicit', -- explicit | inferred | conflict
    reason      TEXT,                         -- the grader's written reason, when the source has one
    PRIMARY KEY (card_id, race, pgm),
    FOREIGN KEY (card_id, race) REFERENCES races(card_id, race) ON DELETE CASCADE
);

-- Every proposed bet.
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id        TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    label          TEXT,
    structure      TEXT,                      -- MAIN | CHAOS | SAVER | VERTICAL ...
    pool           TEXT NOT NULL,             -- WIN EX TRI SUP DD P3 P4 P5 P6
    race           INTEGER NOT NULL,          -- vertical race, or first leg
    base           REAL NOT NULL,
    selections     TEXT NOT NULL,             -- JSON list of lists (legs or positions)
    combos         INTEGER NOT NULL,          -- recomputed
    cost           REAL NOT NULL,             -- recomputed
    claimed_combos INTEGER,
    claimed_cost   REAL,
    issues         TEXT NOT NULL DEFAULT '[]',-- JSON list of validation issues
    played         INTEGER NOT NULL DEFAULT 0 -- 1 = actually bet
);

-- post_race_official: finishing order + final odds.
CREATE TABLE IF NOT EXISTS post_race_official (
    track      TEXT NOT NULL,
    race_date  TEXT NOT NULL,
    race       INTEGER NOT NULL,
    pgm        TEXT NOT NULL,
    finish_pos INTEGER,                       -- official, after DQs; NULL = DNF/scratch
    final_odds REAL,
    scratched  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (track, race_date, race, pgm)
);

-- Official payouts, normalized to the base they were paid on.
CREATE TABLE IF NOT EXISTS payouts (
    track        TEXT NOT NULL,
    race_date    TEXT NOT NULL,
    race         INTEGER NOT NULL,            -- race the pool closed (last leg for horizontals)
    pool         TEXT NOT NULL,
    winning_combo TEXT NOT NULL,              -- e.g. '4-1-5' (vertical) or '3/1/1/6/4' (horizontal)
    base         REAL NOT NULL,
    payout       REAL NOT NULL,
    PRIMARY KEY (track, race_date, race, pool, winning_combo)
);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    flag_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   TEXT,
    race      INTEGER,
    severity  TEXT NOT NULL,                  -- info | warn | error
    message   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Grades joined to results. c.source separates Grant's grades from the rules grader's for the same day.
DROP VIEW IF EXISTS v_bucket_hit_rates;
DROP VIEW IF EXISTS v_graded_results;
CREATE VIEW v_graded_results AS
SELECT g.*, c.track, c.race_date, c.source, r.pace_scenario, r.field_size, r.qsp_sum,
       o.finish_pos, o.final_odds
FROM pre_race_grades g
JOIN cards c USING (card_id)
JOIN races r USING (card_id, race)
LEFT JOIN post_race_official o
  ON o.track = c.track AND o.race_date = c.race_date
 AND o.race = g.race AND o.pgm = g.pgm
WHERE g.scratched = 0;

-- Module 2: bucket hit rates by pace scenario.
CREATE VIEW v_bucket_hit_rates AS
SELECT bucket, pace_scenario,
       COUNT(*)                                            AS starters,
       SUM(finish_pos = 1)                                 AS wins,
       ROUND(1.0 * SUM(finish_pos = 1) / COUNT(*), 3)       AS win_pct,
       ROUND(1.0 * SUM(finish_pos <= 2) / COUNT(*), 3)      AS place_pct,
       ROUND(1.0 * SUM(finish_pos <= 3) / COUNT(*), 3)      AS show_pct,
       ROUND(AVG(final_odds), 2)                           AS avg_final_odds
FROM v_graded_results
WHERE finish_pos IS NOT NULL AND bucket IS NOT NULL
GROUP BY bucket, pace_scenario;
