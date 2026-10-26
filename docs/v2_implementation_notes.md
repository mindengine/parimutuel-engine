# v2 implementation notes

Built from `docs/v2_spec.md`. Code: `pe/v2/` (`schema.sql`, `etl.py`, `pools.py`), tests: `tests/test_v2.py`.
Database: `data/engine_v2.db` (separate from the v1 forensics DB, which still works unchanged).

## Commands

```bash
python -m pe.v2 ingest examples/distilled_BEL_2026-10-22.csv --extras examples/v2_extras_BEL_2026-10-22.json
python -m pe.v2 slips examples/v2_slips_example.json     # exits 1 on any slip overlap
python -m pe.v2 settle --track BEL --date 20261022 --results results.csv --pools pools.csv
python -m pe.v2 report                                   # Queries A, B, C
```

`results.csv`: `race,prog,finish_pos,final_odds[,beaten_lengths,win_pay,place_pay,show_pay]` ($2 mutuels)
`pools.csv`: `pool_type,first_race,winning_prog_str,base,payout[,takeout,carryover]`. Dead heats as `1/4` inside a slot or leg.

## Card extras JSON

Scratches, track condition, DRF herd radar and Grant's grades come in one sidecar file per card
(see `examples/v2_extras_BEL_2026-10-22.json`). Grant's grades override the rules grader runner by runner.

## Where the code differs from the spec, and why

| Spec | Built | Why |
|---|---|---|
| `prog_num INTEGER` | `TEXT` | Coupled entries (`1A`) would be lost. |
| `primary_grade NOT NULL` | nullable | Scratched runners are never graded. |
| `tag_unseen_retail` = no DRF pick and no consensus rank | also requires DRF data loaded for that race (`has_herd_radar`) | Otherwise every runner on a card without DRF text is tagged "unseen". |
| `is_fts`: PP 0/NaN and Last_Fig NaN | that, **or** `Run_Style = 'FTS'` | The distiller already labels FTS runners. |
| Rule 3 floor on `last_fig_delta` | applied; FTS get NULL | A first-timer has no last figure. |
| Query A / B divisions | `NULLIF(..., 0)` | Avoids divide-by-zero before `est_true_prob_p` or combos exist. |
| One `combos_to_capture` | adds `combos_to_capture_keyed` for verticals | The spec defines both box and A+/A-keyed costs; keyed is NULL when the winner isn't A+/A. |
| Keyed formula `|S_top| × ...` | `|S_top ∩ S_m| × ...` | At depth A+, `S_top` would otherwise count A runners that aren't in play. |
| Dead heats | a leg is captured if any of its winners is covered | Not addressed in the spec. |
| `pools.anchor_race_id` | first leg for horizontals | Spec example `R04_P5` reads as the starting race. |

## Not built yet (needs input)

1. **DRF text parsing.** The spec says to parse `<retail_herd_radar>` and `<track_conditions_and_changes>`
   blocks, but no sample was provided. For now that data goes into the extras JSON. Send one real block and
   I'll write the parser that produces the same JSON.
2. **`tag_retail_trap` and `est_true_prob_p`** have no rule in the spec. They come from Grant's grades
   (extras `tags: ["RETAIL_TRAP"]`, `p`) until a rule is defined.
3. **`actual_e1_par_delta`** needs the race's actual E1 call from the chart; Brisnet charts would supply it.
4. **MVR / FLX / IND / PRX** are harness tracks (the spec cites `MVR_20261026_distilled.csv`). The code is
   track-agnostic, but it has only been tested on the Belmont sample. Send the MVR file to verify.
