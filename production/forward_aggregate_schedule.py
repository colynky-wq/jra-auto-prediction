"""Choose a completed forward-aggregation day despite delayed GitHub cron.

The recurring job is intended for 20:00 JST, after the final JRA race.
If GitHub actually launches it after midnight but before the next day's
20:00 boundary, process the last completed settlement day instead of
requesting incomplete results for the new race day.
"""
from datetime import datetime, timedelta, time, timezone
import re

JST = timezone(timedelta(hours=9))
SETTLEMENT_TIME = time(20, 0)


def target_date(explicit: str, event_name: str, now: datetime) -> str:
    if explicit:
        if not re.fullmatch(r"\d{8}", explicit):
            raise ValueError("aggregate target must be YYYYMMDD")
        day = datetime.strptime(explicit, "%Y%m%d").date()
        if day.strftime("%Y%m%d") != explicit:
            raise ValueError("aggregate target date is invalid")
        return explicit

    if now.tzinfo is None:
        raise ValueError("aggregation clock must include timezone")

    local = now.astimezone(JST)
    if event_name == "schedule" and local.time() < SETTLEMENT_TIME:
        local -= timedelta(days=1)
    return local.strftime("%Y%m%d")
