# KeibaAI Parallel Prediction Research (read-only)

Status: quality_limited_research_only
Source strict audit: 2026-11-22T16:35:34+09:00
Formal AI / betting unchanged; no automatic candidate promotion.

## Track 1: Collection and verification
- Verified aligned races: 128
- Independently matched races: 124
- Remaining matched races for the 300-R research gate: 176
- Formal capture misses: 24
- Outstanding quality issues: 28
- Pending selected settlements: 3

## Track 2: Fixed historical probability diagnostics
Scored races (within registered first 200 only): 124
| Cohort | Event | Races | AI Brier | Market Brier | Fixed 50:50 Brier | AI minus market |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| first100 | win | 98 | 0.057347 | 0.057252 | 0.05729 | 5.621e-05 |
| first100 | top2 | 98 | 0.10025 | 0.099737 | 0.099918 | 0.00057063 |
| first100 | top3 | 98 | 0.135205 | 0.13441 | 0.134472 | 0.00105233 |
| confirmation101to200 | win | 26 | 0.057264 | 0.056982 | 0.057111 | 0.00029765 |
| confirmation101to200 | top2 | 26 | 0.093452 | 0.092135 | 0.09272 | 0.00120689 |
| confirmation101to200 | top3 | 26 | 0.121514 | 0.117822 | 0.119335 | 0.00312966 |

Lower Brier is better. A negative AI-minus-market delta favors AI.
These historical scores are not evidence that any new model improves future bets.

## Track 3: Prospectively frozen candidate validation
- Immutable T-10 shadow records: 24
- Independently result-matched shadows: 21
- Current evidence status: insufficient_prospective_evidence
- 100 prospective matched races is only a descriptive review milestone.
| Event | Paired races | Market Brier | 25%-AI Brier | 50%-AI Brier | Existing AI Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| win | 21 | 0.06253553 | 0.06259666 | 0.06266348 | 0.06281414 |
| top2 | 21 | 0.09773365 | 0.09789765 | 0.09810026 | 0.09862131 |
| top3 | 21 | 0.1230618 | 0.12356104 | 0.12423551 | 0.12611013 |

Do not infer profitability or promote a model from these descriptive comparisons.
Audit quality issues remain visible; the 300-R beta gate is separate.
