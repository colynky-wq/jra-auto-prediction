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
    raw = re.sub(r"\s+", " ", raw).strip()
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
    provider = payload.get("sourceProvider") if isinstance(payload, dict) else None
    if provider not in (None, "netkeiba_api", "jra_official_web"):
        raise WinOddsFreshnessError("unrecognized_win_odds_provider")
    source_field = ("jra.html.interim_label" if provider == "jra_official_web"
                    else "data.official_datetime")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise WinOddsFreshnessError("win_odds_data_missing")
    origin_time = parse_official_datetime(data.get("official_datetime"))
    source_metadata = {}
    if provider == "jra_official_web":
        # The synthetic data.official_datetime alone is NOT evidence that
        # JRA printed an origin time. Require its raw display label, page
        # digest and independently matched race identity before adoption.
        label = payload.get("sourceDisplayLabel")
        match = (re.fullmatch(r"(\\d{1,2})時(\\d{2})分現在オッズ", label)
                 if isinstance(label, str) else None)
        date8 = payload.get("sourceRaceDateJST")
        race_id = payload.get("sourceRaceID")
        digest = payload.get("sourcePageSha256")
        if (match is None or not isinstance(date8, str)
                or not re.fullmatch(r"\\d{8}", date8)
                or not isinstance(race_id, str)
                or not re.fullmatch(r"\\d{12}", race_id)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or payload.get("sourceURL") != "https://www.jra.go.jp/JRADB/accessO.html"
                or payload.get("sourceTimePrecision") != "minute"):
            raise WinOddsFreshnessError("jra_displayed_source_provenance_missing")
        if (int(match[1]) != origin_time.hour
                or int(match[2]) != origin_time.minute
                or origin_time.second != 0 or origin_time.microsecond != 0
                or date8 != origin_time.strftime("%Y%m%d")):
            raise WinOddsFreshnessError("jra_source_display_timestamp_mismatch")
        source_metadata = {
            "sourceDisplayLabel": label,
            "sourceRaceDateJST": date8,
            "sourceRaceID": race_id,
            "sourcePageSha256": digest,
            "sourceURL": payload["sourceURL"],
            "sourceTimePrecision": "minute",
        }
    received = received_at.astimezone(JST)
    age = received - origin_time
    if age < -MAX_CLOCK_SKEW:
        raise WinOddsFreshnessError("win_odds_source_timestamp_in_future")
    if age > MAX_AGE:
        raise WinOddsFreshnessError("win_odds_source_stale_over_30m")
    return {
        "sourceField": source_field,
        "sourceProvider": provider or "netkeiba_api",
        **source_metadata,
        "sourceUpdatedAtJST": origin_time.isoformat(),
        "receivedAtJST": received.isoformat(),
        "sourceAgeSecondsAtReceipt": round(age.total_seconds(), 3),
        "maxSourceAgeSeconds": int(MAX_AGE.total_seconds()),
        "freshnessStatus": "verified_within_30m",
    }
