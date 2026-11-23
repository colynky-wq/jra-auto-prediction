# Wide walk-forward calibration: odds freshness and matched-data comparison

- Run: https://github.com/colynky-wq/jra-auto-prediction/actions/runs/45155928858
- Research PR: https://github.com/colynky-wq/jra-auto-prediction/pull/160
- Evaluation cutoff: 2026-11-23; data are committed, date-scoped historical snapshots.
- Verified aligned review: `projectionState=verified`, `aligned.countsAreCurrent=true`.
- 146 matched formal pre-race prediction records loaded. First two chronological race days (58 records) reserved as initial training. Last five days (88 records) evaluated out of sample; each test day is calibrated using strictly earlier race days.
- All evaluated strategies use the same odds-age eligibility check. Odds age = `wideEvaluation.capturedAtJST - wideEvaluation.officialDatetime` (JST, minutes). Missing/invalid time is always excluded.
- Non-finishing horses or missing/null actual finish ranks are not counted as top-three hits.
- Payouts below are simulated from `100 yen × saved pre-race wideOddsLow`; they are **not settled payouts**, not real bets and not an accuracy/ROI guarantee.

## 30-minute maximum age

All **88 evaluation races excluded as stale**; 0 eligible races. Neither baseline nor calibration can be evaluated with this age criterion. Do not interpret 0 bets as a profitability result.

## 60-minute maximum age (also same as unrestricted for evaluated races)

| Strategy | Races | Tickets | Stake | Proxy return | Proxy ROI |
|---|---:|---:|---:|---:|---:|
| Baseline AI axis + 3 highest AI-vs-market top3 edges (popular rank >=6) | 88 | 263 | 26,300 yen | 15,260 yen | 58.02% |
| Calibrated probability × lower wide odds >=1.10, top 3 | 88 | 165 | 16,500 yen | 880 yen | 5.33% |
| Calibrated probability × lower wide odds >=1.10, top 5 | 88 | 202 | 20,200 yen | 880 yen | 4.36% |
| Calibrated top 3 and original AI pair probability >=5% | 88 | 72 | 7,200 yen | 880 yen | 12.22% |

Calibration is a band-wise multiplicative adjustment, fit strictly on preceding dates, with shrinkage toward no adjustment. Training and test results are dependent within races: do not treat bets as independent trials.

## Decision

**No deployment.** None of the studied policies surpassed a 100% proxy return and all calibrated variants performed materially worse than baseline in the held-out evaluation. The strict 30-minute freshness rule currently leaves no evaluable data. Do not widen that limit for real wagering based only on this exploratory result.

## Quality verification

GitHub Actions execution succeeded, and six regression tests passed: correct JST age calculation, rejection of missing/reversed timestamps, rejecting null/missing/non-integer finish ranks, consistent race-age gating across strategies, unknown-age rejection, strict previous-date training, and probability-prior fallback (covered in six tests).

Keep PR draft, no code changes to production prediction or betting paths.
