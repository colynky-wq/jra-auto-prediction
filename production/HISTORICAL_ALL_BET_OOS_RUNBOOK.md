# Historical all-bet strict-OOS runbook

Retrospective research only. Forward ledgers are never read or written.

## Current Drive inputs

```text
/content/gdrive_live/MyDrive/code_output/
  github_actions_stage/production/production_history.parquet
  jra_data/historical_odds_2025/races/*_odds.parquet
  jra_data/historical_odds_2025/races/*_horses.parquet
  jra_data/historical_odds_2025/payout_raw/*.html.gz
```

The 2025 retrospective predictions are rebuilt using only data through 2024-12-31. Do not use the fixed-2025 model or its 212 rounds.

## 1. 20-race smoke test

From `production/`:

```bash
python run_all_bet_oos_drive_2025.py
```

Then validate:

```bash
python validate_all_bet_oos_run.py \
  --manifest /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/drive_2025_run_manifest.json
```

## 2. Full 2025 batch

After the smoke gate passes, reuse the already validated OOS predictions:

```bash
python run_all_bet_oos_drive_2025.py --full --reuse-predictions
```

Then require all eight standard pools to have settled coverage:

```bash
python validate_all_bet_oos_run.py \
  --manifest /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/drive_2025_run_manifest.json \
  --full \
  --output-json /content/gdrive_live/MyDrive/code_output/jra_data/research_all_bet_oos_2025/full_quality_gate.json
```

`QUALITY_GATE=PASS` is required before interpreting ROI. It checks strict OOS provenance, real scored/settled samples, all eight pools on a full run, usable frame metadata for 枠連, and zero forward-ledger contact.

## Safeguards

- missing payout coverage is unresolved, never a loss;
- `票数なし` / missing quotes are unavailable, never zero odds;
- place/wide range odds use the lower bound for conservative EV;
- do not merge PR #47 or claim historical ROI until real Drive smoke/full runs pass;
- even after PASS, tiny-sample high ROI is not evidence: add uncertainty/condition analysis before changing app recommendations.
