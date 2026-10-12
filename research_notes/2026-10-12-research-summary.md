# KeibaAI Research Summary — 2026-10-12

Research branch: `research/wide-budget-model-v1`

All analyses below are research-only. Production models and live publishing were not modified. Unless otherwise noted, 2026 data was excluded. The clean protocol used train <= 2023, 2024 for early stopping / cutoffs, and untouched 2025 for final evaluation.

## 1. Wide joint probability

2025 untouched test:

- Horse Top3 logloss: market 0.40611941 -> AI 0.39997712 (improvement 0.00614229)
- Horse Top3 Brier: market 0.13025617 -> AI 0.12840051 (improvement 0.00185566)
- Wide pair logloss: market 0.11033572 -> AI 0.10886810 (improvement 0.00146762)
- Wide pair Brier: market 0.02787975 -> AI 0.02761248 (improvement 0.00026727)
- Top3 pair capture: market 25.8747% -> AI 26.1855%
- Top5 pair capture: market 36.8622% -> AI 37.0426%
- 3,325 races / 309,750 unordered pairs used.

Interpretation: joint Wide probabilities are internally coherent and modestly better than the market-derived baseline, but ROI was not evaluated because historical T-10 Wide odds are not available in `production_history.parquet`.

## 2. Chaos score (荒れ度)

The first transparent chaos score combines favorite uncertainty, market entropy, favorite closeness, AI-market disagreement, longshot Top3 signal, and field size. Grade cutoffs came from 2024 only; 2025 was untouched.

2025:

| Grade | Races | Favorite win | Winner pop >=6 | Top3 includes pop >=8 | Top3 includes pop >=10 |
|---|---:|---:|---:|---:|---:|
| ★☆☆☆☆ | 607 | 51.89% | 9.88% | 25.04% | 12.36% |
| ★★☆☆☆ | 672 | 34.82% | 14.43% | 32.44% | 15.92% |
| ★★★☆☆ | 733 | 31.65% | 15.55% | 38.61% | 20.46% |
| ★★★★☆ | 618 | 28.96% | 20.06% | 41.59% | 22.49% |
| ★★★★★ | 700 | 20.00% | 28.00% | 50.57% | 29.29% |

Interpretation: the score shows a strong monotonic relationship with upset-like outcomes in 2025. This is a good candidate for future app display after additional forward checks.

## 3. First opportunity score (confidence-heavy)

The first version of 勝負度 mostly tracked favorite strength, not model value. Example: S-grade AI top win rate was 48.74%, but the AI top horse was the same as the market favorite in 99.53% of those races. Retrospective flat-win return index was 0.8445.

Decision: do not use this version in the app as the final 勝負度.

## 4. Value-oriented opportunity score

A second score was built to focus on AI-vs-market ratio and absolute probability edge, with only a small probability-support component. Grade cutoffs again came from 2024; 2025 was untouched.

2025 one-candidate-per-race results:

| Grade | Races | Win | Top3 | Mean AI/market | Mean final odds | Retrospective return index | 95% day-block CI |
|---|---:|---:|---:|---:|---:|---:|---:|
| 見送り | 632 | 20.25% | 48.89% | 1.063 | 8.96 | 0.754 | [0.561, 1.024] |
| C | 685 | 16.79% | 43.21% | 1.102 | 10.93 | 0.791 | [0.597, 1.003] |
| B | 675 | 14.96% | 36.89% | 1.140 | 12.65 | 0.737 | [0.534, 0.968] |
| A | 667 | 16.79% | 36.73% | 1.182 | 13.98 | 1.173 | [0.830, 1.555] |
| S | 676 | 13.46% | 34.32% | 1.270 | 15.46 | 1.104 | [0.806, 1.424] |

Transparent threshold check:

- AI/market >= 1.2 and AI win probability >= 2%: 797 races, return index 1.150, 95% CI [0.836, 1.502].
- AI/market >= 1.3 and AI win probability >= 2%: 219 races, return index 1.505, 95% CI [0.747, 2.389].

Interpretation: value-oriented grades are more aligned with the intended meaning of 勝負度, but uncertainty is still large and all 95% intervals for promising groups include 1.0. These are signals to forward-test, not proof of profit.

## 5. Longshot signal

For 2025 horses with popularity >=6, final win odds >=8, and AI win probability >=1%:

- AI/market <1.0: 8,517 horses, win 2.79%, Top3 13.55%, return index 0.781.
- 1.0-1.1: 5,019 horses, win 3.55%, Top3 14.88%, return index 0.843.
- 1.1-1.2: 2,265 horses, win 4.02%, Top3 15.94%, return index 0.996.
- 1.2-1.4: 893 horses, win 4.26%, Top3 15.01%, return index 1.180.
- >=1.4: 50 horses, win 6.00%, Top3 20.00%, return index 2.754 (very small sample / high variance).

Interpretation: AI/market ratio is promising as a longshot-ranking signal, especially around 1.2+, but the most extreme bucket is too small to trust.

## 6. Running style (脚質)

The current production feature engine already has a leakage-safe pre-race feature `脚質初角`, based on the horse's historical first-corner position rate. The current race's first-corner position was used only for evaluation.

2025:

- 46,282 horses / 3,335 races.
- Exact 4-class agreement (逃げ / 先行 / 差し / 追込): 35.76%.
- Within one adjacent class: 87.19%.
- Mean absolute first-corner-rate error: 0.2138.
- Horses with >=4 prior runs: exact 37.40%, within-one 88.54%, error 0.2027.
- First-time starters: exact 25.51%, within-one 86.54%, error 0.2595.
- Race-level predicted front-runner count MAE: 2.62 horses.

Interpretation: this is usable as a soft "running-style tendency" label, especially for experienced horses, but it is not accurate enough to call a full pace model. First-time and low-history horses need an uncertainty marker. A true pace model needs richer corner/sectional information.

## Decisions / next steps

1. Keep production untouched while live validation is active.
2. Treat 荒れ度 as the most mature new race-level app signal; continue forward validation before production integration.
3. Reject the confidence-heavy 勝負度 v1. Continue with the value-oriented definition and T-10 forward odds.
4. Display 脚質 initially as a tendency with confidence/experience, not as a definitive pace forecast.
5. Start/extend forward capture of all relevant bet-type odds, especially T-10 Wide odds, before budget optimization.
6. Use joint Wide probabilities plus live Wide odds to evaluate expected value and eventually build budget-based ticket plans.
