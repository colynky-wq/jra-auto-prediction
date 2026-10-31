"""Same-Day Track Bias Shadow V2.

Adds research-only running-style and sectional signals using completed races from
the same day/venue/surface. Formal A probabilities are never overwritten.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

from same_day_track_bias import build_same_day_bias, apply_draw_shadow_adjustment


def _safe_int(value):
    try:
        return int(float(value))
    except Exception:
        return None


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


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


def _last_corner(value) -> int | None:
    nums = [int(x) for x in re.findall(r"\d+", str(value or ""))]
    return nums[-1] if nums else None


def _complete(rows: list[dict]) -> bool:
    if not rows:
        return False
    for row in rows:
        if row.get("finishPosition") is not None:
            continue
        status = str(row.get("resultStatus") or "").strip()
        if status and status not in {"不明", "nan", "None"}:
            continue
        return False
    return True


def _rate(n, d):
    return n / d if d else None


def _round(value, n=4):
    return round(value, n) if value is not None else None


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def build_same_day_bias_v2(
    races: Iterable[dict],
    results: Iterable[dict],
    *,
    race_date: str,
    venue: str,
    target_start_time: str,
    target_course: str,
    target_race_number: int | None = None,
) -> dict:
    """Build V1 draw signal plus V2 running-style/sectional context.

    Leakage guard is identical to V1: only strictly earlier, completed races from
    the same date, venue and surface may contribute.
    """
    races = list(races)
    results = list(results)
    base = build_same_day_bias(
        races,
        results,
        race_date=race_date,
        venue=venue,
        target_start_time=target_start_time,
        target_course=target_course,
        target_race_number=target_race_number,
    )

    target_surface = _surface(target_course)
    target_minute = _time_minutes(target_start_time)
    if target_surface is None or target_minute is None:
        raise ValueError("target surface/start time unavailable")

    eligible = {}
    for race in races:
        if str(race.get("raceDate") or "") != race_date:
            continue
        if str(race.get("venue") or "") != venue:
            continue
        if _surface(race.get("course")) != target_surface:
            continue
        minute = _time_minutes(race.get("startTime"))
        rid = str(race.get("raceID") or "").zfill(12)
        if rid.strip("0") and minute is not None and minute < target_minute:
            eligible[rid] = race

    result_map = {
        str(r.get("raceID") or "").zfill(12): r
        for r in results
        if str(r.get("raceDate") or "") == race_date
        and str(r.get("venue") or "") == venue
    }

    front_starts = front_top3 = back_starts = back_top3 = 0
    fast_starts = fast_top3 = slow_starts = slow_top3 = 0
    top3_corner_pcts: list[float] = []
    top3_gains: list[float] = []
    detail_races = 0
    sectional_races = 0
    lap_races = 0
    early_minus_late: list[float] = []
    detail_source_numbers: list[int] = []

    for rid, race in sorted(eligible.items(), key=lambda kv: _time_minutes(kv[1].get("startTime")) or 9999):
        result = result_map.get(rid)
        if not result:
            continue
        rows = result.get("horses") or []
        if not _complete(rows):
            continue
        field_size = _safe_int(result.get("fieldSize")) or len(rows)
        if field_size < 2:
            continue

        valid = []
        for row in rows:
            finish = _safe_int(row.get("finishPosition"))
            corner = _last_corner(row.get("passingOrder"))
            if finish is None or corner is None or corner < 1:
                continue
            corner = min(corner, field_size)
            valid.append((row, finish, corner))

        if len(valid) >= max(3, math.ceil(field_size * 0.5)):
            detail_races += 1
            rn = _safe_int(race.get("raceNumber"))
            if rn is not None:
                detail_source_numbers.append(rn)
            for row, finish, corner in valid:
                pct = (corner - 0.5) / field_size
                is_top3 = finish <= 3
                if pct <= 1 / 3:
                    front_starts += 1
                    front_top3 += int(is_top3)
                elif pct >= 2 / 3:
                    back_starts += 1
                    back_top3 += int(is_top3)
                if is_top3:
                    top3_corner_pcts.append(pct)
                    top3_gains.append((corner - finish) / max(field_size - 1, 1))

        # Last-3F signal is calculated race-by-race so distances/fields do not mix.
        sec = []
        for row in rows:
            finish = _safe_int(row.get("finishPosition"))
            l3 = _safe_float(row.get("last3F"))
            if finish is not None and l3 is not None:
                sec.append((l3, finish))
        if len(sec) >= max(4, math.ceil(field_size * 0.5)):
            sectional_races += 1
            sec.sort(key=lambda x: x[0])
            k = max(1, math.ceil(len(sec) / 3))
            for _, finish in sec[:k]:
                fast_starts += 1
                fast_top3 += int(finish <= 3)
            for _, finish in sec[-k:]:
                slow_starts += 1
                slow_top3 += int(finish <= 3)

        lap = result.get("raceLap") or {}
        first600 = _safe_float(lap.get("first600mSeconds"))
        last600 = _safe_float(lap.get("raceLast600mSeconds"))
        if first600 is not None and last600 is not None:
            lap_races += 1
            early_minus_late.append(first600 - last600)

    front_rate = _rate(front_top3, front_starts)
    back_rate = _rate(back_top3, back_starts)
    group_score = None
    if front_rate is not None and back_rate is not None:
        group_score = back_rate - front_rate

    corner_avg = sum(top3_corner_pcts) / len(top3_corner_pcts) if top3_corner_pcts else None
    corner_score = _clamp((corner_avg - 0.5) * 2.0) if corner_avg is not None else None
    gain_avg = sum(top3_gains) / len(top3_gains) if top3_gains else None
    gain_score = _clamp(gain_avg * 2.0) if gain_avg is not None else None

    pieces = []
    if group_score is not None:
        pieces.append((0.50, _clamp(group_score)))
    if corner_score is not None:
        pieces.append((0.30, corner_score))
    if gain_score is not None:
        pieces.append((0.20, gain_score))
    if pieces:
        denom = sum(w for w, _ in pieces)
        style_score = _clamp(sum(w * x for w, x in pieces) / denom)
    else:
        style_score = None

    if style_score is None:
        style_label = "insufficient"
    elif style_score >= 0.12:
        style_label = "closer_positive"
    elif style_score <= -0.12:
        style_label = "front_positive"
    else:
        style_label = "neutral"

    fast_rate = _rate(fast_top3, fast_starts)
    slow_rate = _rate(slow_top3, slow_starts)
    sectional_score = None
    if fast_rate is not None and slow_rate is not None:
        sectional_score = _clamp(fast_rate - slow_rate)
    if sectional_score is None:
        sectional_label = "insufficient"
    elif sectional_score >= 0.15:
        sectional_label = "fast_last3_rewarded"
    elif sectional_score <= -0.15:
        sectional_label = "last3_speed_not_rewarded"
    else:
        sectional_label = "neutral"

    completed = int(base.get("completedRaceCount") or 0)
    coverage = detail_races / completed if completed else 0.0
    style_conf = min(0.85, detail_races / 5 * 0.85) * min(1.0, coverage + 0.25)
    sectional_conf = min(0.75, sectional_races / 5 * 0.75)

    pace_delta = sum(early_minus_late) / len(early_minus_late) if early_minus_late else None
    if pace_delta is None:
        pace_label = "insufficient"
    elif pace_delta <= -0.8:
        pace_label = "early_faster_than_late"
    elif pace_delta >= 0.8:
        pace_label = "late_faster_than_early"
    else:
        pace_label = "balanced_600m"

    base.update(
        {
            "version": "same_day_track_bias_v2",
            "detailRaceCount": detail_races,
            "detailSourceRaceNumbers": detail_source_numbers,
            "frontGroupTop3Rate": _round(front_rate),
            "backGroupTop3Rate": _round(back_rate),
            "top3AverageFinalCornerPct": _round(corner_avg),
            "top3AverageLatePositionGain": _round(gain_avg),
            "runningStyleBiasScore": _round(style_score),
            "runningStyleBiasLabel": style_label,
            "runningStyleConfidence": round(style_conf, 3),
            "sectionalRaceCount": sectional_races,
            "fastLast3Top3Rate": _round(fast_rate),
            "slowLast3Top3Rate": _round(slow_rate),
            "closingSpeedBiasScore": _round(sectional_score),
            "closingSpeedBiasLabel": sectional_label,
            "closingSpeedConfidence": round(sectional_conf, 3),
            "lapRaceCount": lap_races,
            "first600MinusLast600AvgSeconds": _round(pace_delta, 3),
            "paceShapeLabel": pace_label,
            "limitations": [
                "V2 uses official result-table passing order and last-3F when available.",
                "Draw number remains a proxy for gate position, not the actual rail/path taken in the stretch.",
                "Pace shape compares first and last 600m within completed races; it is not yet course-normalized.",
                "Shadow-only: do not replace the fixed production A model without prospective validation.",
            ],
        }
    )
    return base


def build_style_map_from_history(history_path: str | Path, race_date: str) -> dict[str, float]:
    """Estimate each horse's historical frontness (1=front, 0=back), pre-race only."""
    try:
        import pandas as pd
        path = Path(history_path)
        if not path.exists():
            return {}
        d = pd.read_parquet(path, columns=["馬キー", "日付", "初角", "頭数"])
        d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
        cutoff = pd.Timestamp(race_date)
        d = d.loc[d["日付"].lt(cutoff)].copy()
        d["初角"] = pd.to_numeric(d["初角"], errors="coerce")
        d["頭数"] = pd.to_numeric(d["頭数"], errors="coerce")
        d = d.loc[d["馬キー"].notna() & d["初角"].notna() & d["頭数"].gt(1)].copy()
        if d.empty:
            return {}
        d["frontness"] = 1.0 - ((d["初角"] - 1.0) / (d["頭数"] - 1.0)).clip(0, 1)
        d = d.sort_values(["馬キー", "日付"])
        recent = d.groupby("馬キー", group_keys=False).tail(5)
        return recent.groupby("馬キー")["frontness"].mean().clip(0, 1).to_dict()
    except Exception:
        return {}


def apply_same_day_v2_adjustment(
    horses: Iterable[dict],
    bias: dict,
    style_map: dict[str, float] | None = None,
    *,
    max_abs_pct: float = 0.08,
) -> list[dict]:
    """Combine bounded draw and running-style signals in a separate shadow score."""
    style_map = style_map or {}
    draw_rows = apply_draw_shadow_adjustment(horses, bias, max_abs_pct=0.05)
    draw_by_number = {int(h.get("number")): h for h in draw_rows if h.get("number") is not None}
    style_score = _safe_float(bias.get("runningStyleBiasScore")) or 0.0
    style_conf = _safe_float(bias.get("runningStyleConfidence")) or 0.0

    out = []
    for source in horses:
        h = dict(source)
        number = _safe_int(h.get("number"))
        base = _safe_float(h.get("aiProbability"))
        draw_adj = 0.0
        if number in draw_by_number:
            draw_adj = _safe_float(draw_by_number[number].get("sameDayShadowAdjustment")) or 0.0

        horse_key = str(h.get("horseKey") or "")
        frontness = _safe_float(style_map.get(horse_key))
        style_adj = 0.0
        if frontness is not None:
            # closer-preference + front-running horse => negative; front bias does reverse.
            style_alignment = 1.0 - 2.0 * frontness
            style_adj = 0.06 * style_score * style_conf * style_alignment

        combined = max(-max_abs_pct, min(max_abs_pct, draw_adj + style_adj))
        h["sameDayExpectedFrontness"] = _round(frontness, 4)
        h["sameDayDrawAdjustment"] = round(draw_adj, 6)
        h["sameDayStyleAdjustment"] = round(style_adj, 6)
        h["sameDayShadowAdjustment"] = round(combined, 6)
        h["sameDayShadowScore"] = round(base * (1.0 + combined), 6) if base is not None else None
        out.append(h)

    out.sort(
        key=lambda h: (
            -(_safe_float(h.get("sameDayShadowScore")) or -1e9),
            _safe_int(h.get("number")) or 999,
        )
    )
    for i, h in enumerate(out, 1):
        h["sameDayShadowRank"] = i
    return out
