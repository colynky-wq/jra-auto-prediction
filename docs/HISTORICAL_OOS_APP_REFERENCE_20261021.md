# Historical OOS evidence shown in KeibaAI (2026-10-21)

## Status

The 2025 historical all-bet strict-OOS research run completed with `QUALITY_GATE=PASS`.

- Method: `drive-2025-strict-oos-all-bet-final-v5-20261021`
- Study window: 2025-01-05 through 2025-09-20 (contiguous partial-year prefix)
- Processed races: 2,442
- Settled/scored samples: 7,897,709
- Pools: win, place, frame quinella, quinella, wide, exacta, trio, trifecta
- Day-block bootstrap: 2,000 replicates, 280 EV-threshold × Top-K configurations (275 non-empty)
- Forward ledgers touched by the historical run: false

This is technical eligibility for interpretation, not proof of live profitability.

## Neutral reference configuration shown in the app

The app displays one common reference configuration for all eight pools: model EV >= 1.00 and at most one ticket per race. This is a neutral comparison row, not a selected production strategy.

| Pool | Bets | ROI | 95% day-bootstrap ROI interval |
|---|---:|---:|---:|
| Win | 172 | 212.1% | 90.7–353.9% |
| Place | 440 | 86.7% | 78.9–95.1% |
| Frame quinella | 2,196 | 95.7% | 79.4–112.8% |
| Quinella | 2,375 | 89.4% | 60.1–121.8% |
| Wide | 2,311 | 88.8% | 77.4–100.5% |
| Exacta | 2,427 | 67.2% | 38.3–100.9% |
| Trio | 2,436 | 65.1% | 50.0–81.3% |
| Trifecta | 2,442 | 47.9% | 7.1–113.7% |

Across all 280 configurations, zero had a 95% ROI interval lower bound above 100%.

## Deployment rule

The iOS app remains reference-only:

- `formalRecommendationsEnabled = false`
- `autoBetEnabled = false`
- `stakeSizingEnabled = false`

Historical saved odds are closing/final quotes and are not equivalent to the executable quote available about 10 minutes before post time. A ticket strategy must reproduce its advantage on forward pre-race snapshots before it can be promoted to formal recommendation status.

The app therefore enriches the existing bet-research screen with the historical OOS evidence while keeping the existing safety switches off.
