# Frozen T10 retrospective benchmark: historical 2024 market calibration

Status: retrospective_same_timestamp_not_prospective_promotion
Source aligned audit: 2026-11-22T16:35:34+09:00
2024-selected market power: 1.05
Matched formal win races: 124

| Event | Races | Existing formal AI | Original T10 market | Calibrated T10 market | Candidate - existing AI | Whole-race CI 95% |
|---|---:|---:|---:|---:|---:|---|
| win | 124 | 0.05937141135533748 | 0.059264575937764644 | 0.059171921261521085 | -0.00019949 | [-0.0004997, 9.415e-05] |
| top2 | 124 | 0.10129391279787052 | 0.10058987727743794 | 0.10050346446563212 | -0.00079045 | [-0.00176306, 0.00019183] |
| top3 | 124 | 0.1342488993384963 | 0.13276100104017446 | 0.13281202758477995 | -0.00143687 | [-0.00312735, 0.00032371] |

Lower is better. A negative candidate-minus-formal-AI delta favors calibration.
This is a retrospective comparison on originally frozen T10 inputs.
It is NOT a first-frozen prospective test of the new strategy or an ROI claim.
