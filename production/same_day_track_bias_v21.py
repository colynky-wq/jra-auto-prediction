"""Conservative adjustment policy for Same-Day Track Bias Shadow V2.1.

V2.1 keeps the V2 evidence builder unchanged and only changes how evidence may
move a horse's challenger score. The policy is intentionally conservative:
- draw-number evidence is a proxy, so it needs >=4 completed races, confidence
  >=0.40 and a non-neutral label; its contribution is capped at +/-3%.
- running-style evidence is more direct, but still needs >=3 detailed races,
  confidence >=0.50 and a non-neutral label.
- the combined research-only adjustment is capped at +/-6%.
Formal A probabilities are never overwritten.
"""
from __future__ import annotations

import math
from typing import Iterable

from same_day_track_bias import apply_draw_shadow_adjustment


DRAW_MIN_RACES = 4
DRAW_MIN_CONFIDENCE = 0.40
DRAW_MAX_ABS_PCT = 0.03
STYLE_MIN_RACES = 3
STYLE_MIN_CONFIDENCE = 0.50
STYLE_MAX_SCALE = 0.06
COMBINED_MAX_ABS_PCT = 0.06


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


def _round(value, n=4):
    return round(value, n) if value is not None else None


def adjustment_gate_summary(bias: dict) -> dict:
    completed = _safe_int(bias.get("completedRaceCount")) or 0
    draw_conf = _safe_float(bias.get("confidence")) or 0.0
    draw_label = str(bias.get("drawBiasLabel") or "")
    detail = _safe_int(bias.get("detailRaceCount")) or 0
    style_conf = _safe_float(bias.get("runningStyleConfidence")) or 0.0
    style_label = str(bias.get("runningStyleBiasLabel") or "")

    draw_enabled = (
        completed >= DRAW_MIN_RACES
        and draw_conf >= DRAW_MIN_CONFIDENCE
        and draw_label in {"inner_draw_positive", "outer_draw_positive"}
    )
    style_enabled = (
        detail >= STYLE_MIN_RACES
        and style_conf >= STYLE_MIN_CONFIDENCE
        and style_label in {"front_positive", "closer_positive"}
    )
    return {
        "version": "same_day_adjustment_policy_v2_1",
        "drawEnabled": draw_enabled,
        "drawReason": "eligible" if draw_enabled else "insufficient_or_neutral",
        "drawMinRaces": DRAW_MIN_RACES,
        "drawMinConfidence": DRAW_MIN_CONFIDENCE,
        "drawMaxAbsPct": DRAW_MAX_ABS_PCT,
        "styleEnabled": style_enabled,
        "styleReason": "eligible" if style_enabled else "insufficient_or_neutral",
        "styleMinRaces": STYLE_MIN_RACES,
        "styleMinConfidence": STYLE_MIN_CONFIDENCE,
        "combinedMaxAbsPct": COMBINED_MAX_ABS_PCT,
    }


def apply_same_day_v21_adjustment(
    horses: Iterable[dict],
    bias: dict,
    style_map: dict[str, float] | None = None,
) -> list[dict]:
    """Apply confidence-gated draw/style adjustments without touching Formal A."""
    source_rows = [dict(h) for h in horses]
    style_map = style_map or {}
    gates = adjustment_gate_summary(bias)

    draw_by_number: dict[int, dict] = {}
    if gates["drawEnabled"]:
        draw_rows = apply_draw_shadow_adjustment(
            source_rows, bias, max_abs_pct=DRAW_MAX_ABS_PCT
        )
        draw_by_number = {
            int(h.get("number")): h
            for h in draw_rows
            if h.get("number") is not None
        }

    style_score = _safe_float(bias.get("runningStyleBiasScore")) or 0.0
    style_conf = _safe_float(bias.get("runningStyleConfidence")) or 0.0

    out = []
    for source in source_rows:
        h = dict(source)
        number = _safe_int(h.get("number"))
        base = _safe_float(h.get("aiProbability"))

        draw_adj = 0.0
        if gates["drawEnabled"] and number in draw_by_number:
            draw_adj = (
                _safe_float(draw_by_number[number].get("sameDayShadowAdjustment"))
                or 0.0
            )

        horse_key = str(h.get("horseKey") or "")
        frontness = _safe_float(style_map.get(horse_key))
        style_adj = 0.0
        if gates["styleEnabled"] and frontness is not None:
            # closer-positive rewards low frontness; front-positive rewards high frontness.
            style_alignment = 1.0 - 2.0 * frontness
            style_adj = STYLE_MAX_SCALE * style_score * style_conf * style_alignment

        combined = max(
            -COMBINED_MAX_ABS_PCT,
            min(COMBINED_MAX_ABS_PCT, draw_adj + style_adj),
        )
        h["sameDayExpectedFrontness"] = _round(frontness, 4)
        h["sameDayDrawGateEnabled"] = bool(gates["drawEnabled"])
        h["sameDayStyleGateEnabled"] = bool(gates["styleEnabled"])
        h["sameDayDrawAdjustment"] = round(draw_adj, 6)
        h["sameDayStyleAdjustment"] = round(style_adj, 6)
        h["sameDayShadowAdjustment"] = round(combined, 6)
        h["sameDayShadowScore"] = (
            round(base * (1.0 + combined), 6) if base is not None else None
        )
        out.append(h)

    out.sort(
        key=lambda h: (
            -(_safe_float(h.get("sameDayShadowScore")) or -1e9),
            _safe_int(h.get("number")) or 999,
        )
    )
    for rank, h in enumerate(out, 1):
        h["sameDayShadowRank"] = rank
    return out
