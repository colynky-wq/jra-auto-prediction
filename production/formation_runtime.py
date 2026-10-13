"""History-based initial-position references; never a pace or win model."""
from __future__ import annotations

import re
from statistics import mean

from app_research_runtime import finite

FORMATION_METHOD_VERSION = "initial-corner-six-races-v1"
PACE_NOTE_VERSION = "lap-collection-ready-20261013-v1"
MIN_PAST_RACES = 3
MIN_COVERAGE = 0.80


def is_straight_race(race_id: str, surface: str, distance) -> bool:
    # JRA's Niigata turf 1000m has no corner. Stored passage positions there
    # must not be interpreted as first-corner observations.
    return str(race_id)[4:6] == "04" and surface == "芝" and finite(distance) == 1000


def current_race_has_corner(race: dict) -> bool:
    course = str(race.get("course", ""))
    if "直線" in course or "障" in course:
        return False
    niigata = race.get("venue") == "新潟" or str(race.get("raceID", ""))[4:6] == "04"
    return not (niigata and "芝" in course and re.search(r"(?<!\d)1,?000(?!\d)", course))


def relative_position(first_corner, field_size) -> float | None:
    pos, count = finite(first_corner), finite(field_size)
    if pos is None or count is None or not count.is_integer() or count <= 1:
        return None
    if not pos.is_integer() or not 1 <= pos <= count:
        return None
    return (pos - 1) / (count - 1)


def position_history(observations: list[tuple[float, str]]) -> dict | None:
    valid = [(value, date) for raw, date in observations
             if (value := finite(raw)) is not None and 0 <= value <= 1][-6:]
    if not valid:
        return None
    values = [value for value, _ in valid]
    return {
        "sampleRaces": len(valid),
        "meanRelativePosition": round(mean(values), 6),
        "minRelativePosition": round(min(values), 6),
        "maxRelativePosition": round(max(values), 6),
        "latestHistoryDate": max(date for _, date in valid),
    }


def formation_reference(horses: list[dict], *, has_corner: bool = True) -> dict:
    known = [horse for horse in horses
             if horse.get("initialPositionHistory")
             and horse["initialPositionHistory"]["sampleRaces"] >= MIN_PAST_RACES]
    coverage = len(known) / len(horses) if horses else 0
    available = has_corner and len(known) >= 3 and coverage >= MIN_COVERAGE
    ordered = sorted(known, key=lambda horse: (
        horse["initialPositionHistory"]["meanRelativePosition"], horse["number"]))
    escape, front = [], []
    for horse in known:
        stat = horse.get("styleTendency")
        if not stat:
            continue
        frequencies = stat["distributionPct"]
        if frequencies.get("逃げ", 0) >= 50:
            escape.append(horse["number"])
        if frequencies.get("逃げ", 0) + frequencies.get("先行", 0) >= 50:
            front.append(horse["number"])
    return {
        "methodVersion": FORMATION_METHOD_VERSION,
        "status": "no_corner" if not has_corner else "reference_order" if available else "insufficient_history",
        "knownHorses": len(known), "unknownHorses": len(horses) - len(known),
        "totalHorses": len(horses), "coveragePct": round(coverage * 100, 1),
        "minimumPastRaces": MIN_PAST_RACES, "minimumCoveragePct": MIN_COVERAGE * 100,
        "orderedHorseNumbers": [horse["number"] for horse in ordered] if available else [],
        "escapeTrendHorseNumbers": sorted(escape), "frontTrendHorseNumbers": sorted(front),
        "paceForecast": {
            "status": "unavailable",
            "reason": "ペース予想は、脚質と出走メンバーを使った検証を終えてから表示します。",
        },
        "meaning": "初角の相対位置を過去最大6走で平均した参考順。同値は馬番順で、厳密な順位差を意味しません。逃げ・先行の頭数は過去頻度が50%以上の収録済み馬だけを数えます。",
    }
