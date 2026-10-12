from __future__ import annotations

import hashlib
import re
import unicodedata


JRA_VENUE_MAP = {
    "01": "札幌",
    "02": "函館",
    "03": "福島",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}


def normalize_horse_name(name: object) -> str:
    """Normalize width and whitespace only; never match a name by prefix."""
    if name is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(name)))


def horse_profile_id(horse_key: object) -> str:
    """Stable app-safe identifier derived from the existing historical horse key."""
    text = str(horse_key or "").strip()
    if not text:
        raise ValueError("horse_key is empty")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def venue_from_race_id(race_id: object) -> str | None:
    """Derive the JRA venue from the standard 12-digit race ID."""
    text = re.sub(r"\D", "", str(race_id or ""))
    if len(text) != 12:
        return None
    return JRA_VENUE_MAP.get(text[4:6])


def race_number_from_race_id(race_id: object) -> int | None:
    text = re.sub(r"\D", "", str(race_id or ""))
    if len(text) != 12:
        return None
    try:
        return int(text[-2:])
    except ValueError:
        return None


def distance_band(distance: object) -> str | None:
    try:
        d = int(float(distance))
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    lo = (d // 200) * 200
    hi = lo + 199
    return f"{lo}-{hi}m"
