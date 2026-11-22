#!/usr/bin/env python3
"""Fail-closed JST session windows for serialized live GitHub Actions runners.

This policy controls *when a monitor may start*, not what makes a formal
capture eligible. All T-10 window/source checks remain in the existing live
capture code. A queued/delayed runner must never occupy a later race phase.
No network calls, prediction replay, model tuning, or race-data writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path

JST = timezone(timedelta(hours=9))
# 2026-11-22: an actual gap from 10:35 to 10:59 JST caused two missed
# early race captures. GitHub cron runs queued for hours cannot be relied upon
# to start on a strict handoff minute. Allow any *already running* live session
# to continue until the day's final 17:30 cutoff, subject to the 335-minute
# per-worker cap and the one-writer concurrency lock.
#
# Expanding a worker's runtime does not authorize retroactive T-10 evidence:
# the existing live capture timestamp validation remains unchanged.
CUTOFFS = {
    "15 20 * * 5,6,0": "17:30",  # 05:15 JST early primary
    "15 21 * * 5,6,0": "17:30",  # 06:15 JST redundant start
    "45 22 * * *": "17:30",      # 07:45 JST primary
    "15 23 * * 5,6,0": "17:30",  # 08:15 JST redundant start
    "0 3 * * *": "17:30",        # 12:00 JST afternoon primary
    "7,22,37,52 22-23 * * 5,6,0": "17:30",  # 07:07-08:52 JST
    "7,22,37,52 0-7 * * 0,1,6": "17:30",    # 09:07-16:52 JST
}
MAX_RUNTIME_MINUTES = 335
MIN_USEFUL_REMAINING_SECONDS = 120


def choose(event_name, scheduled_cron, date8, now=None, manual_minutes=None):
    """Return {active, minutes, reason}. No side effects."""
    if event_name == "push":
        return {"active": False, "minutes": 0, "reason": "push_ci_only"}
    if len(date8) != 8 or not date8.isdigit():
        raise ValueError("invalid target JST date")
    try:
        day = datetime.strptime(date8, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("invalid target JST date") from exc
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(JST)
    if day != now.date():
        return {"active": False, "minutes": 0, "reason": "different_jst_date"}
    if event_name == "workflow_dispatch":
        try:
            minutes = int(str(manual_minutes if manual_minutes is not None else "120"))
        except ValueError as exc:
            raise ValueError("invalid manual live runtime") from exc
        if not 1 <= minutes <= MAX_RUNTIME_MINUTES:
            raise ValueError("manual live runtime outside safety bounds")
        return {"active": True, "minutes": minutes, "reason": "manual"}
    if event_name != "schedule" or scheduled_cron not in CUTOFFS:
        raise ValueError("unregistered live event/cron; fail closed")
    hour, minute = map(int, CUTOFFS[scheduled_cron].split(":"))
    cutoff = datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)
    remaining = (cutoff - now).total_seconds()
    if remaining < MIN_USEFUL_REMAINING_SECONDS:
        return {"active": False, "minutes": 0,
                "reason": "phase_cutoff_passed_or_imminent"}
    minutes = min(MAX_RUNTIME_MINUTES, math.ceil(remaining / 60.0))
    return {"active": True, "minutes": minutes, "reason": "within_phase_window"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    state = choose(
        os.environ.get("KEIBAAI_EVENT_NAME", ""),
        os.environ.get("KEIBAAI_EVENT_SCHEDULE", ""),
        os.environ.get("KEIBAAI_DATE8", ""),
        manual_minutes=os.environ.get("KEIBAAI_MAX_MINUTES_INPUT") or None,
    )
    print("LIVE_SESSION_SLOT", state)
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as stream:
            stream.write("active=" + str(state["active"]).lower() + "\n")
            stream.write("minutes=" + str(state["minutes"]) + "\n")


if __name__ == "__main__":
    main()
