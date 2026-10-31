# Same-Day Track Bias Shadow V1

## Purpose

Evaluate whether already-completed races from the same race day, venue and surface improve later-race rankings without changing the fixed production model.

## Safety / validation rules

- Formal A prediction is never modified.
- Only races with scheduled start time strictly earlier than the target race are eligible.
- Only completed `ResultData.json` races are used.
- Same venue and same surface only.
- V1 uses horse-number draw outcome tendency plus favorite/longshot outcome tendency.
- V1 does **not** call draw-number tendency an actual inside/outside path bias.
- Passing positions, sectional times and actual path data remain future V2 inputs.
- Challenger adjustment is bounded and written only as `sameDayShadowScore` / `sameDayShadowRank`.

## Runtime

```bash
python production/same_day_shadow_runtime.py
```

Optional date override:

```bash
KEIBAAI_SAME_DAY_DATE=20261101 python production/same_day_shadow_runtime.py
```

Outputs:

- `SameDayTrackBiasShadow.json`
- `reports/YYYYMMDD/SameDayTrackBiasShadow.json`

## Interpretation

`sameDayBias.drawBiasScore` is:

`outer-half top-3 rate - inner-half top-3 rate`

Positive values indicate that outer horse numbers have overperformed so far that day; negative values indicate inner horse numbers have overperformed. This is a draw result statistic, **not** proof of racing-lane bias.

Confidence is deliberately capped at 0.65 because V1 lacks passing-position / sectional evidence.

## Prospective comparison

For each later race compare:

1. Formal A ranking (`aiProbability`)
2. Same-Day Shadow ranking (`sameDayShadowRank`)
3. Final result

Do not promote Same-Day V1 into the fixed model until enough forward races show improvement without leakage.
