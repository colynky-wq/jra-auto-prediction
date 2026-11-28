# KeibaAI Parallel Prediction Research (read-only)

Status: quality_limited_research_only
Source strict audit: 2026-11-28T14:56:33+09:00
Formal AI / betting unchanged; no automatic candidate promotion.

## Track 1: Collection and verification
- Verified aligned races: 150
- Independently matched races: 146
- Remaining matched races for the 300-R research gate: 154
- Formal capture misses: 36
- Outstanding quality issues: 42
- Pending selected settlements: 3

## Track 2: Fixed historical probability diagnostics
Scored races (within registered first 200 only): 146
| Cohort | Event | Races | AI Brier | Market Brier | Fixed 50:50 Brier | AI minus market |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| first100 | win | 98 | 0.057347 | 0.057252 | 0.05729 | 5.621e-05 |
| first100 | top2 | 98 | 0.10025 | 0.099737 | 0.099918 | 0.00057063 |
| first100 | top3 | 98 | 0.135205 | 0.13441 | 0.134472 | 0.00105233 |
| confirmation101to200 | win | 48 | 0.057306 | 0.057234 | 0.057259 | 0.00010402 |
| confirmation101to200 | top2 | 48 | 0.090396 | 0.089337 | 0.089797 | 0.00101828 |
| confirmation101to200 | top3 | 48 | 0.120196 | 0.117271 | 0.118427 | 0.00252109 |

Lower Brier is better. A negative AI-minus-market delta favors AI.
These historical scores are not evidence that any new model improves future bets.

## Track 3: Prospectively frozen candidate validation
- Immutable T-10 shadow records: 46
- Independently result-matched shadows: 43
- Current evidence status: insufficient_prospective_evidence
- 100 prospective matched races is only a descriptive review milestone.
| Event | Paired races | Market Brier | 25%-AI Brier | 50%-AI Brier | Existing AI Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| win | 43 | 0.06067477 | 0.06068468 | 0.06070002 | 0.06074698 |
| top2 | 43 | 0.09201427 | 0.09217046 | 0.0923626 | 0.09285472 |
| top3 | 43 | 0.12043388 | 0.12080095 | 0.12132508 | 0.12284449 |

Do not infer profitability or promote a model from these descriptive comparisons.
Audit quality issues remain visible; the 300-R beta gate is separate.
