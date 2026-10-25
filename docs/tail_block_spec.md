# Tail Block Spec v1.0

Every handicapping run ends with one fenced JSON block. The database ingests that block and nothing else, so the prose above it can change freely.

## Prompt text to append

Paste this at the end of the daily handicapping prompt:

````
After the full analysis, output a final section titled TAIL BLOCK containing exactly one
```json fenced block that follows these rules:

1. Top level: schema_version "1.0", track (3-letter code), race_date (YYYY-MM-DD), races, tickets.
2. races: one object per race on the card, including races you pass. Fields: race, surface
   (D/T/AW), distance_yds, race_type, field_size (active runners after scratches), qsp_sum,
   pace_scenario (LONE_SPEED | HONEST | MELTDOWN), par_spd, par_lp, runners.
3. runners: EVERY runner in the race, including scratches and every F horse. Fields: pgm (string),
   name, ml_odds (decimal odds-to-1, so 9/2 = 4.5), bucket (A+ | A | B | C | D | F- | F, or null
   only if scratched), modifiers (list from W, E), tags (list from UNSEEN, RETAIL_TRAP, CONSENSUS_1),
   scratched (true/false), and when available: p_est, prime_power, last_fig, last_e1, last_lp, qsp.
4. tickets: every bet you recommend, one object per ticket line. Fields: label, structure
   (MAIN | CHAOS | SAVER | VERTICAL | WIN), pool (WIN PLC SHW EX TRI SUP DD P3 P4 P5 P6), race
   (vertical race, or FIRST leg for horizontals), base (dollars), then EITHER
     legs: list of lists of pgm strings, one list per race (horizontals), OR
     positions: list of lists of pgm strings, one list per finishing slot (verticals;
     a box of 4,5 is [["4","5"],["4","5"]]),
   and claimed_combos, claimed_cost.
5. Never describe a ticket as "trimmed" or "scaled" in prose without also emitting the final
   ticket exactly as it should be bet. Express all money in dollars, never units.
6. The block must be valid JSON: no comments, no trailing commas, no LaTeX.
````

## Field reference

| Field | Type | Notes |
|---|---|---|
| `pgm` | string | Program number as printed (`"1"`, `"1A"`). |
| `ml_odds` | number | Decimal odds-to-1. 9/5 → 1.8, 5/2 → 2.5. |
| `bucket` | string | One of `A+ A B C D F- F`. Required for every active runner. |
| `modifiers` | list | `W` (value overlay), `E` (controlling speed). |
| `tags` | list | `UNSEEN`, `RETAIL_TRAP`, `CONSENSUS_1`. Free to extend. |
| `p_est` | number | Model-stated probability. Logged for calibration only. |
| `qsp_sum` | int | Active (post-scratch) QSP total. |
| `legs` / `positions` | list of lists | Horizontal legs or vertical slots. |
| `claimed_*` | number | What the prompt believes. The ingester recomputes and flags any mismatch. |

## Why every runner matters

The LOTT audit asks whether every winner sat in A+ through D. That question only has an answer if the losers' grades are recorded too: a winner missing from the block could be an F the model dismissed or a horse it never looked at. Races without a bucket for every active runner are marked `grades_complete = 0` and left out of LOTT ceilings.

See `examples/tail_block_example.json`.
