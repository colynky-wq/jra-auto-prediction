"""Protect the canonical app feed from delayed early publications."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

JST = timezone(timedelta(hours=9))


def decision(candidate, current, payload, stage, now):
    if stage not in {"morning", "previous_evening"} or not candidate:
        raise ValueError("invalid early publication")
    day = payload["raceDate"]
    date = datetime.strptime(day, "%Y-%m-%d").date()
    if any(r.get("raceDate") != day for r in candidate):
        raise ValueError("candidate race date mismatch")
    snapshot = payload["snapshots"][stage]
    capture = datetime.fromisoformat(snapshot["capturedAtJST"])
    if capture.tzinfo is None or now.tzinfo is None:
        raise ValueError("timezone evidence required")
    capture = capture.astimezone(JST)
    now = now.astimezone(JST)
    expected_day = date if stage == "morning" else date - timedelta(days=1)
    if capture.date() != expected_day or capture > now:
        return "stage_clock_mismatch"
    first_post = min(datetime.fromisoformat(day + "T" + r["startTime"]).replace(tzinfo=JST)
                     for r in candidate)
    if now >= first_post:
        return "first_post_deadline"
    if not isinstance(current, list):
        raise ValueError("invalid current race feed")
    for race in current:
        existing_day = race.get("raceDate")
        if existing_day and existing_day > day:
            return "newer_meeting"
        if existing_day == day and race.get("predictionTiming") in {"live_10min", "live_late_recovery"}:
            return "existing_live_capture"
    return "publish"


def main():
    candidate, current, payload, stage = sys.argv[1:]
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
    outcome = decision(read(candidate), read(current) if Path(current).exists() else [],
                       read(payload), stage, datetime.now(JST))
    print("EARLY_PUBLISH_GUARD", outcome)
    return 0 if outcome == "publish" else 3


if __name__ == "__main__":
    sys.exit(main())
