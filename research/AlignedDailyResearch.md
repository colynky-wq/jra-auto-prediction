# KeibaAI aligned daily research (read-only)

**Source audit:** 2026-11-07T20:49:20+09:00
**Status:** verified input, research-only. No model/selection/betting changes.

## Cohort and outcome
- Aligned races / matched results: 35 / 35
- Selected / skipped races: 11 / 24
- Settled selected / pending selected: 11 / 0
- Paper tickets: 22 (Wide 19, trio 3)
- Paper stake / return / profit (JPY): 2200 / 1310 / -890
- Return multiple: 0.595455
- Predicted vs actual winning tickets: 1.7626 vs 2

## H1: frozen common-Wide reference, selected vs skipped races

| Cohort | Tickets | Expected wins | Actual wins | Stake (JPY) | Return (JPY) | Return multiple |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Selected | 19 | 1.5861 | 2 | 1900 | 1310 | 0.689474 |
| Skipped | 34 | 3.4952 | 2 | 3400 | 2970 | 0.873529 |
| All | 53 | 5.0813 | 4 | 5300 | 4280 | 0.807547 |

Zero-reference-candidate race counts (never dropped from cohort): {'selected': 0, 'skipped': 2}

## H2: observed Wide performance by frozen battle grade

| Grade | Tickets | Expected wins | Actual wins | Return multiple |
| --- | ---: | ---: | ---: | ---: |
| A | 11 | 0.7135 | 0 | 0.0 |
| S | 8 | 0.8726 | 2 | 1.6375 |

## H3: largest-return-race concentration

| Excluding top returned races | Races remaining | Return multiple |
| --- | ---: | ---: |
| 0 | 11 | 0.595455 |
| 1 | 10 | 0.0 |
| 2 | 9 | 0.0 |

## H4: incremental trio tickets

- Trio-only incremental: 3 tickets; 300 JPY stake, 0 JPY return.
- Wide legs in those same races: 4 tickets; 3.275 return multiple.

## Probability reliability / daily trends

Detailed, fixed-bin reliability, venue, date, grade/chaos, frozen-odds, and scope comparisons are in the sibling JSON file.

**Interpretation guardrails:** Multiple legs from the same race are correlated. Do not infer calibration, a better optimized rule, or profitable betting from a short run. No beta enabling or backtest promotion.
