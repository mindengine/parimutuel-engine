# Breeders' Cup v3 card (Keeneland, Oct 30–31 2026)

Two steps per race day, both deterministic:

```bash
python tools/distill_v3.py --file KEE1031.DRF --output distilled_v3_KEE_20261031.csv
python tools/ultimate_pp_overlay.py --pdf kee1031y.pdf --card distilled_v3_KEE_20261031.csv \
       --output bc_v3_KEE_20261031.csv
```

* `KEE1031.DRF` is the Brisnet PP Data File (single). `kee1031y.pdf` is Ultimate PPs w/ QuickPlay Comments.
* Grant's 47 columns are unchanged. 21 columns are added on the right (see each script's docstring).
* Nothing is imputed. Blank means Brisnet has no value. `Form_Sig` says why a horse has no figures.

What Grant's v3 spec asked for and where it stands:

| Spec item | Status |
|---|---|
| Bullet work flag + positive work time | `Work_1_Bullet`, `Work_1_Time_Abs` |
| Year trainer/jockey win % (meet % reads 0 early) | `Trn_Yr_*`, `Jock_Yr_*` (all tracks, current year) |
| Form_Sig | done, from raw Brisnet values before any fill |
| Last_Fin, Fin_2Back, Fin_3Back, Last_Btn_Len, Last_Odds, Last_Group_Class | from the data file, no PDF scraping needed (Brisnet has no odds for most European races) |
| Capsule / QuickPlay comments | `QP_Positive`, `QP_Negative` |
| Foreign trip notes | `Last_Intl_Trip_Note` (Brisnet race-reader comment, dated) |
| International class on the same scale | `Bris_Current_Class`, `Bris_Avg_Class_L3` (Brisnet's own; most Japanese runners have none) |
| International Scouting Report (ground preference, intl run style) | not published by Brisnet for 2026 as of Oct 28 |
| Timeform / RPR / TFUS conversions | not done: needs a subscription, and the offsets are unvalidated |
| Profit Line, consensus | not done |
