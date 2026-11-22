# Market-anchored AI candidate — retrospective research

Pre-registered coefficient selection: 2024 only.
Selection: {"marketCalibrated": {"marketPower": 1.05, "historicalWeight": 0.0, "method": "linear"}, "historyAugmented": {"marketPower": 1.05, "historicalWeight": 0.02, "method": "linear"}}
2025 scored on 2023-trained model; 2026 scored on 2025-refitted model.

| Period | Outcome | Final-market Brier | Calibrated-market Brier | Market+history Brier | Market+history minus market |
|---|---|---:|---:|---:|---:|
| 2025 | win | 0.057254 | 0.057269 | 0.057270 | +0.000016 |
| 2025 | top2 | 0.097984 | 0.098221 | 0.098174 | +0.000190 |
| 2025 | top3 | 0.130188 | 0.130787 | 0.130647 | +0.000459 |
| 2026 | win | 0.057286 | 0.057279 | 0.057289 | +0.000003 |
| 2026 | top2 | 0.098123 | 0.098335 | 0.098301 | +0.000178 |
| 2026 | top3 | 0.129082 | 0.129603 | 0.129487 | +0.000405 |

Market benchmark uses *closing odds*, which are unavailable at T-10.
No direct production AI T-10 superiority is established.
Prospective results must remain a separate frozen cohort.
