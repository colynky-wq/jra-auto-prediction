"""Accelerated mixed 100-race reference review for portfolio shadow v1.

This is deliberately separate from the formal prospective 100R review.
It combines the already-existing development/backfill cohort with the first
future prospective races only to obtain an earlier *reference* read.
It must never be presented as forward proof or used to tune v1 in place.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
SOURCE_VERSION = "portfolio-shadow-v1-20261019"
PROSPECTIVE_START = "2026-10-24"
REFERENCE_TARGET = 100
BASELINE_DEVELOPMENT_RACES = 69
VERSION = "portfolio-early-reference-100-v1-20261020"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def pct(n: int, d: int) -> float | None:
    return round(n / d * 100.0, 3) if d else None


def validate_source(data: dict[str, Any]) -> None:
    if data.get("schemaVersion") != 1 or data.get("version") != SOURCE_VERSION:
        raise ValueError("unexpected portfolio source/version")
    if data.get("status") != "research_display_only":
        raise ValueError("source must remain research-only")
    if data.get("prospectiveStartDate") != PROSPECTIVE_START:
        raise ValueError("prospective start changed")
    if data.get("formalRecommendationsEnabled") is not False or data.get("autoBetEnabled") is not False:
        raise ValueError("formal/auto betting must remain disabled")


def settled_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [r for r in rows if r.get("planType") != "skip"]
    settled = [r for r in selected if r.get("settled") is True]
    stake = sum(int(r.get("totalStakeYen", 0)) for r in settled)
    returned = sum(int(r.get("returnYen", 0)) for r in settled)
    hit = sum(r.get("hitAny") is True for r in settled)
    profitable = sum(r.get("profitable") is True for r in settled)
    torigami = sum(r.get("torigami") is True for r in settled)
    return {
        "races": len(rows),
        "selectedRaces": len(selected),
        "skippedRaces": len(rows) - len(selected),
        "participationRatePct": pct(len(selected), len(rows)),
        "settledSelectedRaces": len(settled),
        "resultsPendingSelectedRaces": len(selected) - len(settled),
        "hitAnyRaces": hit,
        "hitRatePct": pct(hit, len(settled)),
        "profitableRaces": profitable,
        "profitableRatePct": pct(profitable, len(settled)),
        "torigamiRaces": torigami,
        "torigamiRatePct": pct(torigami, len(settled)),
        "stakeYen": stake,
        "returnYen": returned,
        "profitYen": returned - stake,
        "returnIndex": round(returned / stake, 6) if stake else None,
    }


def build(data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    validate_source(data)
    now = now or datetime.now(JST)
    races = data.get("races")
    if not isinstance(races, list):
        raise ValueError("portfolio races must be list")

    dev = [r for r in races if isinstance(r, dict) and r.get("scope") == "development_backfill"]
    prospective = [r for r in races if isinstance(r, dict) and r.get("scope") == "prospective"]
    key = lambda r: (str(r.get("raceDate", "")), str(r.get("startTime") or ""), str(r.get("raceID", "")))
    dev.sort(key=key)
    prospective.sort(key=key)

    if any(r.get("ruleVersion") != SOURCE_VERSION for r in dev + prospective):
        raise ValueError("mixed portfolio rule versions")

    ids = [(str(r.get("raceDate")), str(r.get("raceID"))) for r in dev]
    cohort_hash = hashlib.sha256("\n".join(f"{d}:{r}" for d, r in ids).encode()).hexdigest()
    baseline_drift = len(dev) != BASELINE_DEVELOPMENT_RACES

    dev_for_reference = dev[:BASELINE_DEVELOPMENT_RACES]
    future_needed = max(0, REFERENCE_TARGET - len(dev_for_reference))
    future_for_reference = prospective[:future_needed]
    mixed = dev_for_reference + future_for_reference

    plan_counts = Counter(str(r.get("planLabel") or r.get("planType")) for r in mixed if r.get("planType") != "skip")
    skip_counts = Counter(str(r.get("reason") or "理由不明") for r in mixed if r.get("planType") == "skip")

    by_scope = {
        "development": settled_summary(dev_for_reference),
        "prospectiveContribution": settled_summary(future_for_reference),
        "combinedReference": settled_summary(mixed),
    }

    if baseline_drift:
        status = "baseline_drift_detected"
    elif len(mixed) < REFERENCE_TARGET:
        status = "collecting"
    elif by_scope["combinedReference"]["resultsPendingSelectedRaces"] > 0:
        status = "reference_100_reached_results_pending"
    else:
        status = "reference_100_ready"

    return {
        "schemaVersion": 1,
        "version": VERSION,
        "sourceVersion": SOURCE_VERSION,
        "status": "development_mixed_reference_only",
        "reviewStatus": status,
        "generatedAtJST": now.astimezone(JST).isoformat(timespec="seconds"),
        "prospectiveStartDate": PROSPECTIVE_START,
        "referenceTargetRaces": REFERENCE_TARGET,
        "baselineDevelopmentRaceCountExpected": BASELINE_DEVELOPMENT_RACES,
        "baselineDevelopmentRaceCountObserved": len(dev),
        "baselineCohortHash": cohort_hash,
        "baselineDriftDetected": baseline_drift,
        "developmentRacesUsed": len(dev_for_reference),
        "prospectiveRacesUsed": len(future_for_reference),
        "combinedReferenceRaces": len(mixed),
        "remainingToReference100": max(0, REFERENCE_TARGET - len(mixed)),
        "progressPct": round(min(1.0, len(mixed) / REFERENCE_TARGET) * 100.0, 1),
        "summaries": by_scope,
        "planMix": [{"planLabel": k, "races": v} for k, v in sorted(plan_counts.items(), key=lambda x: (-x[1], x[0]))],
        "topSkipReasons": [{"reason": k, "races": v} for k, v in sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))[:10]],
        "interpretationPolicy": {
            "whatThisIs": "既存development/backfillと最初のfutureレースを混ぜた早期参考評価。",
            "notForwardProof": "これは正式forward100Rの代替ではなく、性能証明には使わない。",
            "noTuning": "この参考結果を見てportfolio-shadow-v1の閾値を変更しない。改善案はv2 shadowに分離する。",
            "formalForwardReview": "正式な短期評価は2026-10-24以降だけで100Rを集めるPortfolioReview100を使う。"
        }
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    out = build(read_json(root / "PortfolioResearchData.json"))
    write_json(root / "PortfolioEarlyReview100.json", out)
    print(json.dumps({
        "reviewStatus": out["reviewStatus"],
        "combinedReferenceRaces": out["combinedReferenceRaces"],
        "remainingToReference100": out["remainingToReference100"],
        "combined": out["summaries"]["combinedReference"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
