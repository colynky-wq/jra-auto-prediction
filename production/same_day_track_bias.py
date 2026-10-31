"""Same-day venue/surface bias shadow feature builder.

This module is intentionally SHADOW-ONLY. It never mutates the fixed production
LightGBM inputs or the formal 10-minute prediction. It summarizes only races
that started before the target race and for which ResultData already contains
final results.

V1 uses data that is already persisted in RaceData.json + ResultData.json:
- same race day
- same venue
- same surface (芝 / ダ)
- horse number / field size (as a conservative proxy for inside-vs-outside draw)
- finish position
- final popularity

It does NOT claim to measure actual path bias (inside rail vs outside lane) or
closing/pace bias because those require passing positions / sectional times.
Those can be added later as V2 without contaminating the fixed production model.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import math
import re
from typing import Iterable


def _surface(course: str | None) -> str | None:
    text = str(course or "")
    if text.startswith("芝"):
        return "芝"
    if text.startswith("ダ"):
        return "ダート"
    return None


def _time_minutes(value: str | None) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


@dataclass(frozen=True)
class SameDayBiasSummary:
    version: str
    raceDate: str
    venue: str
    surface: str
    targetRaceNumber: int | None
    completedRaceCount: int
    completedHorseCount: int
    top3HorseCount: int
    innerTop3Rate: float | None
    outerTop3Rate: float | None
    drawBiasScore: float | None
    drawBiasLabel: str
    favoriteTop3Rate: float | None
    longshotTop3Rate: float | None
    upsetScore: float | None
    confidence: float
    sourceRaceNumbers: list[int]
    limitations: list[str]


def build_same_day_bias(
    races: Iterable[dict],
    results: Iterable[dict],
    *,
    race_date: str,
    venue: str,
    target_start_time: str,
    target_course: str,
    target_race_number: int | None = None,
) -> dict:
    """Summarize already-finished same-day races before the target race.

    Leakage guard: only races whose scheduled start time is strictly earlier than
    target_start_time and that have a complete ResultData row are eligible.
    """
    target_surface = _surface(target_course)
    target_minute = _time_minutes(target_start_time)
    if target_surface is None or target_minute is None:
        raise ValueError("target surface/start time unavailable")

    race_map = {}
    for race in races:
        if str(race.get("raceDate") or "") != race_date:
            continue
        if str(race.get("venue") or "") != venue:
            continue
        if _surface(race.get("course")) != target_surface:
            continue
        minute = _time_minutes(race.get("startTime"))
        rid = str(race.get("raceID") or "").zfill(12)
        if not rid.strip("0") or minute is None or minute >= target_minute:
            continue
        race_map[rid] = race

    result_map = {
        str(r.get("raceID") or "").zfill(12): r
        for r in results
        if str(r.get("raceDate") or "") == race_date
        and str(r.get("venue") or "") == venue
    }

    inner_starts = outer_starts = 0
    inner_top3 = outer_top3 = 0
    favorite_starts = longshot_starts = 0
    favorite_top3 = longshot_top3 = 0
    horse_count = top3_count = 0
    source_numbers: list[int] = []

    for rid, race in sorted(race_map.items(), key=lambda kv: _time_minutes(kv[1].get("startTime")) or 9999):
        result = result_map.get(rid)
        if not result:
            continue
        rows = result.get("horses") or []
        if not rows:
            continue
        # Require all runners to have a final placing/status before using a race.
        if any(
            row.get("finishPosition") is None
            and str(row.get("resultStatus") or "") in {"", "不明", "nan", "None"}
            for row in rows
        ):
            continue

        field_size = _safe_int(result.get("fieldSize")) or len(rows)
        if field_size <= 0:
            continue
        rn = _safe_int(race.get("raceNumber"))
        if rn is not None:
            source_numbers.append(rn)

        for row in rows:
            number = _safe_int(row.get("number"))
            finish = _safe_int(row.get("finishPosition"))
            popularity = _safe_int(row.get("popularity"))
            if number is None or finish is None:
                continue
            horse_count += 1
            is_top3 = finish <= 3
            if is_top3:
                top3_count += 1

            normalized_draw = (number - 0.5) / field_size
            if normalized_draw <= 0.5:
                inner_starts += 1
                inner_top3 += int(is_top3)
            else:
                outer_starts += 1
                outer_top3 += int(is_top3)

            if popularity is not None:
                if popularity <= 3:
                    favorite_starts += 1
                    favorite_top3 += int(is_top3)
                elif popularity >= max(6, math.ceil(field_size * 0.5)):
                    longshot_starts += 1
                    longshot_top3 += int(is_top3)

    completed_races = len(source_numbers)

    inner_rate = inner_top3 / inner_starts if inner_starts else None
    outer_rate = outer_top3 / outer_starts if outer_starts else None
    draw_score = None
    draw_label = "insufficient"
    if inner_rate is not None and outer_rate is not None:
        draw_score = round(outer_rate - inner_rate, 4)
        if draw_score >= 0.08:
            draw_label = "outer_draw_positive"
        elif draw_score <= -0.08:
            draw_label = "inner_draw_positive"
        else:
            draw_label = "neutral"

    favorite_rate = favorite_top3 / favorite_starts if favorite_starts else None
    longshot_rate = longshot_top3 / longshot_starts if longshot_starts else None
    upset_score = None
    if favorite_rate is not None and longshot_rate is not None:
        upset_score = round(longshot_rate - favorite_rate, 4)

    # Confidence is deliberately capped: V1 has draw/outcome data, not actual
    # racing path, pace or sectional evidence.
    confidence = min(0.65, completed_races / 6 * 0.65)

    summary = SameDayBiasSummary(
        version="same_day_track_bias_v1",
        raceDate=race_date,
        venue=venue,
        surface=target_surface,
        targetRaceNumber=target_race_number,
        completedRaceCount=completed_races,
        completedHorseCount=horse_count,
        top3HorseCount=top3_count,
        innerTop3Rate=round(inner_rate, 4) if inner_rate is not None else None,
        outerTop3Rate=round(outer_rate, 4) if outer_rate is not None else None,
        drawBiasScore=draw_score,
        drawBiasLabel=draw_label,
        favoriteTop3Rate=round(favorite_rate, 4) if favorite_rate is not None else None,
        longshotTop3Rate=round(longshot_rate, 4) if longshot_rate is not None else None,
        upsetScore=upset_score,
        confidence=round(confidence, 3),
        sourceRaceNumbers=source_numbers,
        limitations=[
            "V1 measures draw-number outcome tendency, not actual inside/outside racing path.",
            "Passing positions and sectional times are not present in ResultData.json V1.",
            "Do not feed this summary into the fixed production LightGBM until forward validation supports it.",
        ],
    )
    return asdict(summary)


def apply_draw_shadow_adjustment(horses: Iterable[dict], bias: dict, *, max_abs_pct: float = 0.06) -> list[dict]:
    """Return a shadow-only ranking adjustment, leaving original values intact.

    The adjustment is intentionally small and bounded. It is NOT a probability
    recalibration; it produces `sameDayShadowScore` for challenger comparison.
    """
    rows = [dict(h) for h in horses]
    if not rows:
        return rows
    score = _safe_float(bias.get("drawBiasScore")) or 0.0
    confidence = _safe_float(bias.get("confidence")) or 0.0
    numbers = [_safe_int(h.get("number")) for h in rows]
    valid = [n for n in numbers if n is not None]
    field_size = max(valid) if valid else len(rows)
    if field_size <= 0:
        field_size = len(rows)

    strength = max(-max_abs_pct, min(max_abs_pct, score * confidence))
    for h in rows:
        n = _safe_int(h.get("number"))
        base = _safe_float(h.get("aiProbability"))
        if n is None or base is None:
            h["sameDayShadowAdjustment"] = 0.0
            h["sameDayShadowScore"] = base
            continue
        draw_centered = ((n - 0.5) / field_size - 0.5) * 2.0  # -1 inner ... +1 outer
        adjustment = strength * draw_centered
        h["sameDayShadowAdjustment"] = round(adjustment, 6)
        h["sameDayShadowScore"] = round(base * (1.0 + adjustment), 6)

    rows.sort(
        key=lambda h: (
            -(_safe_float(h.get("sameDayShadowScore")) or -1e9),
            _safe_int(h.get("number")) or 999,
        )
    )
    for i, h in enumerate(rows, 1):
        h["sameDayShadowRank"] = i
    return rows
