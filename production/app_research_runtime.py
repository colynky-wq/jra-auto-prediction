from __future__ import annotations

import math
from collections import Counter

STYLE_ORDER = ["逃げ", "先行", "差し", "追込"]
METHOD_VERSION = "history-corner-six-races-v2"


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def classify_style(first_corner, field_size) -> str | None:
    pos, n = finite(first_corner), finite(field_size)
    if pos is None or n is None or n <= 1 or not n.is_integer():
        return None
    if not pos.is_integer() or not 1 <= pos <= n:
        return None
    if pos == 1:
        return "逃げ"
    ratio = (pos - 1) / (n - 1)
    return "先行" if ratio <= 0.33 else "差し" if ratio <= 0.67 else "追込"


def tendency(labels: list[str]) -> dict | None:
    if not labels:
        return None
    counts = Counter(labels)
    n = len(labels)
    dominant = max(STYLE_ORDER, key=lambda style: counts[style])
    share = counts[dominant] / n
    confidence = "高" if n >= 5 and share >= 0.60 else "中" if n >= 3 and share >= 0.45 else "低"
    return {
        "sampleRaces": n,
        "dominantStyle": dominant,
        "confidence": confidence,
        "distributionPct": {style: round(counts[style] / n * 100, 1) for style in STYLE_ORDER},
    }


def practical_metrics(probability: float, odds: float) -> dict:
    if not 0 <= probability <= 1 or not math.isfinite(probability):
        raise ValueError("invalid AI probability")
    if not math.isfinite(odds) or odds <= 0:
        raise ValueError("invalid win odds")
    ev = probability * odds
    score = max(0.0, (ev - 1) / (odds - 1)) if odds > 1 and ev > 1 else 0.0
    return {
        "expectedValue": round(ev, 4),
        "practicalScore": round(min(score, 1) * 100, 3),
    }


def practical_candidates(race: dict) -> list[dict]:
    ranked = sorted(race["horses"], key=lambda horse: (-float(horse["marketProbability"]), horse["number"]))
    candidates = []
    for market_rank, horse in enumerate(ranked, 1):
        probability = float(horse["aiProbability"]) / 100
        odds = float(horse["odds"])
        if market_rank < 6 or odds < 8 or probability < 0.01:
            continue
        market = float(horse["marketProbability"]) / 100
        ratio = probability / market if market > 0 else None
        metrics = practical_metrics(probability, odds)
        expected_value = probability * odds
        candidates.append({
            "number": horse["number"], "name": horse["name"],
            "odds": odds, "marketRank": market_rank,
            "aiProbabilityPct": float(horse["aiProbability"]),
            "aiMarketRatio": round(ratio, 4) if ratio is not None else None,
            "legacyV1Eligible": ratio is not None and ratio >= 1.20,
            **metrics,
            "shadowGates": {
                "balanced_2pct_ev105": probability >= 0.02 and expected_value >= 1.05,
                "balanced_3pct_ev110": probability >= 0.03 and expected_value >= 1.10,
                "hit_5pct_ev105": probability >= 0.05 and expected_value >= 1.05,
                "value_ev120": expected_value >= 1.20,
            },
        })
    candidates.sort(key=lambda item: (-item["practicalScore"], -item["aiProbabilityPct"], -item["expectedValue"], item["number"]))
    for rank, item in enumerate(candidates, 1):
        item["practicalRank"] = rank
    return candidates
