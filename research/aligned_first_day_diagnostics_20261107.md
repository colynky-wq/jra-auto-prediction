# KeibaAI aligned T-10: first-day research-only diagnostics (2026-11-07)

**Status:** diagnostic snapshot, not a strategy revision, validation milestone, trading instruction, or evidence that profitability is established.

## Sources and scope
- `AlignedPortfolioReview.json` on main, generated `2026-11-07T20:49:20+09:00`: `projectionState=verified`, `aligned.countsAreCurrent=true`, 35 formal captures, 35 result matches, 11 selected settled races, zero recorded quality issues.
- `reports/20261107/ResultData.json`: 35 same-date official result receipts.
- `production/ALIGNED_PORTFOLIO_PROTOCOL.json`: frozen, registered `aligned-portfolio-confirmation-v1-20261102`. This memo applies the pre-registered H1–H4 framing **without changing any selection, predictive inputs, source hashes, frozen odds, stake or rules**.
- Financial numbers below represent *hypothetical 100-yen-per-leg paper results*, not actual wagers.

## Actual plan, all tickets
| Diagnostic | Observation |
| --- | ---: |
| Aligned races / results matched | 35 / 35 |
| Selected races / skip | 11 / 24 |
| Settled selected races | 11 |
| Selected legs | 22 (19 Wide + 3 trio) |
| Winning legs / winning races | 2 / 1 |
| Predicted winning-leg count, sum of frozen leg probabilities | 1.763 |
| Simulated stake / return / profit | JPY 2,200 / JPY 1,310 / JPY -890 |
| Observed return multiple | 0.5955 (59.55%) |
| Sum of frozen probability × conservative lower odds × unit stake | ~JPY 2,925 (1.3295x stake) |

The 2 winning legs were **both in Kyoto 7R**. They are *correlated outcomes in one race*, not two independent successful race predictions. The aggregate actual winning-leg count (2) is close to its frozen probability sum (1.763), which **does not establish probability calibration** from one small day. Projected return is conditional on frozen odds floors; payout can change before settlement.

## Frozen-hypothesis diagnostic slices

### H1 — participation filter vs the same Wide reference
Same fixed common-Wide reference among *all* races (max two candidates/race, using the registered probability/expected-value rule):
- Selected-race reference: 19 tickets, 2 hits, JPY 1,900 stake / JPY 1,310 return = **68.95%**, expected hit count 1.586.
- Skipped-race reference: 34 tickets, 2 hits, JPY 3,400 hypothetical stake / JPY 2,970 return = **87.35%**, expected hit count 3.495.
- Combined: 53 tickets, 4 hits, 80.75% observed return, expected hit count 5.081.

The selected-race reference does **not** outperform skipped-race reference on this day. This is diagnostic only: nonselected hypothetical tickets are not an executable historical strategy, and one date cannot establish filter effectiveness. Races with no qualifying references remain in the race cohort and are not silently relabelled.

### H2 — grade S vs A (Wide legs only)
- **A:** 11 Wide legs, 0 hits, JPY 1,100 stake, 0 return; summed predicted hit probability 0.713.
- **S:** 8 Wide legs, 2 hits (same Kyoto 7R), JPY 800 stake, JPY 1,310 return = **163.75%**; summed predicted hit probability 0.873.
- Neither a permanent S-only rule nor elimination of A is justified from these observations.

### H3 — Chaos5 × BattleA and largest-return dependence
- Chaos5 × BattleA: 9 selected Wide legs, 0 hits; summed predicted hits 0.525; JPY 900 stake and 0 return.
- **All** selected-plan payout (JPY 1,310) came from one race (Kyoto 7R). Removing the highest-return race leaves zero payout among the other 10 selected races. This is a concentration warning, not grounds for changing a preregistered rule.

### H4 — added trio leg
- Wide only: 19 bets, 2 hits, JPY 1,900 stake, JPY 1,310 returned = **68.95%**.
- Trio increment: 3 bets, 0 hits, JPY 300 added stake, JPY 0 added return.
- Overall mixed plan: 22 bets, 59.55% returned. The trio's incremental performance is an early sample, **not** proof to remove it.

## Research judgment and next checks
1. **Immediate explanation:** The published result pipeline is functioning and the initial shortfall is dominated by payoff concentration / one-day variance. Prediction-quality or odds-overvaluation errors remain plausible and *not yet isolated*.
2. **Probability diagnostics:** Keep `sum(p)` vs actual hits and reliability bins for Wide and trio *separately*. Wide/trio legs within a race are correlated; do not use naïve binomial confidence statements.
3. **Selection diagnostic:** Continue exactly registered H1 comparison on subsequent frozen races, including zero-candidate races in the observed cohort.
4. **Return stability:** Report daily/venue/grade return and largest-one/two-winner removal, explicitly labelling such subsets as *diagnostics, not newly fitted policies*.
5. **Milestones:** Retain first 100 as diagnostic only, 101–200 confirmation under frozen rules, beta only at `>=300` verified aligned and matched races **plus** other registered acceptance and quality conditions. No live strategy or automatic bet changes.

**Limitations:** 35 race observations, 11 selected races and 22 correlated legs on a single date have high uncertainty. No profitability claim, retrospective refitting, simulated capture promotion or risk-free expected return claim is permitted.
