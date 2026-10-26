# Parimutuel Engine — start here

Everything for the project is in this folder: code, card data, both databases, specs and tests.
Zip the whole folder to move it; nothing outside it is needed.

## 1. One-time setup

Needs Python 3.11 or newer (python.org).

- **Mac / Linux:** open a terminal in this folder and run `./setup.sh`
- **Windows:** double-click `setup.bat`

This makes a private Python environment in `.venv/`, installs the packages, and runs the tests.

## 2. Everyday use

Activate the environment first (`source .venv/bin/activate` on Mac, `.venv\Scripts\activate` on Windows), then:

| Task | Command |
|---|---|
| Add a new card | make `data/cards/TRACK_YYYYMMDD/`, put `distilled.csv` in it, then `python -m pe.v2 rebuild` |
| See the diagnostics | `python -m pe.v2 report` |
| Check slips for overlap and cost | `python -m pe.v2 slips my_slips.json` |
| Open the v1 dashboard | `streamlit run app.py` |
| Run the tests | `pytest -q` |

## 3. Folder map

| Folder | What's in it |
|---|---|
| `data/cards/` | **Your data.** One folder per card — the source of truth |
| `data/engine_v2.db` | v2 database (rebuilt from `data/cards/` any time) |
| `data/engine.db` | v1 forensics database (from `data/archive/`) |
| `data/archive/` | Grant's original chat outputs |
| `pe/v2/` | v2 pipeline: schema, ETL, pool math |
| `pe/` | v1 engine: grader, ticket validator, parsers |
| `docs/` | The v2 spec, implementation notes, tail-block spec |
| `examples/` | Sample input files |
| `tests/` | Automated checks |
| `tools/` | Grant's distiller, audit workbook builder |

## 4. What goes in a card folder

```
data/cards/MNR_20261026/
  distilled.csv     required: the 47-column distilled card
  extras.json       optional: scratches, track condition, DRF herd radar, Grant's grades
  slips/*.json      optional: ADW slips (checked for overlap)
  results.csv       optional: race,prog,finish_pos,final_odds,win_pay,place_pay,show_pay
  pools.csv         optional: pool_type,first_race,winning_prog_str,base,payout
```

The databases are always rebuildable from `data/cards/`, so back up that folder above all.
Formats are detailed in `docs/v2_implementation_notes.md`.

## 5. GitHub

This folder is also a git clone of `github.com/mindengine/parimutuel-engine`. `git pull` gets the latest
code. The databases aren't stored on GitHub; the card folders are.
