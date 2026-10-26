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

## v2 database (handicapping + pool efficiency)

The four-stage pipeline from `docs/v2_spec.md` lives in `pe/v2/`. See `docs/v2_implementation_notes.md`
for commands, input formats and where the code differs from the spec.

```bash
python -m pe.v2 ingest examples/distilled_BEL_2026-10-22.csv --extras examples/v2_extras_BEL_2026-10-22.json
python -m pe.v2 report
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
`payouts.csv`: `track,race_date,race,pool,winning_combo,base,payout`. `race` is the race the pool closed. Use Brisnet's notation: slots or legs joined by `-`, with alternative winners in one leg joined by `/` (e.g. `2-1/8-4/5-1-1/4/10`).

Brisnet Comprehensive Charts ZIPs load directly: `python -m pe brisnet charts.zip` (or the dashboard's Load data page). The importer follows Brisnet's published layout and still needs checking against a real file.

## Known limits

* Archived cards only grade the runners they mention, so their races are never `grades_complete`. LOTT treats any ungraded winner as not covered.
* Archive dates aren't in the files. Pass `--date` to `pe legacy` or results can't be joined.
* `p_est` values come from the LLM layer and are logged for calibration, never used as inputs.

## Roadmap

1. Results importer for the chosen data source.
2. LOTT leak rules (which bucket and modifier combinations consumed budget without returning).
3. Module 3: ticket generator sized from empirical hit rates, with out-of-sample backtests.

## Grant's distill.py

`tools/distill.py` is Grant's script, unchanged. It turns a Brisnet single-file PP download (`.drf`/`.csv`) into one row per horse:
`python tools/distill.py --file BEL1025.DRF --output distilled_card.csv`. A sample output is in `examples/distilled_BEL_2026-10-22.csv`.
It does not assign grades, remove scratches, or compute the Active QSP sum or pace scenario; those happen in the grading step.

## Rules grader

`pe/grading.py` grades every starter from a distilled card: QSP sum, pace scenario, A+/A/B/C/D/F-/F and W/E, with the reason for each grade. It's a deterministic approximation of Grant's LLM grading, built from the dossier's definitions and tuned only against his own grades (59% exact agreement on the 204 horses he graded on Oct 25, 2026). All thresholds are in `RULES`.

```bash
python -m pe brisnet charts.zip                       # results first, so scratches are known
python -m pe grade distilled_BEL.csv --track BEL --date 2026-10-25
PYTHONPATH=. python tools/build_audit_xlsx.py RULES_DB BREAKDOWN_DB out.xlsx "Oct 25, 2026"
```
