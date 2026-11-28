# Frozen T10 retrospective benchmark: historical 2024 market calibration

Status: retrospective_same_timestamp_not_prospective_promotion
Source aligned audit: 2026-11-28T14:56:33+09:00
2024-selected market power: 1.05
Matched formal win races: 146

| Event | Races | Existing formal AI | Original T10 market | Calibrated T10 market | Candidate - existing AI | Whole-race CI 95% |
|---|---:|---:|---:|---:|---:|---|
| win | 146 | 0.05928135575726732 | 0.05920942701395438 | 0.05916036171783869 | -0.00012099 | [-0.00040809, 0.00016398] |
| top2 | 146 | 0.09919281158048747 | 0.09847501321866656 | 0.09834125706149041 | -0.00085155 | [-0.00174665, 5.823e-05] |
| top3 | 146 | 0.13206071022826124 | 0.13052550223991913 | 0.13053616009849725 | -0.00152455 | [-0.00312888, 6.923e-05] |

Lower is better. A negative candidate-minus-formal-AI delta favors calibration.
This is a retrospective comparison on originally frozen T10 inputs.
It is NOT a first-frozen prospective test of the new strategy or an ROI claim.
