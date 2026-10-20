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

Before research starts, the archive is compared with `race_manifest_2025.csv`. A non-contiguous archive fails closed. `--full` means every race currently available in the audited archive; it does **not** automatically mean the full 2025 calendar year.

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

## 2. Full currently available archive

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

`QUALITY_GATE=PASS` is required before interpreting ROI. It checks strict OOS provenance, real scored/settled samples, all eight pools on a full available-archive run, usable frame metadata for 枠連, race-level losing-streak/trigami semantics, explicit archive scope, and zero forward-ledger contact.

## Race-level portfolio metrics

For each bet type and EV threshold, every settled quoted combination above the threshold is treated as a 100-yen ticket. The summary therefore evaluates the **whole race portfolio**, not just isolated winning tickets.

- `hit_races`: at least one selected ticket hit in the race.
- `profitable_races`: the race return exceeded the total stake for that bet type.
- `non_loss_races`: the race return was at least the total stake.
- `trigami_races`: at least one ticket hit, but total race return was below total race stake.
- `trigami_rate_among_hit_races`: trigami races divided by hit races.
- `max_losing_streak`: consecutive losing **races**, not the arbitrary ordering of tickets inside a race.
- `mean_tickets_per_settled_race`: average selected ticket count per settled race.

This is essential for the project's trigami-avoidance goal. A high ticket hit rate is not useful if the combined race portfolio frequently loses money.

## Safeguards

- missing payout coverage is unresolved, never a loss;
- `票数なし` / missing quotes are unavailable, never zero odds;
- place/wide range odds use the lower bound for conservative EV;
- never label a partial archive as a full-calendar-year result;
- do not merge PR #47 or claim historical ROI until real Drive smoke/full runs pass;
- even after PASS, tiny-sample high ROI is not evidence: add uncertainty/condition analysis before changing app recommendations;
- WIN5 remains a separate five-race research path.
