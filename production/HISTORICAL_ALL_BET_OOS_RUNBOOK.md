# Historical all-bet strict-OOS runbook

Retrospective research only. Forward ledgers are never read or written.

## Current Drive inputs

```text
/content/gdrive_live/MyDrive/code_output/
  github_actions_stage/production/production_history.parquet
  jra_data/historical_odds_2025/race_manifest_2025.csv
  jra_data/historical_odds_2025/races/*_odds.parquet
  jra_data/historical_odds_2025/races/*_horses.parquet
  jra_data/historical_odds_2025/payout_raw/*.html.gz
```

The 2025 retrospective predictions are rebuilt using only data through 2024-12-31. Do not use the fixed-2025 model or its 212 rounds. The current strict walk-forward rebuild selects its own round count on 2024.

The collector horse parquet does **not** contain an explicit frame column. The runner reconstructs JRA frame numbers from the complete original card horse numbers before the all-bet batch, so 枠連 remains scorable even when a horse is scratched.

## Final full-archive command

From `production/`, this is the final research command:

```bash
python run_all_bet_oos_drive_2025_final.py
```

It performs, in order:

1. historical archive continuity audit;
2. strict 2025 walk-forward OOS prediction rebuild;
3. restriction to races actually present in the saved quote archive;
4. deterministic JRA frame-number enrichment;
5. all eight standard-pool historical quote + payout settlement;
6. EV-threshold race-level ROI / trigami / losing-streak summary;
7. per-race Top-K portfolio grid (K=1,3,5,10,20);
8. 2,000-rep calendar-day bootstrap ROI uncertainty intervals;
9. strict full-archive technical quality gate;
10. final `full_research_quality_gate.json` output.

Use `--reuse-predictions` only when reusing a previously generated prediction CSV; provenance is revalidated before use.

The command processes every race in the current **audited contiguous archive**. It does not imply the entire 2025 calendar year unless the archive audit says the full manifest is covered.

## Optional 20-race smoke test

```bash
python run_all_bet_oos_drive_2025.py
```

This is useful after future parser/model changes before rerunning the full archive.

## Outputs

```text
research_all_bet_oos_2025/
  historical_archive_coverage.json
  strict_oos_predictions_2025.csv
  strict_oos_predictions_2025_archive_with_frames.csv
  strict_oos_prediction_audit.json
  strict_oos_frame_audit.json
  drive_2025_run_manifest.json
  full_research_quality_gate.json
  retrospective_2025/
    retrospective_oos_all_bet_samples.csv
    retrospective_oos_race_audit.csv
    retrospective_oos_all_bet_summary.csv
    retrospective_oos_portfolio_grid.csv
    retrospective_oos_day_bootstrap.csv
    retrospective_oos_manifest.json
```

`retrospective_oos_portfolio_grid.csv` compares EV threshold × per-race Top-K cap. `retrospective_oos_day_bootstrap.csv` adds day-block bootstrap intervals so millions of individual tickets are not falsely treated as independent observations.

## Interpretation gate

A historical result is not promoted simply because point-estimate ROI exceeds 100%. Before changing app recommendations, require:

- `QUALITY_GATE=PASS`;
- all eight pools scored and settled on the full currently available archive;
- strict OOS provenance;
- contiguous archive scope;
- race-level losing streak and trigami semantics;
- Top-K portfolio output;
- calendar-day bootstrap uncertainty output;
- no forward-ledger contact.

Even after those conditions pass, saved historical odds are final-quote research evidence. They are **not** proof that the same EV was observable at the app's live pre-race timing. Forward odds snapshots remain the deployment-validation layer.

## Safeguards

- missing payout coverage is unresolved, never a loss;
- `票数なし` / missing quotes are unavailable, never zero odds;
- place/wide range odds use the lower bound for conservative EV;
- payout HTML can contain two pool blocks in one table row and concatenated combination strings; the strict parser is regression-tested against an exact saved Drive gzip;
- losing streak is counted by race, not by the number of individual losing tickets inside one race;
- trigami means: at least one selected ticket hits but total race return is below total selected-ticket stake;
- do not claim a profitable strategy from a tiny smoke sample;
- WIN5 remains a separate five-race research path.
