# Frozen T10 retrospective benchmark: historical 2024 market calibration

Status: retrospective_same_timestamp_not_prospective_promotion
Source aligned audit: 2026-11-23T14:30:02+09:00
2024-selected market power: 1.05
Matched formal win races: 139

| Event | Races | Existing formal AI | Original T10 market | Calibrated T10 market | Candidate - existing AI | Whole-race CI 95% |
|---|---:|---:|---:|---:|---:|---|
| win | 139 | 0.05908193557066189 | 0.058993410156685494 | 0.058923978409916686 | -0.00015796 | [-0.00046219, 0.00013757] |
| top2 | 139 | 0.09902516708007036 | 0.0982326144209965 | 0.09807505810777321 | -0.00095011 | [-0.00183929, -1.133e-05] |
| top3 | 139 | 0.1319281120092658 | 0.13025318339731015 | 0.13024433705723082 | -0.00168377 | [-0.00336292, 9.9e-07] |

Lower is better. A negative candidate-minus-formal-AI delta favors calibration.
This is a retrospective comparison on originally frozen T10 inputs.
It is NOT a first-frozen prospective test of the new strategy or an ROI claim.
