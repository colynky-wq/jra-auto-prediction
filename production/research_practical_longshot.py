from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
REPORTS_DIR = REPO / "reports"
OUT_PATH = REPORTS_DIR / "PracticalLongshotShadow.json"


def _finite(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _pct(value) -> float:
    x = _finite(value, 0.0)
    return max(0.0, min(1.0, float(x) / 100.0))


def practical_metrics(ai_probability: float, odds: float) -> dict:
    """Continuous value/hit-rate metrics for research ranking only.

    Kelly is used only as a ranking statistic. It is NOT a stake-sizing rule.
    """
    p = max(0.0, min(1.0, float(ai_probability)))
    o = max(0.0, float(odds))
    ev = p * o
    break_even = (1.0 / o) if o > 0 else 1.0
    cushion = p - break_even
    if o <= 1.0 or ev <= 1.0:
        kelly = 0.0
    else:
        kelly = max(0.0, min(1.0, (ev - 1.0) / (o - 1.0)))
    return {
        "expectedValue": round(ev, 4),
        "breakEvenProbabilityPct": round(break_even * 100.0, 3),
        "probabilityCushionPct": round(cushion * 100.0, 3),
        "practicalScore": round(kelly * 100.0, 3),
    }


def _build_race(race: dict) -> dict | None:
    horses = [dict(h) for h in (race.get("horses") or [])]
    if not horses:
        return None

    rows = []
    for h in horses:
        number = h.get("number")
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        odds = _finite(h.get("odds"))
        if odds is None or odds <= 0:
            continue
        market = _pct(h.get("marketProbability"))
        ai = _pct(h.get("aiProbability"))
        ratio = ai / max(market, 1e-9)
        edge = ai - market
        rows.append({
            "number": number,
            "name": str(h.get("name") or ""),
            "odds": round(odds, 1),
            "marketProbability": market,
            "aiProbability": ai,
            "aiMarketRatio": ratio,
            "aiMarketEdge": edge,
        })

    if not rows:
        return None

    rows.sort(key=lambda x: (-x["marketProbability"], x["number"]))
    for rank, row in enumerate(rows, start=1):
        row["marketRank"] = rank

    pool = []
    for row in rows:
        # Broad research pool. Do not freeze a final production threshold yet.
        if row["marketRank"] < 6 or row["odds"] < 8.0 or row["aiProbability"] < 0.01:
            continue
        item = {
            "number": row["number"],
            "name": row["name"],
            "odds": row["odds"],
            "marketRank": row["marketRank"],
            "marketProbabilityPct": round(row["marketProbability"] * 100.0, 3),
            "aiProbabilityPct": round(row["aiProbability"] * 100.0, 3),
            "aiMarketRatio": round(row["aiMarketRatio"], 4),
            "aiMarketEdgePct": round(row["aiMarketEdge"] * 100.0, 3),
            "legacyV1Eligible": bool(row["aiMarketRatio"] >= 1.20),
        }
        item.update(practical_metrics(row["aiProbability"], row["odds"]))
        # Multiple pre-registered shadow gates are kept side-by-side so that
        # future results can choose a useful hit-rate/value trade-off instead
        # of selecting one threshold after seeing the outcomes.
        item["shadowGates"] = {
            "balanced_2pct_ev105": bool(row["aiProbability"] >= 0.02 and item["expectedValue"] >= 1.05),
            "balanced_3pct_ev110": bool(row["aiProbability"] >= 0.03 and item["expectedValue"] >= 1.10),
            "hit_5pct_ev105": bool(row["aiProbability"] >= 0.05 and item["expectedValue"] >= 1.05),
            "value_ev120": bool(item["expectedValue"] >= 1.20),
        }
        pool.append(item)

    # Practical rank balances price and actual chance of winning. At the same
    # EV a higher win probability/lower odds candidate ranks above a rare-hit
    # high-odds candidate.
    pool.sort(key=lambda x: (
        -float(x.get("practicalScore") or 0.0),
        -float(x.get("aiProbabilityPct") or 0.0),
        -float(x.get("expectedValue") or 0.0),
        int(x.get("number") or 0),
    ))
    for rank, item in enumerate(pool, start=1):
        item["practicalRank"] = rank

    return {
        "raceDate": race.get("raceDate"),
        "raceID": str(race.get("raceID") or ""),
        "venue": race.get("venue"),
        "raceNumber": race.get("raceNumber"),
        "startTime": race.get("startTime"),
        "raceName": race.get("raceName"),
        "course": race.get("course"),
        "candidateCount": len(pool),
        "candidates": pool,
    }


def build() -> dict:
    races = json.loads(RACEDATA_PATH.read_text(encoding="utf-8")) if RACEDATA_PATH.exists() else []
    out = []
    for race in races if isinstance(races, list) else []:
        built = _build_race(race)
        if built is not None:
            out.append(built)
    out.sort(key=lambda r: (str(r.get("raceDate") or ""), str(r.get("startTime") or ""), str(r.get("raceID") or "")))
    return {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "status": "shadow_research_only",
        "meaning": "大穴候補を市場との差だけでなく、AI勝率・実単勝オッズ・期待値・実戦優先度で比較する研究出力。",
        "stakeSizingEnabled": False,
        "rankingMethod": "positive Kelly fraction for ranking only; AI win probability and EV are tie-breakers",
        "races": out,
    }


def main() -> None:
    payload = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("races:", len(payload["races"]))
    print("candidates:", sum(int(r.get("candidateCount") or 0) for r in payload["races"]))
    print("output:", OUT_PATH)


if __name__ == "__main__":
    main()
