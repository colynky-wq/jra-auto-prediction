"""Fail-closed freshness check for the upstream win-odds publication time.

A T-10 HTTP fetch is NOT proof that the prices were refreshed at T-10.
Only the origin's official_datetime is treated as the odds timestamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
MAX_AGE = timedelta(minutes=30)
MAX_CLOCK_SKEW = timedelta(seconds=0)


class WinOddsFreshnessError(ValueError):
    """Origin timestamp is absent, malformed, stale, or in the future."""


def parse_official_datetime(value: object) -> datetime:
    """Convert provider official_datetime to an aware JST datetime.

    Numeric/unlabelled epoch-like values and time-of-day-only values are
    deliberately rejected: a race date or receipt time must never be guessed.
    """
    if not isinstance(value, str) or not value.strip():
        raise WinOddsFreshnessError("win_odds_source_timestamp_missing")
    raw = value.strip()
    # Provider timestamps can use Japanese date separators and space/T.
    raw = raw.replace("年", "-").replace("月", "-").replace("日", " ")
    raw = raw.replace("/", "-")
    raw = re.sub(r"\\s+", " ", raw).strip()
    if not re.match(r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$", raw):
        raise WinOddsFreshnessError("win_odds_source_timestamp_unparseable")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WinOddsFreshnessError("win_odds_source_timestamp_unparseable") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=JST)
    return stamp.astimezone(JST)


def validate_win_odds_freshness(payload: dict, received_at: datetime) -> dict:
    if received_at.tzinfo is None:
        raise WinOddsFreshnessError("win_odds_receipt_timestamp_naive")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise WinOddsFreshnessError("win_odds_data_missing")
    origin_time = parse_official_datetime(data.get("official_datetime"))
    received = received_at.astimezone(JST)
    age = received - origin_time
    if age < -MAX_CLOCK_SKEW:
        raise WinOddsFreshnessError("win_odds_source_timestamp_in_future")
    if age > MAX_AGE:
        raise WinOddsFreshnessError("win_odds_source_stale_over_30m")
    return {
        "sourceField": "data.official_datetime",
        "sourceUpdatedAtJST": origin_time.isoformat(),
        "receivedAtJST": received.isoformat(),
        "sourceAgeSecondsAtReceipt": round(age.total_seconds(), 3),
        "maxSourceAgeSeconds": int(MAX_AGE.total_seconds()),
        "freshnessStatus": "verified_within_30m",
    }
