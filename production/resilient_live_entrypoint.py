from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import live_monitor_dry_run as base
import live_monitor_topk as topk
import live_monitor_topk_publish as publish
from live_capture_policy import (
    FORMAL_TIMING_MARKER,
    RECOVERY_TIMING_MARKER,
    classify_capture_timing,
    validate_capture_windows,
)


RECOVERY_LATE_MINUTES = float(os.environ.get("KEIBAAI_LIVE_GRACE_MINUTES", "5").strip() or "5")
validate_capture_windows(base.WINDOW_EARLY_MINUTES, base.WINDOW_LATE_MINUTES, RECOVERY_LATE_MINUTES)


def configure_http_retries() -> None:
    """Harden all netkeiba/JRA GETs made through the shared production session."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    base.SESSION.mount("https://", adapter)
    base.SESSION.mount("http://", adapter)


configure_http_retries()


def resilient_main() -> None:
    """Live monitor with retries plus a separately labelled late-recovery window.

    Formal captures remain strictly inside the existing 8-12 minute T-10 window.
    Transient failures are retried throughout that window.  If the formal window
    is lost, a bounded recovery capture may still be saved down to the configured
    grace edge (5 minutes by default), but it is labelled live_late_recovery so it
    can never be mistaken for formal T-10 research evidence.
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
    print(
        "LIVE_CAPTURE_WINDOWS",
        f"formal={base.WINDOW_EARLY_MINUTES:.1f}-{base.WINDOW_LATE_MINUTES:.1f}m",
        f"recovery={base.WINDOW_LATE_MINUTES:.1f}-{RECOVERY_LATE_MINUTES:.1f}m",
    )

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
            timing = classify_capture_timing(
                remaining,
                formal_early=base.WINDOW_EARLY_MINUTES,
                formal_late=base.WINDOW_LATE_MINUTES,
                recovery_late=RECOVERY_LATE_MINUTES,
            )
            if timing is None:
                if remaining < RECOVERY_LATE_MINUTES:
                    missed.add(rid)
                    print(
                        "MISSED recovery window:", rid, f"remaining={remaining:.2f}m",
                        "attempts=", attempts.get(rid, 0), "last_error=", last_error.get(rid),
                    )
                    if os.environ.get("KEIBAAI_CAPTURE_PRERACE", "").lower() in {"true", "1"}:
                        try:
                            from pace_recording_events import record_event
                            record_event(
                                rid,
                                base.TARGET_DATE8,
                                "monitor_missed",
                                RuntimeError(last_error.get(rid) or "live capture window expired"),
                            )
                        except Exception:
                            pass
                continue

            attempts[rid] = attempts.get(rid, 0) + 1
            label = "FORMAL" if timing == FORMAL_TIMING_MARKER else "LATE_RECOVERY"
            print("RESILIENT_TRIGGER", rid, label, f"remaining={remaining:.2f}m", "attempt", attempts[rid])
            capture_race = dict(race)
            capture_race["_prediction_timing"] = timing
            capture_race["_trigger_minutes_to_post"] = round(remaining, 3)
            try:
                topk.predict_live_race_topk(capture_race, hist, target_date)
                processed.add(rid)
                last_error.pop(rid, None)
                print("RESILIENT_TRIGGER_SUCCESS", rid, label)
            except Exception as exc:
                last_error[rid] = f"{type(exc).__name__}: {exc}"
                print("RESILIENT_TRIGGER_RETRY", rid, label, last_error[rid])
                if timing == RECOVERY_TIMING_MARKER:
                    print("RECOVERY_RETRY_REMAINING", rid, f"remaining={remaining:.2f}m")
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

    pending = sorted(
        r["race_id"] for r in flat if r["race_id"] not in processed and r["race_id"] not in missed
    )
    print(
        "RESILIENT_LIVE_SUMMARY processed=", sorted(processed),
        "missed=", sorted(missed), "pending=", pending,
        "attempts=", attempts, "last_error=", last_error,
    )


# publish.main() calls topk.main(); replace only that orchestration layer while
# preserving the existing prediction wrapper that publishes RaceData + signals.
topk.main = resilient_main
publish.main()
