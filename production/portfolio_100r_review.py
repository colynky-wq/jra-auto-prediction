"""Build the preregistered first-100-race review for portfolio shadow v1.

This module is evaluation-only.  It never changes predictions, candidate selection,
thresholds, or stakes.  The review cohort is the first 100 prospective races in
chronological order.  Once later races arrive, that first-100 cohort stays fixed.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

SOURCE_VERSION = "portfolio-shadow-v1-20261019"
REVIEW_VERSION = "portfolio-100r-review-v1"
PROSPECTIVE_START = "2026-10-24"
TARGET_RACES = 100


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def pct(n: int, d: int) -> float | None:
    return round(n * 100.0 / d, 2) if d else None


def ratio(n: int, d: int) -> float | None:
    return round(n / d, 6) if d else None


def race_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("raceDate", "")), str(row.get("startTime") or ""), str(row.get("raceID", "")))


def validate_source(value: dict[str, Any]) -> None:
    if value.get("schemaVersion") != 1:
        raise ValueError("unsupported portfolio schema")
    if value.get("version") != SOURCE_VERSION:
        raise ValueError("portfolio rule version changed; create a new review version")
    if value.get("status") != "research_display_only":
        raise ValueError("portfolio source is not research-only")
    if value.get("formalRecommendationsEnabled") is not False:
        raise ValueError("formal recommendation flag must stay false")
    if value.get("autoBetEnabled") is not False or value.get("stakeSizingEnabled") is not False:
        raise ValueError("automatic betting/stake sizing must stay disabled")
    if value.get("prospectiveStartDate") != PROSPECTIVE_START:
        raise ValueError("prospective start date changed")


def selected(row: dict[str, Any]) -> bool:
    return row.get("planType") != "skip"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected_rows = [r for r in rows if selected(r)]
    settled = [r for r in selected_rows if r.get("settled") is True]
    pending = [r for r in selected_rows if r.get("settled") is not True]

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
    skipped = len(rows) - len(selected_rows)

    return {
        "races": len(rows),
        "selectedRaces": len(selected_rows),
        "skippedRaces": skipped,
        "participationRatePct": pct(len(selected_rows), len(rows)),
        "skipRatePct": pct(skipped, len(rows)),
        "settledSelectedRaces": len(settled),
        "pendingSelectedRaces": len(pending),
        "hitAnyRaces": hit,
        "hitRatePctOfSettledSelected": pct(hit, len(settled)),
        "profitableRaces": profitable,
        "profitableRatePctOfSettledSelected": pct(profitable, len(settled)),
        "torigamiRaces": torigami,
        "torigamiRatePctOfSettledSelected": pct(torigami, len(settled)),
        "stakeYen": stake,
        "returnYen": returned,
        "profitYen": returned - stake,
        "returnIndex": ratio(returned, stake),
    }


def breakdown(rows: list[dict[str, Any]], field: str, order: list[Any] | None = None) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get(field)
        groups.setdefault(key, []).append(row)
    keys = list(groups)
    if order is not None:
        rank = {v: i for i, v in enumerate(order)}
        keys.sort(key=lambda x: (rank.get(x, len(rank)), str(x)))
    else:
        keys.sort(key=lambda x: str(x))
    out = []
    for key in keys:
        item = summarize(groups[key])
        item[field] = key
        if field == "planType":
            labels = sorted({str(r.get("planLabel", "")) for r in groups[key]})
            item["planLabel"] = labels[0] if len(labels) == 1 else " / ".join(labels)
        out.append(item)
    return out


def build(value: dict[str, Any]) -> dict[str, Any]:
    validate_source(value)
    all_rows = value.get("races")
    if not isinstance(all_rows, list):
        raise ValueError("portfolio races must be a list")

    prospective: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in all_rows:
        if not isinstance(row, dict):
            raise ValueError("invalid portfolio race row")
        if row.get("scope") != "prospective":
            continue
        if row.get("ruleVersion") != SOURCE_VERSION:
            raise ValueError("prospective row uses a different rule version")
        day = row.get("raceDate")
        rid = row.get("raceID")
        if not isinstance(day, str) or day < PROSPECTIVE_START or not isinstance(rid, str) or not rid:
            raise ValueError("invalid prospective race identity/date")
        key = (day, rid)
        if key in seen:
            raise ValueError(f"duplicate prospective race: {key}")
        seen.add(key)
        prospective.append(row)

    prospective.sort(key=race_key)
    cohort = prospective[:TARGET_RACES]
    stats = summarize(cohort)
    observed = len(prospective)
    reached = observed >= TARGET_RACES
    all_selected_settled = stats["pendingSelectedRaces"] == 0
    if not reached:
        status = "collecting"
    elif not all_selected_settled:
        status = "awaiting_settlement"
    else:
        status = "first_review_ready"

    skip_reasons = Counter(
        str(r.get("reason") or "理由未記録") for r in cohort if r.get("planType") == "skip"
    )
    review_start = cohort[0].get("raceDate") if cohort else None
    review_end = cohort[-1].get("raceDate") if len(cohort) == TARGET_RACES else None

    return {
        "schemaVersion": 1,
        "version": REVIEW_VERSION,
        "sourceVersion": SOURCE_VERSION,
        "status": status,
        "researchOnly": True,
        "formalRecommendationDecision": False,
        "generatedAtJST": value.get("generatedAtJST"),
        "prospectiveStartDate": PROSPECTIVE_START,
        "targetRaces": TARGET_RACES,
        "racesObserved": observed,
        "reviewWindowRaces": len(cohort),
        "remainingTo100": max(0, TARGET_RACES - observed),
        "progressPct": round(min(observed, TARGET_RACES) * 100.0 / TARGET_RACES, 1),
        "reviewWindowStartDate": review_start,
        "reviewWindowEndDate": review_end,
        "first100CohortLockedByRule": True,
        "reviewPolicy": (
            "最初の100 prospectiveレースだけを時系列で固定して集計する。"
            "100Rでは正式採用・閾値変更を行わず、動作異常、参加率、的中、利益、"
            "トリガミ、券種構成の初期点検だけを行う。"
        ),
        "overall": stats,
        "planBreakdown": breakdown(
            cohort,
            "planType",
            ["win_single", "win_plus_wide", "wide_focus", "wide_plus_trio", "skip"],
        ),
        "chaosBreakdown": breakdown(cohort, "chaosLevel", [1, 2, 3, 4, 5]),
        "battleBreakdown": breakdown(cohort, "battleGrade", ["S", "A", "B", "C", "見送り"]),
        "skipReasons": [
            {"reason": reason, "races": count, "sharePctOfSkipped": pct(count, stats["skippedRaces"])}
            for reason, count in sorted(skip_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        "qualityChecks": {
            "sourceRuleVersionMatches": True,
            "prospectiveStartMatches": True,
            "duplicateRaceCount": 0,
            "selectedPendingSettlement": stats["pendingSelectedRaces"],
            "cohortDoesNotUseDevelopmentBackfill": True,
            "laterRacesExcludedAfterFirst100": max(0, observed - TARGET_RACES),
        },
        "interpretation": {
            "at100R": "初期レビューのみ。優位性や収益性の証明とは扱わない。",
            "at300R": "候補ロジックの絞り込み材料。正式採用判断にはまだ早い。",
            "smallEdgeEvaluation": "小さい予測優位の評価には1000R前後以上を想定し、別途市場対比を行う。",
        },
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    source = read_json(root / "PortfolioResearchData.json")
    value = build(source)
    write_json(root / "Portfolio100RReview.json", value)
    print(
        "PORTFOLIO_100R_REVIEW",
        value["status"],
        f"{value['reviewWindowRaces']}/{value['targetRaces']}",
        "selected",
        value["overall"]["selectedRaces"],
        "pending",
        value["overall"]["pendingSelectedRaces"],
    )


if __name__ == "__main__":
    main()
