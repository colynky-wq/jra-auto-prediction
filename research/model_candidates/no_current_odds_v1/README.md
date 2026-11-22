# No-current-odds candidate (research only)

Training rows: 465,544; validation 2024: 45,130; 2025 historical holdout: 45,779.
Model refit through 2025, best rounds selected on 2024: 350.
The model uses 36 features and excludes current-race market logit.
The market benchmark below uses *final* odds unavailable at T-10.

| Event | Candidate Brier | Final-market Brier | Delta (candidate - final market) | Race bootstrap 95% |
|---|---:|---:|---:|---|
| win | 0.060612 | 0.057254 | +0.003357 | [0.0031213146219891013, 0.0041138965935802844] |
| top2 | 0.104617 | 0.097984 | +0.006634 | [0.006280794488401342, 0.007931135203807872] |
| top3 | 0.138528 | 0.130188 | +0.008339 | [0.007394052398507123, 0.00967252859860115] |

No promotion; compare strictly first-frozen future forecasts.
2025 has been examined by earlier project research and is not pristine evidence.
