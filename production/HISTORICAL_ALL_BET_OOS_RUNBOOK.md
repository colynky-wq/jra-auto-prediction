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

The collector horse parquet does **not** contain an explicit frame column. The one-command runner reconstructs JRA frame numbers from the complete original card horse numbers before the all-bet batch, so 枠連 remains scorable even when a horse is scratched.

## 1. 20-race smoke test

From `production/`:

```bash
python run_all_bet_oos_drive_2025.py
```

This single command now performs:

1. archive continuity audit;
2. strict 2025 walk-forward OOS prediction rebuild;
3. restriction to races actually present in the saved quote archive;
4. deterministic JRA frame-number enrichment;
5. eight-pool historical quote + payout settlement;
6. EV-threshold summary with race-level trigami / losing-streak metrics;
7. per-race Top-K portfolio grid for ticket-count control.

Then validate:

```bash
python validate_all_bet_oos_run.py \
  --manifest /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/drive_2025_run_manifest.json
```

## 2. Full currently available archive

After the smoke gate passes, reuse the already validated OOS predictions:

```bash
python run_all_bet_oos_drive_2025.py --full --reuse-predictions
```

`--full` means every race in the current **audited contiguous archive**, not the entire 2025 calendar year unless the archive audit says so.

Then require all eight standard pools to have settled coverage:

```bash
python validate_all_bet_oos_run.py \
  --manifest /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/drive_2025_run_manifest.json \
  --full \
  --output-json /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/full_quality_gate.json
```

`QUALITY_GATE=PASS` is required before interpreting ROI. It checks strict OOS provenance, real scored/settled samples, all eight pools on a full available-archive run, usable frame information for 枠連, race-unit losing streak semantics, explicit trigami semantics, and zero forward-ledger contact.

## Outputs

```text
research_all_bet_oos_2025/
  historical_archive_coverage.json
  strict_oos_predictions_2025.csv
  strict_oos_predictions_2025_archive_with_frames.csv
  strict_oos_prediction_audit.json
  strict_oos_frame_audit.json
  drive_2025_run_manifest.json
  retrospective_2025/
    retrospective_oos_all_bet_samples.csv
    retrospective_oos_race_audit.csv
    retrospective_oos_all_bet_summary.csv
    retrospective_oos_portfolio_grid.csv
    retrospective_oos_manifest.json
```

`retrospective_oos_portfolio_grid.csv` compares EV threshold × per-race Top-K cap. This is important because an apparently high-EV ticket set can still become a losing race or trigami when too many combinations are purchased.

## Safeguards

- missing payout coverage is unresolved, never a loss;
- `票数なし` / missing quotes are unavailable, never zero odds;
- place/wide range odds use the lower bound for conservative EV;
- payout HTML can contain two pool blocks in one table row and concatenated combination strings; the strict parser is regression-tested against an exact saved Drive gzip;
- losing streak is counted by race, not by the number of individual losing tickets inside one race;
- trigami means: at least one selected ticket hits but total race return is below total selected-ticket stake;
- do not claim a profitable strategy from a tiny smoke sample; use the full available archive plus uncertainty / condition analysis before changing app recommendations;
- WIN5 remains a separate five-race research path.
