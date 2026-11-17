# KeibaAI T-10 Odds Integrity / Wide Calibration (research only)

## Why this was added

Historical aligned records (105 races, generated 2026-11-15) show:
- 165 frozen comparable Wide candidates over 92 result-matched races.
- 52 tickets with estimated conservative return multiplier >=1.50, zero hits (about 5.33 hits expected from their frozen probabilities).
- All 105 aligned odds snapshots across 2026-11-07/08/14/15 had `officialDatetime` 46.33-54.58 minutes before `capturedAtJST`.
- Comparing earlier archived observations with the formal T-10 archive yielded 86 genuinely separate observations: 72 with changed pair quotes AND changed `officialDatetime`, and 14 with unchanged pair quotes AND unchanged `officialDatetime`. A further 19 had the same/near capture time and are not independent temporal observations.
- An example from 2026-11-08 Tokyo R4 has Wide pair 7-11 at 23.2-26.6x and 26.601% model probability (6.171x estimated low-end payout multiplier).

Public provider statements support a **plausible mechanism**: netkeiba described an approximately hourly update cadence for non-premium users (2018 notice; current-site free-version wording likewise says hourly). The recorded API status was "middle" with `officialDatetime` around 50 minutes before capture. See:
- https://info.netkeiba.com/?id=118&pid=info_detail
- https://race.sp.netkeiba.com/race/shutuba.html?race_id=202610010604&rf=rs
- JRA official odds availability (candidate independent comparison source): https://www.jra.go.jp/faq/pop02/1_1.html

A further cross-check in 2026-11-08 Tokyo R4 highlights why a stale quote matters: stored T-10 source single win odds had #7=3.9 and #11=3.1, whereas the published *post-race* market final odds were #7=2.4 and #11=17.8. These final odds are **not** substitute T-10 prices and must never be used retrospectively as genuine live forecasts:
- https://pc.keibalab.jp/db/race/202611080504/odds.html?kind=tan

These are **alerts, not proof of stale odds or data corruption**. The netkeiba API field `official_datetime` is not confirmed to be the pair value update time. Do not label data as stale without independent source evidence.

## Run

From repository root:

```bash
python -m unittest discover -s tests -p 'test_odds_integrity_audit.py' -v
python production/odds_integrity_audit.py --root . > odds-integrity-audit.json
python production/odds_integrity_audit.py --root . --strict-structure
```

The script reads the existing repository only and prints a JSON report. It does not retrieve external data, modify predictions, turn on any betting recommendations, or adjust stakes. `--strict-structure` fails on malformed identities, pairs, input files, or invalid snapshots, **not** on ambiguous timestamp age alone.

### Meaning of the report

- `sourceAgeMinutes`: `capturedAtJST - officialDatetime`, not a certified market-data delay.
- `source_timestamp_age_alert`: a discrepancy that needs independent verification.
- `researchOddsReliability`: false/hold for monetizable edge claims until independently observed fresh T-10 quotes exist. This is an informational audit gate, not a change to the formal prediction or capture protocol.
- `incomplete_or_extra_pairs`, `pair_horse_mismatch`: actual pair/entrants structural failures.
- `favorite_pair_wide_price_alert`: research warning (not a confirmed key-mapping error) when both horses were among the three most popular win horses but the Wide lower quote is >=15x. Verify against an independent source.
- `wideReferenceResearch`: frozen research-only common-Wide tickets; these include **skipped** races and cannot be conflated with executed or selected portfolio ROI.
- The aligned input gate requires `projectionState=verified` and `aligned.countsAreCurrent=true`; otherwise the audit aborts.
- A single race's Wide selections are correlated. Do not use ordinary independent-ticket binomial significance tests.

## Before changing live prediction or candidate selection

1. Confirm whether netkeiba `official_datetime` is the quote update time for this specific API feed. Archive two timestamped source observations and compare price changes; validate against JRA official odds (or appropriately licensed realtime feed) at the same clock time. Do not bypass access controls or rely on after-race final prices as T-10 observations.
2. Independently validate high-EV candidate identifiers (4-digit zero-padded pair IDs) and live lower/upper odds against the actual pair on the visible betting page. Pay special attention to Tokyo R4, 2026-11-08, 7-11 and 11-14.
3. On an isolated research branch, calibrate joint Top3 pair probabilities vs outcomes. If coefficients need changing, preregister a new rule/version and validate prospectively; do not retune the frozen aligned cohort.
4. Avoid modifying the production frozen LightGBM files, formal protocol hashes, capture clock logic, or stake/recommendation flags until the provenance issue is resolved.

## Source files

- `AlignedPortfolioReview.json`
- `reports/YYYYMMDD/wide_odds_live10/RACEID.json`
- `reports/YYYYMMDD/all_bet_v1/win/RACEID.json`
- `production/portfolio_shadow.py`
- `production/formal_wide_odds_runtime.py`

All changes in this patch are isolated to read-only research auditing and CI checks.

## Historic final single-win odds drift (post hoc only)

A separate read-only script `production/final_odds_drift_audit.py` compares
the frozen `reports/YYYYMMDD/all_bet_v1/win/RACEID.json` odds against
`reports/YYYYMMDD/ResultData.json`'s `horses[].finalOdds`.
It reports paired horse coverage, median absolute odds changes, and changes
to the favorite. This is a diagnostic of how far the frozen market quote
was from the published final quote; it does **not** prove stale feed values,
and it is **not** a backtest using odds that were known before the race.

Run:
```bash
python -m unittest discover -s tests -p 'test_final_odds_drift_audit.py' -v
python production/final_odds_drift_audit.py --root . > final-odds-drift.json
```
The report explicitly marks `finalOddsUsedAsPreRace=false`.
Never use it to promote a shadow ticket, set buy/no-buy gates, or claim
that a final quote was available before betting ended.
