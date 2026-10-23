"""Cross-pool KeibaAI recommendation research, display only.

This module never fetches odds, changes predictions, sizes stakes, or enables a
formal recommendation. It only reads already-published RaceData plus frozen
BetResearchData/SignalData and compares the best available candidate from each
ordinary pool. Missing real odds stay explicitly pending.

The rules are frozen before the prospective start date. Any later tuning must
use a new VERSION so forward results are not used to rewrite the rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
VERSION = "all-bet-recommendation-shadow-v1-20261023"
PROSPECTIVE_START = "2026-10-24"
BET_TYPES = (
    "win", "place", "frame_quinella", "quinella",
    "wide", "exacta", "trio", "trifecta",
)
LABELS = {
    "win": "単勝", "place": "複勝", "frame_quinella": "枠連", "quinella": "馬連",
    "wide": "ワイド", "exacta": "馬単", "trio": "3連複", "trifecta": "3連単",
}
# Preregistered research gates. These are conservative display gates, not claims
# of profitability. Higher-variance pools require a larger expected-value margin.
RULES = {
    "win": {"minProbabilityPct": 15.0, "minExpectedMultiplier": 1.03, "riskPenalty": 1.00},
    "place": {"minProbabilityPct": 25.0, "minExpectedMultiplier": 1.03, "riskPenalty": 1.00},
    "frame_quinella": {"minProbabilityPct": 10.0, "minExpectedMultiplier": 1.05, "riskPenalty": 1.20},
    "quinella": {"minProbabilityPct": 8.0, "minExpectedMultiplier": 1.05, "riskPenalty": 1.20},
    "wide": {"minProbabilityPct": 8.0, "minExpectedMultiplier": 1.05, "riskPenalty": 1.20},
    "exacta": {"minProbabilityPct": 4.0, "minExpectedMultiplier": 1.08, "riskPenalty": 1.45},
    "trio": {"minProbabilityPct": 3.0, "minExpectedMultiplier": 1.10, "riskPenalty": 1.60},
    "trifecta": {"minProbabilityPct": 1.0, "minExpectedMultiplier": 1.15, "riskPenalty": 2.00},
}
READY_STATUSES = {"ready", "awaiting_results", "matched", "payouts_pending"}
PENDING_STATUSES = {"missing_record", "held", "failed"}
ALLOWED_BATTLE = {"S", "A", "B"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _day_of_racedata(races: list[dict[str, Any]]) -> str:
    days = {str(r.get("raceDate") or "") for r in races}
    if len(days) != 1:
        raise ValueError(f"RaceData must contain exactly one race date, got {sorted(days)}")
    day = next(iter(days))
    datetime.strptime(day, "%Y-%m-%d")
    return day


def _load_day_source(root: Path, day: str, name: str) -> dict[str, Any] | None:
    dated = root / "reports" / day.replace("-", "") / name
    if dated.exists():
        value = read_json(dated)
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        return value
    current = root / name
    if current.exists():
        value = read_json(current)
        if isinstance(value, dict) and str(value.get("raceDate") or "") == day:
            return value
    return None


def _bet_index(payload: dict[str, Any] | None, day: str) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not payload:
        return out
    for row in payload.get("races") or []:
        if row.get("raceDate") != day or row.get("betType") not in BET_TYPES:
            continue
        rid = str(row.get("raceID") or "")
        kind = str(row.get("betType") or "")
        key = (rid, kind)
        if not rid or key in out:
            raise ValueError(f"duplicate/invalid BetResearchData row: {key}")
        out[key] = row
    return out


def _signal_index(payload: dict[str, Any] | None, day: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not payload:
        return out
    for row in payload.get("races") or []:
        if row.get("raceDate") != day:
            continue
        rid = str(row.get("raceID") or "")
        if not rid or rid in out:
            raise ValueError(f"duplicate/invalid SignalData race: {rid}")
        out[rid] = row
    return out


def _candidate(kind: str, row: dict[str, Any]) -> dict[str, Any] | None:
    p = finite(row.get("probabilityPct"))
    low = finite(row.get("oddsLow"))
    high = finite(row.get("oddsHigh"))
    ev = finite(row.get("expectedMultiplierLow"))
    if p is None or low is None or high is None or ev is None:
        return None
    if not (0 < p <= 100 and 1 <= low <= high and ev > 0):
        return None
    rule = RULES[kind]
    if p < rule["minProbabilityPct"] or ev < rule["minExpectedMultiplier"]:
        return None
    # Probability-weighted conservative edge, penalized for pool variance.
    # This is a deterministic research ordering only, not a Kelly fraction or
    # a claim about future return.
    score = (p / 100.0) * max(0.0, ev - 1.0) / rule["riskPenalty"]
    return {
        "betType": kind,
        "betTypeLabel": LABELS[kind],
        "candidateID": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "probabilityPct": round(p, 6),
        "oddsLow": round(low, 6),
        "oddsHigh": round(high, 6),
        "expectedMultiplierLow": round(ev, 6),
        "expectedMultiplierHigh": finite(row.get("expectedMultiplierHigh")),
        "researchScore": round(score, 9),
        "riskPenalty": rule["riskPenalty"],
    }


def best_for_pool(kind: str, row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row or row.get("status") not in READY_STATUSES:
        return None
    values = [_candidate(kind, c) for c in (row.get("candidates") or [])]
    valid = [v for v in values if v is not None]
    valid.sort(key=lambda x: (-x["researchScore"], -x["probabilityPct"], -x["expectedMultiplierLow"], x["candidateID"]))
    return valid[0] if valid else None


def build_race(race: dict[str, Any], bets: dict[tuple[str, str], dict[str, Any]],
               signal: dict[str, Any] | None) -> dict[str, Any]:
    rid = str(race.get("raceID") or "")
    if not rid:
        raise ValueError("raceID is required")
    coverage = []
    candidates = []
    pending = []
    for kind in BET_TYPES:
        row = bets.get((rid, kind))
        state = str(row.get("status") or "missing_record") if row else "missing_record"
        best = best_for_pool(kind, row)
        coverage.append({
            "betType": kind,
            "label": LABELS[kind],
            "sourceStatus": state,
            "realOddsAvailable": bool(best is not None or (row and row.get("status") in READY_STATUSES and row.get("candidates"))),
            "qualifyingCandidate": best is not None,
        })
        if state in PENDING_STATUSES or row is None:
            pending.append(kind)
        if best is not None:
            candidates.append(best)

    candidates.sort(key=lambda x: (-x["researchScore"], -x["probabilityPct"], -x["expectedMultiplierLow"], BET_TYPES.index(x["betType"]), x["candidateID"]))
    top = candidates[:3]
    battle = (signal or {}).get("battle") or {}
    chaos = (signal or {}).get("chaos") or {}
    grade = battle.get("grade") if isinstance(battle.get("grade"), str) else None
    eligible = (signal or {}).get("eligible")

    if not candidates:
        if pending:
            status = "odds_pending"
            reason = "実オッズの初回保存が未完了の券種があります。推定オッズでは補いません。"
        else:
            status = "skip"
            reason = "8券種を確認しましたが、事前固定した確率・期待値条件を満たす候補がありません。"
    elif signal is None or eligible is not True or grade is None:
        status = "signal_pending"
        reason = "実オッズ候補はありますが、正式な勝負度シグナル確定前なので研究候補として確定しません。"
    elif grade not in ALLOWED_BATTLE:
        status = "skip"
        reason = "勝負度がC以下のため、期待値候補があってもこのレースは見送ります。"
    else:
        status = "candidate"
        reason = "勝負度B以上で、保存済み実オッズを使った8券種横断条件を満たしました。"

    return {
        "raceDate": race.get("raceDate"),
        "raceID": rid,
        "venue": race.get("venue"),
        "raceNumber": race.get("raceNumber"),
        "raceName": race.get("raceName"),
        "startTime": race.get("startTime"),
        "recommendationStatus": status,
        "reason": reason,
        "battleGrade": grade,
        "chaosLevel": chaos.get("level") if isinstance(chaos.get("level"), int) else None,
        "bestBet": top[0] if status == "candidate" and top else None,
        "betRecommendations": top if status == "candidate" else [],
        "qualifyingPoolCount": len(candidates),
        "pendingBetTypes": pending,
        "coverage": coverage,
        "formalRecommendation": False,
        "stakeSizingEnabled": False,
    }


def build(root: Path) -> dict[str, Any]:
    root = Path(root)
    races = read_json(root / "RaceData.json")
    if not isinstance(races, list) or not races:
        raise ValueError("RaceData.json must be a nonempty array")
    day = _day_of_racedata(races)
    bets = _bet_index(_load_day_source(root, day, "BetResearchData.json"), day)
    signals = _signal_index(_load_day_source(root, day, "SignalData.json"), day)
    built = [build_race(race, bets, signals.get(str(race.get("raceID") or ""))) for race in races]
    counts = {status: sum(r["recommendationStatus"] == status for r in built)
              for status in ("candidate", "skip", "odds_pending", "signal_pending")}
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_display_only",
        "raceDate": day,
        "generatedAtJST": datetime.now(JST).isoformat(timespec="seconds"),
        "prospectiveStartDate": PROSPECTIVE_START,
        "rulesFrozenAtJST": "2026-10-23",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "ordinaryBetTypes": [{"id": k, "label": LABELS[k], **RULES[k]} for k in BET_TYPES],
        "win5Policy": "WIN5は5対象レース共通締切の別研究として扱い、1レース単位の8券種横断順位には混ぜない。",
        "scoreMeaning": "researchScore = 的中確率 × 保守的期待値超過分 ÷ 券種リスク係数。研究上の並び順であり利益予測ではない。",
        "counts": counts,
        "races": built,
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    value = build(root)
    write_json(root / "AllBetRecommendationData.json", value)
    write_json(root / "reports" / value["raceDate"].replace("-", "") / "AllBetRecommendationData.json", value)
    print("ALL_BET_RECOMMENDATION_SHADOW", value["raceDate"], value["counts"])


if __name__ == "__main__":
    main()
