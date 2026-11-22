"""Defer expensive derived research publication while live T-10 bundles are changing main.

A research build may still run for CI/artifact generation, but it must not race
the high-frequency real-time writer during today's JRA session. No results,
predictions, stakes or saved race evidence are modified.
"""
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys

JST = timezone(timedelta(hours=9))
LIVE_START = time(8, 30)
LIVE_END = time(17, 35)


def defer(today: datetime, races) -> bool:
    if today.tzinfo is None:
        raise ValueError("JST-aware clock required")
    local = today.astimezone(JST)
    if not LIVE_START <= local.time() < LIVE_END:
        return False
    if not isinstance(races, list):
        raise ValueError("RaceData must be a list")
    day = local.date().isoformat()
    # Today's published live schedule is direct evidence of racing. Older
    # snapshots alone cannot trigger a defer on an ordinary non-racing day.
    return any(isinstance(r, dict) and r.get("raceDate") == day for r in races)


def main() -> int:
    races = json.loads(Path("RaceData.json").read_text(encoding="utf-8"))
    if defer(datetime.now(JST), races):
        print("APP_RESEARCH_DEFERRED_TO_EVENING: ongoing live race-day main writes")
        return 0
    print("APP_RESEARCH_PUBLICATION_WINDOW_OK")
    return 1


if __name__ == "__main__":
    sys.exit(main())
