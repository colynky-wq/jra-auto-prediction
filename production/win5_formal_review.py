"""Formal prospective review for frozen five-race WIN5 research sessions.

One observation is one immutable five-race WIN5 session captured before the common
WIN5 deadline. This review evaluates probability quality only for sessions whose
selection inputs were actually frozen in advance. It deliberately does not reconstruct
or backfill betting ROI because the historical frozen sessions did not preregister a
purchase-selection/budget rule.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import win5_payouts as payouts
import win5_research as win5


VERSION = "win5-formal-review-v1-20261031"
SOURCE_VERSION = "win5-factorized-win-v1-20261015"
PROSPECTIVE_START = "2026-10-18"
VOLATILE_TOP_LEVEL_FIELDS = {"generatedAtJST"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None


def pct(n: int, d: int) -> float | None:
    return round(n / d * 100.0, 2) if d else None


def session_day_from_path(path: Path) -> str:
    value = path.parent.name
    if len(value) != 8 or not value.isdigit():
        raise ValueError("invalid WIN5 report directory")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def load_saved_payout(root: Path, record: dict[str, Any], score: dict[str, Any]) -> dict[str, Any] | None:
    day = record["plan"]["raceDate"]
    path = root / "reports" / day.replace("-", "") / "win5_payouts_v1" / "session.json"
    if not path.exists():
        return None
    wrapper = read_json(path)
    fingerprint = wrapper.get("fingerprint")
    if not isinstance(fingerprint, str):
        raise ValueError("WIN5 payout fingerprint missing")
    unsigned = dict(wrapper)
    unsigned.pop("fingerprint", None)
    if payouts.digest(unsigned) != fingerprint:
        raise ValueError("WIN5 payout fingerprint mismatch")
    if wrapper.get("recordFingerprint") != record.get("recordFingerprint"):
        raise ValueError("WIN5 payout record fingerprint mismatch")
    document = wrapper.get("payout")
    if not isinstance(document, dict):
        raise ValueError("WIN5 payout document missing")
    payouts.verify_winners(record, score, document)
    return document


def session_row(root: Path, journal_path: Path) -> dict[str, Any]:
    day = session_day_from_path(journal_path)
    journal = read_json(journal_path)
    if journal.get("raceDate") != day:
        raise ValueError("WIN5 journal path/date mismatch")
    if journal.get("version") != SOURCE_VERSION:
        raise ValueError("WIN5 journal version mismatch")
    if journal.get("hasTarget") is not True:
        raise ValueError("WIN5 formal review requires an official target session")

    frozen_path = journal_path.parent / "win5_v1" / "session.json"
    if not frozen_path.exists():
        return {
            "raceDate": day,
            "status": "missing_frozen_session",
            "eligible": False,
            "reason": str(journal.get("status") or "missing_frozen_session"),
        }

    try:
        record = win5.validate(read_json(frozen_path))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {
            "raceDate": day,
            "status": "invalid_frozen_session",
            "eligible": False,
            "reason": type(error).__name__,
        }

    if record.get("status") != "ready":
        return {
            "raceDate": day,
            "status": "held_frozen_session",
            "eligible": False,
            "reason": str(record.get("reason") or "held"),
            "recordedAtJST": record.get("recordedAtJST"),
        }

    result_path = journal_path.parent / "ResultData.json"
    if not result_path.exists():
        return {
            "raceDate": day,
            "status": "results_pending",
            "eligible": True,
            "recordedAtJST": record.get("recordedAtJST"),
            "deadlineJST": record.get("plan", {}).get("deadlineJST"),
        }

    try:
        results = win5.index_unique(win5.load_rows(result_path))
        score = win5.probability_score(record, results)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {
            "raceDate": day,
            "status": "result_validation_failed",
            "eligible": True,
            "recordedAtJST": record.get("recordedAtJST"),
            "reason": type(error).__name__,
        }

    if score.get("status") != "probability_matched":
        return {
            "raceDate": day,
            "status": str(score.get("status") or "results_pending"),
            "eligible": True,
            "recordedAtJST": record.get("recordedAtJST"),
            "deadlineJST": record.get("plan", {}).get("deadlineJST"),
        }

    row = {
        "raceDate": day,
        "status": "probability_matched",
        "eligible": True,
        "recordedAtJST": record.get("recordedAtJST"),
        "deadlineJST": record.get("plan", {}).get("deadlineJST"),
        "winningOutcome": score.get("winningOutcome"),
        "aiActualOutcomeProbability": score.get("aiActualOutcomeProbability"),
        "marketActualOutcomeProbability": score.get("marketActualOutcomeProbability"),
        "aiNegativeLogProbability": score.get("aiNegativeLogProbability"),
        "marketNegativeLogProbability": score.get("marketNegativeLogProbability"),
        "aiTopOneAllFiveHit": score.get("aiTopOneAllFiveHit"),
        "marketTopOneAllFiveHit": score.get("marketTopOneAllFiveHit"),
        "aiTopOneLegHits": score.get("aiTopOneLegHits"),
        "marketTopOneLegHits": score.get("marketTopOneLegHits"),
    }
    try:
        payout = load_saved_payout(root, record, score)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        row.update(status="payout_validation_failed", payoutVerified=False, payoutError=type(error).__name__)
    else:
        if payout is None:
            row.update(status="payout_pending", payoutVerified=False)
        else:
            row.update(
                status="payout_verified",
                payoutVerified=True,
                publishedPayoutPer100Yen=payout.get("payoutPer100Yen"),
                winningTicketCount=payout.get("winningTicketCount"),
                payoutStatus=payout.get("payoutStatus"),
            )
    return row


def build(root: Path, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root)
    rows: list[dict[str, Any]] = []
    for journal_path in sorted((root / "reports").glob("*/win5_status.json")):
        try:
            day = session_day_from_path(journal_path)
        except ValueError:
            continue
        if day < PROSPECTIVE_START:
            continue
        try:
            journal = read_json(journal_path)
        except (OSError, json.JSONDecodeError):
            rows.append({"raceDate": day, "status": "invalid_journal", "eligible": False, "reason": "journal_unreadable"})
            continue
        if journal.get("hasTarget") is not True:
            continue
        try:
            rows.append(session_row(root, journal_path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            rows.append({"raceDate": day, "status": "invalid_journal", "eligible": False, "reason": type(error).__name__})

    rows.sort(key=lambda row: str(row.get("raceDate") or ""))
    eligible = [row for row in rows if row.get("eligible") is True]
    matched = [row for row in eligible if row.get("aiNegativeLogProbability") is not None]
    payout_verified = [row for row in matched if row.get("payoutVerified") is True]
    excluded = [row for row in rows if row.get("eligible") is not True]
    pending = [row for row in eligible if row.get("status") not in {"payout_verified"}]

    ai_nll = [float(row["aiNegativeLogProbability"]) for row in matched]
    market_nll = [float(row["marketNegativeLogProbability"]) for row in matched]
    ai_leg_hits = sum(int(row.get("aiTopOneLegHits") or 0) for row in matched)
    market_leg_hits = sum(int(row.get("marketTopOneLegHits") or 0) for row in matched)
    total_legs = 5 * len(matched)

    generated = (now or datetime.now(win5.JST)).astimezone(win5.JST).isoformat(timespec="seconds")
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "status": "research_evaluation_only",
        "generatedAtJST": generated,
        "sourceVersion": SOURCE_VERSION,
        "prospectiveStartDate": PROSPECTIVE_START,
        "observationUnit": "one_frozen_five_race_win5_session",
        "sessionsObserved": len(rows),
        "sessionsEligible": len(eligible),
        "sessionsResultMatched": len(matched),
        "sessionsPayoutVerified": len(payout_verified),
        "sessionsPending": len(pending),
        "sessionsExcluded": len(excluded),
        "probabilityEvaluation": {
            "sessionsEvaluated": len(matched),
            "totalLegsEvaluated": total_legs,
            "aiTopOneAllFiveHits": sum(row.get("aiTopOneAllFiveHit") is True for row in matched),
            "marketTopOneAllFiveHits": sum(row.get("marketTopOneAllFiveHit") is True for row in matched),
            "aiTopOneLegHits": ai_leg_hits,
            "marketTopOneLegHits": market_leg_hits,
            "aiTopOneLegHitRatePct": pct(ai_leg_hits, total_legs),
            "marketTopOneLegHitRatePct": pct(market_leg_hits, total_legs),
            "meanAINegativeLogProbability": mean(ai_nll),
            "meanMarketNegativeLogProbability": mean(market_nll),
            "aiLowerNLLSessions": sum(float(row["aiNegativeLogProbability"]) < float(row["marketNegativeLogProbability"]) for row in matched),
            "marketLowerNLLSessions": sum(float(row["marketNegativeLogProbability"]) < float(row["aiNegativeLogProbability"]) for row in matched),
        },
        "bettingEvaluation": {
            "status": "not_started_no_frozen_selection_rule",
            "formalROIAvailable": False,
            "historicalReferenceBacktestExcluded": True,
            "selectionRuleFrozenForTheseSessions": False,
            "note": "既存の凍結セッションは5レースの確率予測を事前固定しているが、購入点・予算ルールは固定していない。結果を見た後に買い目を復元せず、正式ROIは将来の購入ルール凍結後から別コホートで評価する。",
        },
        "sessions": rows,
    }


def material(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for field in VOLATILE_TOP_LEVEL_FIELDS:
        result.pop(field, None)
    return result


def write_if_material_changed(root: Path) -> tuple[bool, dict[str, Any]]:
    root = Path(root)
    output = root / "Win5FormalReview.json"
    candidate = build(root)
    if output.exists():
        try:
            current = read_json(output)
        except (OSError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict) and material(current) == material(candidate):
            return False, current
    write_json(output, candidate)
    return True, candidate


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    changed, review = write_if_material_changed(root)
    print(
        "WIN5_FORMAL_REVIEW",
        "changed" if changed else "unchanged",
        "observed", review.get("sessionsObserved"),
        "eligible", review.get("sessionsEligible"),
        "matched", review.get("sessionsResultMatched"),
        "payouts", review.get("sessionsPayoutVerified"),
    )


if __name__ == "__main__":
    main()
