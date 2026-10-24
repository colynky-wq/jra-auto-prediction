from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pandas as pd

import live_monitor_dry_run as base
import live_monitor_topk as topk
import live_monitor_topk_publish as publish


def resilient_main() -> None:
    """Live monitor that never marks a transient failure as processed.

    A race is completed only after predict_live_race_topk returns successfully.
    Failures are retried on every poll while 8-12 minutes remain before post.
    This fixes the old fail-once/skip-forever behavior.
    """
    if base.MODE != "live":
        return base.main()
    if base.now_jst().strftime("%Y%m%d") != base.TARGET_DATE8:
        base.fail("live mode target date must equal the current JST date")

    races = base.fetch_schedule(base.TARGET_DATE8)
    if base.TARGET_RACE_ID:
        races = [r for r in races if r["race_id"] == base.TARGET_RACE_ID]
    flat = [r for r in races if not (r.get("surface") == "障" or "障害" in str(r.get("race_name", "")))]
    if not flat:
        base.fail("no flat races found")
    base.print_schedule(races)

    if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
        try:
            from pace_recording_events import save_schedule
            save_schedule(base.TARGET_DATE8, flat)
        except Exception as exc:
            print("RECORDING_SCHEDULE_SKIPPED", repr(exc))

    target_date = pd.Timestamp(datetime.strptime(base.TARGET_DATE8, "%Y%m%d").date())
    hist = base.load_history(target_date)
    deadline = base.now_jst() + timedelta(minutes=base.MAX_RUNTIME_MINUTES)
    processed: set[str] = set()
    if os.environ.get("KEIBAAI_PUBLISH_RACEDATA", "").lower() in {"true", "1"}:
        from live_recording_resume import published_race_ids
        processed = published_race_ids(base.HERE.parent, base.TARGET_DATE8, flat, require_research=True)
        print("LIVE_RESUME_PUBLISHED", sorted(processed))
    missed: set[str] = set()
    attempts: dict[str, int] = {}
    last_error: dict[str, str] = {}

    while base.now_jst() < deadline:
        now = base.now_jst()
        for race in flat:
            rid = race["race_id"]
            if rid in processed or rid in missed:
                continue
            post = base.race_post_datetime(base.TARGET_DATE8, race["start_time"])
            remaining = (post - now).total_seconds() / 60.0
            if remaining < base.WINDOW_LATE_MINUTES:
                missed.add(rid)
                print("MISSED window:", rid, f"remaining={remaining:.2f}m", "attempts=", attempts.get(rid, 0), "last_error=", last_error.get(rid))
                continue
            if base.WINDOW_LATE_MINUTES <= remaining <= base.WINDOW_EARLY_MINUTES:
                attempts[rid] = attempts.get(rid, 0) + 1
                print("RESILIENT_TRIGGER", rid, f"remaining={remaining:.2f}m", "attempt", attempts[rid])
                try:
                    topk.predict_live_race_topk(race, hist, target_date)
                    processed.add(rid)
                    last_error.pop(rid, None)
                    print("RESILIENT_TRIGGER_SUCCESS", rid)
                except Exception as exc:
                    last_error[rid] = f"{type(exc).__name__}: {exc}"
                    print("RESILIENT_TRIGGER_RETRY", rid, last_error[rid])
                    if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
                        try:
                            from pace_recording_events import record_event
                            record_event(rid, base.TARGET_DATE8, "monitor_retry", exc)
                        except Exception:
                            pass
        if base.TARGET_RACE_ID and base.TARGET_RACE_ID in processed | missed:
            break
        if all(r["race_id"] in processed | missed or (r.get("surface") == "障" or "障害" in str(r.get("race_name", ""))) for r in races):
            break
        time.sleep(max(5, base.POLL_SECONDS))

    print("RESILIENT_LIVE_SUMMARY processed=", sorted(processed), "missed=", sorted(missed), "attempts=", attempts, "last_error=", last_error)


# publish.main() calls topk.main(); replace only that orchestration layer while
# preserving the existing prediction wrapper that publishes RaceData + signals.
topk.main = resilient_main
publish.main()
