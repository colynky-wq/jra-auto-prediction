# 2026-10-18 live recording repair

## Evidence and causes

- Morning Live Permanent 39081066111 succeeded. Afternoon run 39101459807 / job 117598919209 failed at publication, not prediction. Its retained artifact 12128807189 contains 9 pending files, all later attempts at Kyoto 10R (202608040710). SHA256 validation passed for all 118 retained observed blobs; the morning artifact 12126402634 has 354 valid blobs and no pending files.
- The afternoon checkout used the run's queued source dc8b0445a4d45dd3a67af2e19fe348ecc2500be7. It lacked the morning Kyoto 10R records, and the monitor's processed set reset on restart. The same 14:55 race was predicted again at 14:46. The first 14:43 forecasts and pool records were correctly protected by the publisher; 9 conflicting attempts remained pending, causing the final failure.
- `/wide_odds/` was missing from the publisher's immutable path policy. The 14:43:35 first odds archive was replaced by a 14:46:18 snapshot while the original 14:43:36 Wide-axis record stayed frozen. The different snapshot caused invalid result matching. The first odds bytes survive in the morning outbox and its published commit.
- Kyoto 3R (202608040703) had a valid normal forecast and odds at 10:43:39. The log records `SignalData refresh failed ... FileNotFoundError(2, 'No such file or directory')` at 10:43:39.948. `repair_wide_odds.py` and the normal publisher both rebuilt SignalData using the same `SignalData.json.tmp`; one replaced the temporary file before the other could. The early return skipped the subsequent Wide-axis first save. Neither outbox contains that Wide-axis record. It cannot be recreated after the race.

## Corrections

- Checkout current main after queueing, and resume normal publishing sessions from validated same-day RaceData identities and post times. Existing forecasts are not predicted again. Corrupted/duplicate/changed-post forecasts cause an explicit stop rather than silent replay.
- Use unique fsynced temporary files for SignalData writes. Freeze one race's Wide-axis evaluation from its own inputs before shared display generation, so an unrelated display write failure cannot erase the first-save opportunity. The scheduled-post guard remains enforced.
- Make per-race Wide odds immutable in publication, retain the already published first snapshot in the rotating cache, and reuse the immutable archive if that cache is lost. Use atomic first-writer links for archives and a short locked merge for cache writes.
- Existing immutable conflicts remain explicit failures and retain their outbox; no conflicting forecast is silently accepted. Today's original morning records are retained, and the later duplicate attempts are audited separately. The failed Actions run and health journal remain historical facts.

## Validation and recovery limits

167 local regression tests passed, including the actual monitor loop skipping an already published race in the overlap window, two simultaneous display writers, archive reuse without HTTP, simultaneous cache writers, Wide-axis preservation despite display-write failure, publisher conflict preservation, payout/entry/research contracts, and read-only outbox inspection. No model inference, training, manual workflow start/rerun, observation backfill or purchase was performed.

Recovery uses the original morning bytes for Kyoto 10R's forecast, first odds and signal; it does not create a forecast at a later time. Result evaluation and app projections may be rebuilt from those original inputs and already fetched independent results/payouts. Kyoto 3R stays missing. The nine later duplicate attempts must never be counted as nine new observations or overwrite first records. A subsequent normal session is required to verify the complete future live path; isolated tests are not actual race observations.
