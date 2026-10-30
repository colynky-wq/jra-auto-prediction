"""Validate immutable formal T-10 evidence for prospective race reviews.

The formal review must never infer a T-10 capture from a morning/app snapshot.
It accepts only the frozen per-race win sidecar written by the live runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
FORMAL_TIMING = "live_10min"
MIN_MINUTES_TO_POST = 8.0
MAX_MINUTES_TO_POST = 12.0


def _parse_jst(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(JST)


def _scheduled_post(day: str, start_time: str) -> datetime | None:
    if not isinstance(day, str) or not isinstance(start_time, str):
        return None
    try:
        return datetime.fromisoformat(f"{day}T{start_time}:00+09:00").astimezone(JST)
    except ValueError:
        return None


def _result(eligible: bool, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "reason": reason,
        "requiredTiming": FORMAL_TIMING,
        "formalWindowMinutesToPost": [MIN_MINUTES_TO_POST, MAX_MINUTES_TO_POST],
        **extra,
    }


def inspect_formal_10min_evidence(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Return fail-closed evidence for one portfolio race."""
    day = row.get("raceDate")
    rid = row.get("raceID")
    start = row.get("startTime")
    if not isinstance(day, str) or not isinstance(rid, str) or not rid or not isinstance(start, str):
        return _result(False, "invalid_portfolio_identity")

    day8 = day.replace("-", "")
    sidecar = root / "reports" / day8 / "all_bet_v1" / "win" / f"{rid}.json"
    rel = str(sidecar.relative_to(root)) if sidecar.is_relative_to(root) else str(sidecar)
    if not sidecar.exists():
        return _result(False, "formal_win_sidecar_missing", source=rel)

    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        return _result(False, f"formal_win_sidecar_unreadable:{type(exc).__name__}", source=rel)

    if payload.get("schemaVersion") != 1 or payload.get("betType") != "win":
        return _result(False, "unexpected_formal_win_sidecar_schema", source=rel)

    prediction = payload.get("prediction")
    if not isinstance(prediction, dict):
        return _result(False, "prediction_missing", source=rel)

    if str(prediction.get("raceID") or "") != rid:
        return _result(False, "prediction_race_id_mismatch", source=rel)
    if prediction.get("raceDate") != day:
        return _result(False, "prediction_race_date_mismatch", source=rel)
    if str(prediction.get("startTime") or "") != start:
        return _result(False, "prediction_start_time_mismatch", source=rel)
    if prediction.get("predictionTiming") != FORMAL_TIMING:
        return _result(False, "prediction_not_formal_10min", source=rel)

    captured = _parse_jst(prediction.get("capturedAtJST"))
    scheduled = _scheduled_post(day, start)
    if captured is None or scheduled is None:
        return _result(False, "formal_timestamp_unusable", source=rel)

    minutes_to_post = (scheduled - captured).total_seconds() / 60.0
    if not (MIN_MINUTES_TO_POST <= minutes_to_post <= MAX_MINUTES_TO_POST):
        return _result(
            False,
            "captured_outside_formal_window",
            source=rel,
            capturedAtJST=captured.isoformat(),
            scheduledPostJST=scheduled.isoformat(),
            minutesToPost=round(minutes_to_post, 3),
        )

    trigger = prediction.get("triggerMinutesToPost")
    if trigger is None:
        trigger = payload.get("triggerMinutesToPost")
    if trigger is not None:
        try:
            trigger_value = float(trigger)
        except (TypeError, ValueError):
            return _result(False, "trigger_minutes_invalid", source=rel)
        if not (MIN_MINUTES_TO_POST <= trigger_value <= MAX_MINUTES_TO_POST):
            return _result(False, "trigger_outside_formal_window", source=rel, triggerMinutesToPost=trigger_value)

    entry = payload.get("entryEvidence")
    if isinstance(entry, dict):
        entry_rid = entry.get("raceID")
        entry_day = entry.get("raceDate")
        if entry_rid is not None and str(entry_rid) != rid:
            return _result(False, "entry_evidence_race_id_mismatch", source=rel)
        if entry_day is not None and entry_day != day:
            return _result(False, "entry_evidence_race_date_mismatch", source=rel)
        entry_scheduled = _parse_jst(entry.get("scheduledPostJST"))
        entry_captured = _parse_jst(entry.get("capturedAtJST"))
        if entry_scheduled is not None and abs((entry_scheduled - scheduled).total_seconds()) > 1:
            return _result(False, "entry_evidence_post_time_mismatch", source=rel)
        if entry_captured is not None:
            entry_minutes = (scheduled - entry_captured).total_seconds() / 60.0
            if not (MIN_MINUTES_TO_POST <= entry_minutes <= MAX_MINUTES_TO_POST):
                return _result(
                    False,
                    "entry_evidence_outside_formal_window",
                    source=rel,
                    entryMinutesToPost=round(entry_minutes, 3),
                )

    return _result(
        True,
        "formal_10min_verified",
        source=rel,
        capturedAtJST=captured.isoformat(),
        scheduledPostJST=scheduled.isoformat(),
        minutesToPost=round(minutes_to_post, 3),
    )
