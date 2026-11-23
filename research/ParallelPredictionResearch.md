# KeibaAI Parallel Prediction Research (read-only)

Status: quality_limited_research_only
Source strict audit: 2026-11-23T14:30:02+09:00
Formal AI / betting unchanged; no automatic candidate promotion.

## Track 1: Collection and verification
- Verified aligned races: 145
- Independently matched races: 139
- Remaining matched races for the 300-R research gate: 161
- Formal capture misses: 25
- Outstanding quality issues: 30
- Pending selected settlements: 5

## Track 2: Fixed historical probability diagnostics
Scored races (within registered first 200 only): 139
| Cohort | Event | Races | AI Brier | Market Brier | Fixed 50:50 Brier | AI minus market |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| first100 | win | 98 | 0.057347 | 0.057252 | 0.05729 | 5.621e-05 |
| first100 | top2 | 98 | 0.10025 | 0.099737 | 0.099918 | 0.00057063 |
| first100 | top3 | 98 | 0.135205 | 0.13441 | 0.134472 | 0.00105233 |
| confirmation101to200 | win | 41 | 0.056629 | 0.056488 | 0.056548 | 0.00016577 |
| confirmation101to200 | top2 | 41 | 0.088456 | 0.087076 | 0.087691 | 0.00132301 |
| confirmation101to200 | top3 | 41 | 0.117976 | 0.1144 | 0.115872 | 0.00316309 |

Lower Brier is better. A negative AI-minus-market delta favors AI.
These historical scores are not evidence that any new model improves future bets.

## Track 3: Prospectively frozen candidate validation
- Immutable T-10 shadow records: 41
- Independently result-matched shadows: 36
- Current evidence status: insufficient_prospective_evidence
- 100 prospective matched races is only a descriptive review milestone.
| Event | Paired races | Market Brier | 25%-AI Brier | 50%-AI Brier | Existing AI Brier |
| --- | ---: | ---: | ---: | ---: | ---: |
| win | 36 | 0.06012563 | 0.06015209 | 0.06018364 | 0.06026197 |
| top2 | 36 | 0.08982209 | 0.09005252 | 0.09032148 | 0.09097502 |
| top3 | 36 | 0.11742017 | 0.11795621 | 0.11865494 | 0.12054047 |

Do not infer profitability or promote a model from these descriptive comparisons.
Audit quality issues remain visible; the 300-R beta gate is separate.
