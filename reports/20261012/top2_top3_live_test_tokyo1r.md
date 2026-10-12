# KeibaAI Top2/Top3 Live Test Record — Tokyo 1R

- Date: 2026-10-12 (JST)
- Race: Tokyo 1R
- Race ID: `202605040501`
- Scheduled post time: 09:50 JST
- Intended live window: 8–12 minutes before post (09:38–09:42 JST)
- Workflow: `KeibaAI Live Monitor Top2 Top3`
- Requested inputs:
  - `target_date=20261012`
  - `mode=live`
  - `target_race_id=202605040501`
  - `max_runtime_minutes=15`
  - `poll_seconds=20`
  - `publish_to_app=true`

## Result

The live test was **not triggered**.

The connected GitHub interface available to the assistant could inspect repository files, workflow runs, jobs, and logs, but did not provide a safe workflow-dispatch action at the time of the test. To avoid changing repository behavior merely to force execution, no broader repository modification was made.

Therefore this attempt produced **no live prediction result** and the following checks were not evaluated:

- AI win probability output
- AI Top2 probability output
- AI Top3 probability output
- Top2 probability sum = 2
- Top3 probability sum = 3
- `P(win) <= P(Top2) <= P(Top3)` violations
- `RaceData.json` Top2/Top3 publication
- `RaceData.json` commit/push

## Safety status

- Existing win model modified: **NO**
- Top2/Top3 model files modified: **NO**
- Research thresholds modified: **NO**
- Prediction files modified: **NO**
- `RaceData.json` modified by this test attempt: **NO**

## Interpretation

This record means **the test could not be launched through the connected control path**. It does **not** indicate that the Top2/Top3 prediction code itself failed.
