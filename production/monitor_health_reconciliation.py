"""Verify and reconcile same-day GitHub live-monitor sessions, without race data writes."""
from datetime import datetime
from monitor_health_runtime import (JST, REPOSITORY, WORKFLOW, aware,
                                    health_from_event, validate_event)


def recent_race_day_health(runs, get_jobs, now, limit=25):
    if now.tzinfo is None or not isinstance(runs, list):
        raise ValueError("race-day reconciliation requires an aware clock and workflow runs")
    day = now.astimezone(JST).date()
    selected = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        event = {"repository": {"full_name": REPOSITORY}, "workflow_run": run}
        try:
            validate_event(event)
            created = aware(run.get("run_started_at") or run["created_at"])
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
        if created.date() == day and run["status"] in {
                "completed", "in_progress", "pending", "queued", "waiting", "requested"}:
            selected.append((created, event))
    selected.sort(key=lambda item: item[0], reverse=True)
    if len(selected) > limit:
        raise ValueError("more monitor runs than bounded same-day verification limit")
    verified = []
    for _, event in selected:
        try:
            jobs = get_jobs(event["workflow_run"]["id"])
        except (OSError, ValueError, KeyError):
            # Unknown is not a verified empty queue and cannot be
            # upgraded to superseded. Keep the interruption warning.
            jobs = None
        health = health_from_event(event, jobs, now)
        if health["raceDate"] == day.isoformat():
            verified.append(health)
    return verified
