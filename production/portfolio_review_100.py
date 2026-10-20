"""Build the preregistered first-100-race review for portfolio shadow v1.

Evaluation only: this module never changes portfolio selection rules, predictions,
thresholds, or stake sizing.  The review cohort is chronologically locked to the
first 100 prospective races beginning 2026-10-24.  Later races never rewrite the
100-race snapshot.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
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
    return round(n / d * 100.0, 2) if d else None


def ratio(n: int, d: int) -> float | None:
    return round(n / d, 6) if d else None


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


def race_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("raceDate", "")), str(row.get("startTime") or ""), str(row.get("raceID", "")))


def is_selected(row: dict[str, Any]) -> bool:
    return row.get("planType") != "skip"


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [r for r in rows if is_selected(r)]
    skipped = [r for r in rows if not is_selected(r)]
    settled = [r for r in selected if r.get("settled") is True]
    pending = [r for r in selected if r.get("settled") is not True]

    stake = 0
    returned = 0
    for row in settled:
        s = row.get("totalStakeYen")
        y = row.get("returnYen")
        if type(s) is not int or s <= 0:
            raise ValueError(f"invalid settled stake: {row.get('raceID')}")
        if type(y) is not int or y < 0:
            raise ValueError(f"invalid settled return: {row.get('raceID')}")
        stake += s
        returned += y

    hit = sum(r.get("hitAny") is True for r in settled)
    profitable = sum(r.get("profitable") is True for r in settled)
    torigami = sum(r.get("torigami") is True for r in settled)

    return {
        "races": len(rows),
        "selectedRaces": len(selected),
        "skippedRaces": len(skipped),
        "participationRatePct": pct(len(selected), len(rows)),
        "skipRatePct": pct(len(skipped), len(rows)),
        "settledSelectedRaces": len(settled),
        "resultsPendingSelectedRaces": len(pending),
        "hitAnyRaces": hit,
        "hitRatePctOfSettledSelected": pct(hit, len(settled)),
        "profitableRaces": profitable,
        "profitableRatePctOfSettledSelected": pct(profitable, len(settled)),
        "torigamiRaces": torigami,
        "torigamiRatePctOfSettledSelected": pct(torigami, len(settled)),
        "torigamiRatePctOfHitRaces": pct(torigami, hit),
        "stakeYen": stake,
        "returnYen": returned,
        "profitYen": returned - stake,
        "returnIndex": ratio(returned, stake),
    }


def breakdown(rows: list[dict[str, Any]], field: str, order: list[Any]) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get(field), []).append(row)
    rank = {value: index for index, value in enumerate(order)}
    keys = sorted(groups, key=lambda value: (rank.get(value, len(rank)), str(value)))
    out = []
    for key in keys:
        item = summary(groups[key])
        item[field] = key
        if field == "planType":
            labels = sorted({str(r.get("planLabel") or "") for r in groups[key]})
            item["planLabel"] = labels[0] if len(labels) == 1 else " / ".join(labels)
        out.append(item)
    return out


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
        if not isinstance(day, str) or not isinstance(rid, str) or not rid or day < PROSPECTIVE_START:
            raise ValueError("invalid prospective race identity/date")
        key = (day, rid)
        if key in ids:
            raise ValueError(f"duplicate prospective race: {key}")
        ids.add(key)
        if row.get("ruleVersion") != SOURCE_VERSION:
            raise ValueError("mixed portfolio rule versions")
        prospective.append(row)

    prospective.sort(key=race_key)
    observed = len(prospective)
    cohort = prospective[:TARGET_RACES]
    overall = summary(cohort)

    if observed == 0:
        review_status = "waiting_for_prospective_start"
    elif observed < TARGET_RACES:
        review_status = "collecting"
    elif overall["resultsPendingSelectedRaces"] > 0:
        review_status = "target_reached_results_pending"
    else:
        review_status = "ready_for_short_term_review"

    skipped = [r for r in cohort if not is_selected(r)]
    skip_reasons = Counter(str(r.get("reason") or "理由不明") for r in skipped)
    race_days = sorted({str(r.get("raceDate")) for r in cohort})

    return {
        "schemaVersion": 1,
        "version": REVIEW_VERSION,
        "sourceVersion": SOURCE_VERSION,
        "status": "research_evaluation_only",
        "reviewStatus": review_status,
        "generatedAtJST": now.astimezone(JST).isoformat(timespec="seconds"),
        "prospectiveStartDate": PROSPECTIVE_START,
        "targetRaces": TARGET_RACES,
        "racesObserved": observed,
        "reviewWindowRaces": len(cohort),
        "remainingTo100": max(0, TARGET_RACES - observed),
        "progressPct": round(min(1.0, observed / TARGET_RACES) * 100.0, 1),
        "raceDaysInReviewWindow": len(race_days),
        "reviewWindowStartDate": race_days[0] if race_days else None,
        "reviewWindowEndDate": race_days[-1] if len(cohort) == TARGET_RACES else None,
        "first100CohortLockedByRule": True,
        "overall": overall,
        "planMix": breakdown(
            cohort,
            "planType",
            ["win_single", "win_plus_wide", "wide_focus", "wide_plus_trio", "skip"],
        ),
        "chaosBreakdown": breakdown(cohort, "chaosLevel", [1, 2, 3, 4, 5]),
        "battleBreakdown": breakdown(cohort, "battleGrade", ["S", "A", "B", "C", "見送り"]),
        "skipReasons": [
            {"reason": reason, "races": count, "shareOfSkippedPct": pct(count, len(skipped))}
            for reason, count in sorted(skip_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        "qualityChecks": {
            "sourceRuleVersionMatches": True,
            "prospectiveStartMatches": True,
            "duplicateRaceCount": 0,
            "selectedPendingSettlement": overall["resultsPendingSelectedRaces"],
            "cohortExcludesDevelopmentBackfill": True,
            "laterRacesExcludedAfterFirst100": max(0, observed - TARGET_RACES),
        },
        "interpretationPolicy": {
            "shortTermPurpose": "100Rは合否判定ではなく、動作異常・極端な失敗・参加率・見送り率・的中・利益・トリガミ・プラン構成を点検する。",
            "noThresholdTuning": "100Rの結果を見てportfolio-shadow-v1の閾値を変更しない。改善案はv2 shadowとして分離する。",
            "roiWarning": "回収指数は標本が小さい間は振れが大きいため、単独で採否を決めない。",
            "nextMilestone": "300Rで候補ロジックの絞り込み材料とし、小さい予測優位の本格評価は1000R前後以上を想定する。",
        },
    }


def main() -> None:
    root = Path(os.environ.get("KEIBAAI_ROOT", Path(__file__).resolve().parent.parent))
    source = read_json(root / "PortfolioResearchData.json")
    review = build(source)
    write_json(root / "PortfolioReview100.json", review)
    newest = review.get("reviewWindowEndDate") or review.get("reviewWindowStartDate")
    if isinstance(newest, str):
        write_json(root / "reports" / newest.replace("-", "") / "PortfolioReview100.json", review)
    print(json.dumps({
        "reviewStatus": review["reviewStatus"],
        "racesObserved": review["racesObserved"],
        "reviewWindowRaces": review["reviewWindowRaces"],
        "remainingTo100": review["remainingTo100"],
        "overall": review["overall"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
