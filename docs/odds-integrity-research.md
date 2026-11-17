# KeibaAI T-10 Odds Integrity / Wide Calibration (research only)

## Why this was added

Historical aligned records (105 races, generated 2026-11-15) show:
- 165 frozen comparable Wide candidates over 92 result-matched races.
- 52 tickets with estimated conservative return multiplier >=1.50, zero hits (about 5.33 hits expected from their frozen probabilities).
- A sample of 12 odds snapshots across 2026-11-07/08/14/15 had \`officialDatetime\` about 50-54 minutes before \`capturedAtJST\`.
- An example from 2026-11-08 Tokyo R4 has Wide pair 7-11 at 23.2-26.6x and 26.601% model probability (6.171x estimated low-end payout multiplier).

These are **alerts, not proof of stale odds or data corruption**. The netkeiba API field \`official_datetime\` is not confirmed to be the pair value update time. Do not label data as stale without independent source evidence.

## Run

From repository root:

\`\`\`bash
python -m unittest discover -s tests -p 'test_odds_integrity_audit.py' -v
python production/odds_integrity_audit.py --root . > odds-integrity-audit.json
python production/odds_integrity_audit.py --root . --strict-structure
\`\`\`

The script reads the existing repository only and prints a JSON report. It does not retrieve external data, modify predictions, turn on any betting recommendations, or adjust stakes. \`--strict-structure\` fails on malformed identities, pairs, input files, or invalid snapshots, **not** on ambiguous timestamp age alone.

### Meaning of the report

- \`sourceAgeMinutes\`: \`capturedAtJST - officialDatetime\`, not a certified market-data delay.
- \`source_timestamp_age_alert\`: a discrepancy that needs independent verification.
- \`incomplete_or_extra_pairs\`, \`pair_horse_mismatch\`: actual pair/entrants structural failures.
- \`wideReferenceResearch\`: frozen research-only common-Wide tickets; these include **skipped** races and cannot be conflated with executed or selected portfolio ROI.
- The aligned input gate requires \`projectionState=verified\` and \`aligned.countsAreCurrent=true\`; otherwise the audit aborts.
- A single race's Wide selections are correlated. Do not use ordinary independent-ticket binomial significance tests.

## Before changing live prediction or candidate selection

1. Clarify whether netkeiba \`official_datetime\` is the last data update or a separate feed timestamp. Compare independent JRA or netkeiba site values at the same clock times where available and permitted.
2. Independently validate high-EV candidate identifiers (4-digit zero-padded pair IDs) and live lower/upper odds against the actual pair on the visible betting page. Pay special attention to Tokyo R4, 2026-11-08, 7-11 and 11-14.
3. On an isolated research branch, calibrate joint Top3 pair probabilities vs outcomes. If coefficients need changing, preregister a new rule/version and validate prospectively; do not retune the frozen aligned cohort.
4. Avoid modifying the production frozen LightGBM files, formal protocol hashes, capture clock logic, or stake/recommendation flags until the provenance issue is resolved.

## Source files

- \`AlignedPortfolioReview.json\`
- \`reports/YYYYMMDD/wide_odds_live10/RACEID.json\`
- \`reports/YYYYMMDD/all_bet_v1/win/RACEID.json\`
- \`production/portfolio_shadow.py\`
- \`production/formal_wide_odds_runtime.py\`

All changes in this patch are isolated to read-only research auditing and CI checks.
