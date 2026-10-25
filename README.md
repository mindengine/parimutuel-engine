# Parimutuel Engine

Post-race forensics database, ticket validator and (next) combinatorial ticket generator for the bucket-grading handicapping engine.

## Quick start

```bash
pip install -r requirements.txt
python -m pe init
python -m pe legacy data/archive/*.txt                    # archived chat outputs (best effort)
python -m pe ingest examples/tail_block_example.json      # structured output (going forward)
python -m pe validate examples/tail_block_example.json    # check ticket math before betting
python -m pe results results.csv && python -m pe payouts payouts.csv
python -m pe report
streamlit run app.py                                      # dashboard
pytest -q
```

## Layout

| Path | What |
|---|---|
| `pe/tickets.py` | Combinatorics: horizontal products, vertical permutations, boxes, validation, hit checks |
| `pe/schema.sql` | SQLite schema: `cards`, `races`, `pre_race_grades`, `tickets`, `post_race_official`, `payouts`, `data_quality_flags` |
| `pe/db.py` | Tail-block ingest, results/payout CSV import |
| `pe/legacy.py` | Archive parser for pre-tail-block chat outputs |
| `pe/analytics.py` | Bucket hit rates, ticket grading, LOTT audit, sequence signatures |
| `app.py` | Streamlit dashboard |
| `docs/tail_block_spec.md` | **The JSON block to add to the handicapping prompt** |

## Results import format

`results.csv`: `track,race_date,race,pgm,finish_pos,final_odds[,scratched]`
`payouts.csv`: `track,race_date,race,pool,winning_combo,base,payout`. `race` is the race the pool closed. Use `4-1-5` for verticals and `3/1/1/6/4` (winner per leg) for horizontals.

No scraper ships yet: the source (Equibase/Brisnet charts or a paid feed) and its terms decide what gets built.

## Known limits

* Archived cards only grade the runners they mention, so their races are never `grades_complete`. LOTT treats any ungraded winner as not covered.
* Archive dates aren't in the files. Pass `--date` to `pe legacy` or results can't be joined.
* `p_est` values come from the LLM layer and are logged for calibration, never used as inputs.

## Roadmap

1. Results importer for the chosen data source.
2. LOTT leak rules (which bucket and modifier combinations consumed budget without returning).
3. Module 3: ticket generator sized from empirical hit rates, with out-of-sample backtests.
