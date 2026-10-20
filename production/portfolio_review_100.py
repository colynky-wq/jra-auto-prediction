"""Build the preregistered 100-race review for the portfolio shadow study.

Evaluation only: this module never changes portfolio selection rules, predictions,
thresholds, or stake sizing.  It reads PortfolioResearchData.json and summarizes
prospective races beginning 2026-10-24.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
SOURCE_VERSION = "portfolio-shadow-v1-20261019"
PROSPECTIVE_START = "2026-10-24"
TARGET_RACES = 100
REVIEW_VERSION = "portfolio-review-100-v1-20261020"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def pct(n: int, d: int) -> float | None:
    return round(n / d * 100.0, 3) if d else None


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_source(data: dict[str, Any]) -> None:
    if data.get("schemaVersion") != 1:
        raise ValueError("unexpected portfolio schema")
    if data.get("version") != SOURCE_VERSION:
        raise ValueError("unexpected portfolio rule version")
    if data.get("status") != "research_display_only":
        raise ValueError("portfolio must remain research-only")
    if data.get("prospectiveStartDate") != PROSPECTIVE_START:
        raise ValueError("prospective start date changed")
    if data.get("formalRecommendationsEnabled") is not False:
        raise ValueError("formal recommendations must remain disabled")
    if data.get("autoBetEnabled") is not False:
        raise ValueError("auto betting must remain disabled")
    if data.get("stakeSizingEnabled") is not False:
        raise ValueError("stake sizing must remain disabled")


def plan_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if r.get("settled") is True]
    stake = sum(int(r.get("totalStakeYen", 0)) for r in settled)
    returned = sum(int(r.get("returnYen", 0)) for r in settled)
    hit = sum(r.get("hitAny") is True for r in settled)
    profitable = sum(r.get("profitable") is True for r in settled)
    torigami = sum(r.get("torigami") is True for r in settled)
    return {
        "selectedRaces": len(rows),
        "settledSelectedRaces": len(settled),
        "hitAnyRaces": hit,
        "profitableRaces": profitable,
        "torigamiRaces": torigami,
        "hitRatePct": pct(hit, len(settled)),
        "profitableRatePct": pct(profitable, len(settled)),
        "torigamiRatePct": pct(torigami, len(settled)),
        "stakeYen": stake,
        "returnYen": returned,
        "returnIndex": round(returned / stake, 6) if stake else None,
    }


def build(data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    validate_source(data)
    now = now or datetime.now(JST)
    races = data.get("races")
    if not isinstance(races, list):
        raise ValueError("portfolio races must be a list")

    prospective: list[dict[str, Any]] = []
    ids: set[tuple[str, str]] = set()
    for row in races:
        if not isinstance(row, dict) or row.get("scope") != "prospective":
            continue
        day = row.get("raceDate")
        rid = row.get("raceID")
        if not isinstance(day, str) or not isinstance(rid, str) or day < PROSPECTIVE_START:
            raise ValueError("invalid prospective race identity/date")
        key = (day, rid)
        if key in ids:
            raise ValueError(f"duplicate prospective race: {key}")
        ids.add(key)
        if row.get("ruleVersion") != SOURCE_VERSION:
            raise ValueError("mixed portfolio rule versions")
        prospective.append(row)

    prospective.sort(key=lambda r: (r.get("raceDate", ""), r.get("startTime") or "", r.get("raceID", "")))
    seen = len(prospective)
    selected = [r for r in prospective if r.get("planType") != "skip"]
    skipped = [r for r in prospective if r.get("planType") == "skip"]
    settled = [r for r in selected if r.get("settled") is True]

    hit = sum(r.get("hitAny") is True for r in settled)
    profitable = sum(r.get("profitable") is True for r in settled)
    torigami = sum(r.get("torigami") is True for r in settled)
    stake = sum(int(r.get("totalStakeYen", 0)) for r in settled)
    returned = sum(int(r.get("returnYen", 0)) for r in settled)

    plan_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        plan_groups[str(row.get("planType", "unknown"))].append(row)

    plan_labels: dict[str, str] = {}
    for rows in plan_groups.values():
        for row in rows:
            plan_labels.setdefault(str(row.get("planType")), str(row.get("planLabel") or row.get("planType")))

    by_plan = []
    for plan_type in sorted(plan_groups):
        rows = plan_groups[plan_type]
        by_plan.append({
            "planType": plan_type,
            "planLabel": plan_labels.get(plan_type, plan_type),
            **plan_summary(rows),
        })

    skip_reasons = Counter(str(r.get("reason") or "理由不明") for r in skipped)
    race_days = sorted({str(r.get("raceDate")) for r in prospective})

    if seen == 0:
        status = "waiting_for_prospective_start"
    elif seen < TARGET_RACES:
        status = "collecting"
    elif len(settled) < len(selected):
        status = "target_reached_results_pending"
    else:
        status = "ready_for_short_term_review"

    return {
        "schemaVersion": 1,
        "version": REVIEW_VERSION,
        "sourceVersion": SOURCE_VERSION,
        "status": "research_evaluation_only",
        "reviewStatus": status,
        "generatedAtJST": now.astimezone(JST).isoformat(timespec="seconds"),
        "prospectiveStartDate": PROSPECTIVE_START,
        "targetRaces": TARGET_RACES,
        "racesSeen": seen,
        "remainingTo100": max(0, TARGET_RACES - seen),
        "progressPct": round(min(1.0, seen / TARGET_RACES) * 100.0, 1),
        "raceDays": len(race_days),
        "firstRaceDate": race_days[0] if race_days else None,
        "lastRaceDate": race_days[-1] if race_days else None,
        "participation": {
            "selectedRaces": len(selected),
            "skippedRaces": len(skipped),
            "participationRatePct": pct(len(selected), seen),
            "skipRatePct": pct(len(skipped), seen),
        },
        "outcomes": {
            "settledSelectedRaces": len(settled),
            "resultsPendingSelectedRaces": len(selected) - len(settled),
            "hitAnyRaces": hit,
            "profitableRaces": profitable,
            "torigamiRaces": torigami,
            "hitRatePct": pct(hit, len(settled)),
            "profitableRatePct": pct(profitable, len(settled)),
            "torigamiRatePctOfSettledSelected": pct(torigami, len(settled)),
            "torigamiRatePctOfHitRaces": pct(torigami, hit),
            "stakeYen": stake,
            "returnYen": returned,
            "returnIndex": round(returned / stake, 6) if stake else None,
        },
        "planMix": by_plan,
        "skipReasons": [
            {"reason": reason, "races": count, "shareOfSkippedPct": pct(count, len(skipped))}
            for reason, count in sorted(skip_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        "interpretationPolicy": {
            "shortTermPurpose": "100Rは合否判定ではなく、動作異常・極端な失敗・参加率・見送り率・トリガミ率・プラン構成を点検する。",
            "noThresholdTuning": "100Rの結果を見てportfolio-shadow-v1の閾値を変更しない。改善案はv2 shadowとして分離する。",
            "roiWarning": "回収指数は標本が小さい間は振れが大きいため、単独で採否を決めない。",
        },
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    source = read_json(root / "PortfolioResearchData.json")
    review = build(source)
    write_json(root / "PortfolioReview100.json", review)
    newest = review.get("lastRaceDate")
    if isinstance(newest, str):
        write_json(root / "reports" / newest.replace("-", "") / "PortfolioReview100.json", review)
    print(json.dumps({
        "reviewStatus": review["reviewStatus"],
        "racesSeen": review["racesSeen"],
        "remainingTo100": review["remainingTo100"],
        "participation": review["participation"],
        "outcomes": review["outcomes"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
