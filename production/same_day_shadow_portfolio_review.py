"""Cumulative prospective review for Same-Day Track Bias Shadow V2.1.

Governance rules:
- 2026-10-31 is a development/tuning baseline and is excluded.
- Prospective evaluation begins 2026-11-01 JST.
- Only Formal live_10min races already admitted by the daily evaluator are used.
- Challenger version is locked to same_day_track_bias_v2_1.
- The first review milestone is 100 eligible races. Reaching it does NOT promote
  the challenger automatically; it only changes reviewStatus to ready_for_review.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS_DIR = REPO / "reports"
OUTPUT_PATH = REPO / "SameDayShadowPortfolioReview.json"

PROSPECTIVE_START_DATE = "2026-11-01"
DEVELOPMENT_BASELINE_DATE = "2026-10-31"
CHALLENGER_VERSION = "same_day_track_bias_v2_1"
TARGET_RACES = 100


def _load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pct(num: int, den: int):
    return round(num / den * 100.0, 2) if den else None


def _mean(values: list[float]):
    return round(sum(values) / len(values), 4) if values else None


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    f1 = sum(bool(r.get("formalTop1Hit")) for r in rows)
    s1 = sum(bool(r.get("shadowTop1Hit")) for r in rows)
    f3 = sum(bool(r.get("formalTop3Hit")) for r in rows)
    s3 = sum(bool(r.get("shadowTop3Hit")) for r in rows)
    formal_ranks = [float(r["formalWinnerRank"]) for r in rows]
    shadow_ranks = [float(r["shadowWinnerRank"]) for r in rows]
    improved = sum(float(r["shadowWinnerRank"]) < float(r["formalWinnerRank"]) for r in rows)
    worsened = sum(float(r["shadowWinnerRank"]) > float(r["formalWinnerRank"]) for r in rows)
    unchanged = n - improved - worsened
    changed = improved + worsened
    return {
        "races": n,
        "formalTop1Hits": f1,
        "shadowTop1Hits": s1,
        "formalTop1RatePct": _pct(f1, n),
        "shadowTop1RatePct": _pct(s1, n),
        "top1HitDeltaShadowMinusFormalPctPoints": (
            round(_pct(s1, n) - _pct(f1, n), 2) if n else None
        ),
        "formalTop3Hits": f3,
        "shadowTop3Hits": s3,
        "formalTop3RatePct": _pct(f3, n),
        "shadowTop3RatePct": _pct(s3, n),
        "top3HitDeltaShadowMinusFormalPctPoints": (
            round(_pct(s3, n) - _pct(f3, n), 2) if n else None
        ),
        "formalMeanWinnerRank": _mean(formal_ranks),
        "shadowMeanWinnerRank": _mean(shadow_ranks),
        "meanWinnerRankDeltaShadowMinusFormal": (
            round(_mean(shadow_ranks) - _mean(formal_ranks), 4) if n else None
        ),
        "shadowImprovedWinnerRank": improved,
        "shadowWorsenedWinnerRank": worsened,
        "shadowUnchangedWinnerRank": unchanged,
        "rankChangedRaces": changed,
        "rankChangedRatePct": _pct(changed, n),
        "netImprovementCount": improved - worsened,
    }


def _evidence_bucket(row: dict) -> str:
    n = int(row.get("completedPriorSameSurfaceRaces") or 0)
    if n <= 3:
        return "1-3_prior_races"
    if n <= 5:
        return "4-5_prior_races"
    return "6plus_prior_races"


def _gate_bucket(row: dict) -> str:
    d = bool(row.get("drawGateEnabled"))
    s = bool(row.get("styleGateEnabled"))
    if d and s:
        return "draw_and_style"
    if d:
        return "draw_only"
    if s:
        return "style_only"
    return "neither"


def collect_daily_rows(reports_dir: Path = REPORTS_DIR) -> tuple[list[dict], dict]:
    accepted: dict[tuple[str, str], dict] = {}
    excluded = defaultdict(int)
    days_seen = set()

    for path in sorted(reports_dir.glob("????????/SameDayShadowEvaluation.json")):
        report = _load_json(path)
        if not isinstance(report, dict):
            excluded["unreadable_report"] += 1
            continue
        race_date = str(report.get("raceDate") or "")
        if not race_date:
            excluded["missing_race_date"] += 1
            continue
        if race_date < PROSPECTIVE_START_DATE:
            excluded["pre_prospective_development_date"] += len(report.get("races") or [])
            continue
        days_seen.add(race_date)

        for row in report.get("races") or []:
            if str(row.get("challengerVersion") or "") != CHALLENGER_VERSION:
                excluded["wrong_challenger_version"] += 1
                continue
            rid = str(row.get("raceID") or "").zfill(12)
            if not rid.strip("0"):
                excluded["missing_race_id"] += 1
                continue
            key = (race_date, rid)
            enriched = dict(row)
            enriched["raceDate"] = race_date
            # Deterministic de-duplication. A later file for the same date/race
            # replaces the earlier copy, but never creates a second observation.
            accepted[key] = enriched

    rows = sorted(
        accepted.values(),
        key=lambda r: (
            str(r.get("raceDate") or ""),
            str(r.get("startTime") or ""),
            str(r.get("venue") or ""),
            int(r.get("raceNumber") or 0),
            str(r.get("raceID") or ""),
        ),
    )
    meta = {
        "raceDaysObserved": len(days_seen),
        "firstRaceDate": min(days_seen) if days_seen else None,
        "lastRaceDate": max(days_seen) if days_seen else None,
        "excluded": dict(sorted(excluded.items())),
    }
    return rows, meta


def build_review(rows: list[dict], meta: dict) -> dict:
    venue_surface = defaultdict(list)
    evidence = defaultdict(list)
    gates = defaultdict(list)
    dates = defaultdict(list)
    for row in rows:
        venue_surface[f"{row.get('venue')}|{row.get('surface')}"] .append(row)
        evidence[_evidence_bucket(row)].append(row)
        gates[_gate_bucket(row)].append(row)
        dates[str(row.get("raceDate"))].append(row)

    observed = len(rows)
    review_status = "ready_for_review" if observed >= TARGET_RACES else "collecting"
    generated = datetime.now(JST).isoformat(timespec="seconds")
    return {
        "schemaVersion": 1,
        "version": "same-day-shadow-portfolio-review-v1",
        "status": "research_evaluation_only",
        "reviewStatus": review_status,
        "generatedAtJST": generated,
        "challengerVersionLocked": CHALLENGER_VERSION,
        "developmentBaselineDateExcluded": DEVELOPMENT_BASELINE_DATE,
        "prospectiveStartDate": PROSPECTIVE_START_DATE,
        "targetRaces": TARGET_RACES,
        "racesObserved": observed,
        "remainingTo100": max(0, TARGET_RACES - observed),
        "progressPct": round(min(observed, TARGET_RACES) / TARGET_RACES * 100.0, 1),
        "raceDaysObserved": int(meta.get("raceDaysObserved") or 0),
        "reviewWindowStartDate": meta.get("firstRaceDate"),
        "reviewWindowEndDate": meta.get("lastRaceDate"),
        "qualityChecks": {
            "formal10minEvidenceRequired": True,
            "prospectiveOnly": True,
            "targetResultLeakageExcluded": True,
            "challengerVersionLocked": True,
            "developmentBaselineExcluded": True,
            "automaticPromotionAllowed": False,
        },
        "interpretation": (
            "100 races is a first review milestone, not an automatic promotion rule. "
            "Formal A remains unchanged until a separate review of prospective results."
        ),
        "overall": _summary(rows),
        "byVenueSurface": {
            key: _summary(value) for key, value in sorted(venue_surface.items())
        },
        "byEvidenceCount": {
            key: _summary(value) for key, value in sorted(evidence.items())
        },
        "byAdjustmentGate": {
            key: _summary(value) for key, value in sorted(gates.items())
        },
        "byDate": {
            key: _summary(value) for key, value in sorted(dates.items())
        },
        "excluded": meta.get("excluded") or {},
        "races": rows,
    }


def main() -> None:
    rows, meta = collect_daily_rows(REPORTS_DIR)
    review = build_review(rows, meta)
    OUTPUT_PATH.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Same-Day V2.1 prospective review: races={review['racesObserved']} "
        f"remaining={review['remainingTo100']} status={review['reviewStatus']}"
    )
    print(json.dumps(review["overall"], ensure_ascii=False))


if __name__ == "__main__":
    main()
