"""Read-only audit of KeibaAI aligned T-10 odds and research-wide probabilities.

No network calls, changes to stored inputs, prediction changes, or betting actions.
The netkeiba officialDatetime field is treated as *unverified provenance*, not
as proof of when the odds values were actually last updated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

JST = timezone(timedelta(hours=9))
ALIGNED_FILE = "AlignedPortfolioReview.json"
DEFAULT_START = "2026-11-07"


def parse_jst(value):
    """Parse an ISO/JST timestamp without silently interpreting naive as UTC."""
    if value is None or not str(value).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def age_minutes(captured_at, source_at):
    captured, source = parse_jst(captured_at), parse_jst(source_at)
    if captured is None or source is None:
        return None
    return round((captured - source).total_seconds() / 60, 2)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def audit_snapshot(snapshot, win_record, *, age_alert_min=30):
    """Return anomaly labels and metrics; do not declare stale odds as a fact."""
    alerts = []
    prediction = win_record.get("prediction", {})
    horse_numbers = {int(h["number"]) for h in prediction.get("horses", [])}
    horse_popularity = {int(h["number"]): h.get("popularity") for h in prediction.get("horses", [])}
    favorite_pair_outliers = []
    race_id = str(prediction.get("raceID", ""))
    if str(snapshot.get("raceID", "")) != race_id:
        alerts.append("race_identity_mismatch")
    if snapshot.get("raceDate") != prediction.get("raceDate"):
        alerts.append("race_date_mismatch")
    if snapshot.get("startTime") != prediction.get("startTime"):
        alerts.append("start_time_mismatch")
    if snapshot.get("betType") != "ワイド" or snapshot.get("apiType") != 5:
        alerts.append("wrong_bet_type")
    if snapshot.get("frozenAtFirstSuccessfulCapture") is not True:
        alerts.append("not_frozen")
    if snapshot.get("apiStatus") not in {"middle", "result"}:
        alerts.append("unexpected_api_status")

    captured = parse_jst(snapshot.get("capturedAtJST"))
    start_time = snapshot.get("startTime")
    race_day = snapshot.get("raceDate")
    try:
        start = parse_jst(f"{race_day}T{start_time}:00")
    except (TypeError, ValueError):
        start = None
    before_post = (
        round((start - captured).total_seconds() / 60, 2)
        if start and captured else None
    )
    if before_post is None or not 8 <= before_post <= 12:
        alerts.append("capture_outside_formal_window")

    age = age_minutes(snapshot.get("capturedAtJST"), snapshot.get("officialDatetime"))
    if age is None:
        alerts.append("source_timestamp_unavailable")
    elif age < -1:
        alerts.append("source_timestamp_after_capture")
    elif age > age_alert_min:
        alerts.append("source_timestamp_age_alert")

    valid_pairs = set()
    for pair in snapshot.get("pairs", []):
        try:
            a, b = int(pair["number1"]), int(pair["number2"])
            low, high = float(pair["oddsLow"]), float(pair["oddsHigh"])
        except (ValueError, TypeError, KeyError):
            alerts.append("malformed_pair")
            continue
        if a >= b or a not in horse_numbers or b not in horse_numbers:
            alerts.append("pair_horse_mismatch")
        if (a, b) in valid_pairs:
            alerts.append("duplicate_pair")
        valid_pairs.add((a, b))
        if not (1 <= low <= high < float("inf")):
            alerts.append("invalid_odds_range")
        # Heuristic warning only: even the most popular pair can have unusual
        # odds. Do not treat this as proof of incorrectly mapped odds.
        if (horse_popularity.get(a) in {1, 2, 3}
                and horse_popularity.get(b) in {1, 2, 3}
                and low >= 15):
            alerts.append("favorite_pair_wide_price_alert")
            favorite_pair_outliers.append({
                "pair": f"{a:02d}{b:02d}", "oddsLow": low,
                "horseAPopularity": horse_popularity[a],
                "horseBPopularity": horse_popularity[b],
            })

    expected_pairs = {
        (a, b) for a in horse_numbers for b in horse_numbers if a < b
    }
    if valid_pairs != expected_pairs or len(snapshot.get("pairs", [])) != len(expected_pairs):
        alerts.append("incomplete_or_extra_pairs")
    if snapshot.get("pairCount") != len(snapshot.get("pairs", [])):
        alerts.append("pair_count_mismatch")

    return {
        "raceID": race_id,
        "raceDate": prediction.get("raceDate"),
        "sourceAgeMinutes": age,
        "minutesBeforePost": before_post,
        "pairCount": len(snapshot.get("pairs", [])),
        "favoritePairPriceOutliers": favorite_pair_outliers[:10],
        "alerts": sorted(set(alerts)),
        "sourceTimestampSemantics": "UNVERIFIED",
        "oddsStalenessProven": False,
    }


def audit_wide_references(aligned):
    """Analyze frozen reference bets, not a hypothetical executed portfolio."""
    bins = {
        "1.10-1.30": {"tickets": 0, "hits": 0, "expectedHits": 0.0, "stakeYen": 0, "payoutYen": 0},
        "1.30-1.50": {"tickets": 0, "hits": 0, "expectedHits": 0.0, "stakeYen": 0, "payoutYen": 0},
        "1.50+": {"tickets": 0, "hits": 0, "expectedHits": 0.0, "stakeYen": 0, "payoutYen": 0},
        "below_1.10": {"tickets": 0, "hits": 0, "expectedHits": 0.0, "stakeYen": 0, "payoutYen": 0},
    }
    examples = []
    audit_errors = []
    for race in aligned.get("races", []):
        if race.get("resultState") != "matched" or race.get("referenceSettled") is not True:
            continue
        finish = race.get("actualFinish", {})
        for bet in race.get("commonWideReference", []):
            p = float(bet.get("probabilityPct", 0)) / 100.0
            odds = float(bet.get("oddsLow", 0))
            ev = p * odds
            reported_ev = float(bet.get("expectedMultiplierLow", ev))
            if abs(ev - reported_ev) > 0.004:
                audit_errors.append({"raceID": race["raceID"], "type": "expected_value_inconsistent"})
            candidate_id = str(bet.get("candidateID", ""))
            if len(candidate_id) == 4 and candidate_id.isdigit():
                a, b = int(candidate_id[:2]), int(candidate_id[2:])
                known = str(a) in finish and str(b) in finish
                hit = known and int(finish[str(a)]) <= 3 and int(finish[str(b)]) <= 3
                if known and hit != bool(bet.get("hit")):
                    audit_errors.append({"raceID": race["raceID"], "type": "hit_result_mismatch"})
            else:
                audit_errors.append({"raceID": race["raceID"], "type": "invalid_candidate_id"})
            key = (
                "1.50+" if ev >= 1.5 else
                "1.30-1.50" if ev >= 1.3 else
                "1.10-1.30" if ev >= 1.1 else "below_1.10"
            )
            row = bins[key]
            row["tickets"] += 1
            row["hits"] += int(bool(bet.get("hit")))
            row["expectedHits"] += p
            row["stakeYen"] += int(bet.get("unitYen") or 0)
            row["payoutYen"] += int(bet.get("payoutYen") or 0)
            if ev >= 2.5:
                examples.append({
                    "raceID": race["raceID"], "raceDate": race["raceDate"],
                    "candidateID": candidate_id,
                    "probabilityPct": round(p * 100, 3),
                    "oddsLow": odds,
                    "expectedMultiplierLow": round(ev, 3),
                })
    for row in bins.values():
        row["expectedHits"] = round(row["expectedHits"], 4)
        row["roiPct"] = (
            round(100 * row["payoutYen"] / row["stakeYen"], 2)
            if row["stakeYen"] else None
        )
    return {"bins": bins, "highExpectationExamples": examples, "errors": audit_errors}


def compare_early_and_formal(early, formal):
    """Compare separately recorded observations without assuming a feed update."""
    if (early.get("raceID") != formal.get("raceID")
            or early.get("raceDate") != formal.get("raceDate")):
        return {"status": "identity_mismatch"}

    first = parse_jst(early.get("capturedAtJST"))
    last = parse_jst(formal.get("capturedAtJST"))
    if first is None or last is None:
        return {"status": "capture_time_unavailable"}
    elapsed = round((last - first).total_seconds() / 60.0, 2)
    if elapsed < -0.01:
        return {"status": "early_snapshot_after_formal", "elapsedMinutes": elapsed}

    earlier_pairs = {}
    later_pairs = {}
    for dest, snapshot in ((earlier_pairs, early), (later_pairs, formal)):
        for pair in snapshot.get("pairs", []):
            key = (pair.get("number1"), pair.get("number2"))
            dest[key] = (pair.get("oddsLow"), pair.get("oddsHigh"),
                         pair.get("popularity"))
    if earlier_pairs.keys() != later_pairs.keys():
        return {"status": "pair_set_mismatch", "elapsedMinutes": elapsed}

    changed = sum(earlier_pairs[k] != later_pairs[k] for k in earlier_pairs)
    if elapsed < 1.0:
        status = "same_or_near_capture"
    elif changed == 0:
        status = "unchanged_across_separate_captures"
    else:
        status = "changed_across_separate_captures"
    return {
        "status": status,
        "elapsedMinutes": elapsed,
        "pairsCompared": len(earlier_pairs),
        "changedPairs": changed,
        "sameSourceTimestamp": early.get("officialDatetime") == formal.get("officialDatetime"),
        "earlySourceTimestamp": early.get("officialDatetime"),
        "formalSourceTimestamp": formal.get("officialDatetime"),
    }


def audit_repository(root: Path, *, age_alert_min=30):
    """Read only the registered aligned cohort and its frozen inputs."""
    aligned = read_json(root / ALIGNED_FILE)
    if aligned.get("projectionState") != "verified" or aligned.get("aligned", {}).get("countsAreCurrent") is not True:
        raise ValueError("aligned source not verified/current; abort instead of trusting counts")

    rows = []
    observation_comparisons = []
    for race in aligned["races"]:
        day = str(race["raceDate"]).replace("-", "")
        race_id = str(race["raceID"])
        base = root / "reports" / day
        odds_file = base / "wide_odds_live10" / f"{race_id}.json"
        win_file = base / "all_bet_v1" / "win" / f"{race_id}.json"
        if not odds_file.is_file() or not win_file.is_file():
            rows.append({
                "raceID": race_id, "raceDate": race["raceDate"],
                "sourceAgeMinutes": None, "oddsStalenessProven": False,
                "alerts": ["input_file_missing"],
            })
            continue
        try:
            formal_snapshot = read_json(odds_file)
            rows.append(audit_snapshot(
                formal_snapshot, read_json(win_file),
                age_alert_min=age_alert_min,
            ))
            early_file = base / "wide_odds" / f"{race_id}.json"
            if early_file.is_file():
                comparison = compare_early_and_formal(read_json(early_file), formal_snapshot)
                observation_comparisons.append({
                    "raceID": race_id, "raceDate": race["raceDate"], **comparison,
                })
            else:
                observation_comparisons.append({
                    "raceID": race_id, "raceDate": race["raceDate"],
                    "status": "early_snapshot_unavailable",
                })
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as err:
            rows.append({
                "raceID": race_id, "raceDate": race["raceDate"],
                "sourceAgeMinutes": None, "oddsStalenessProven": False,
                "alerts": ["input_parse_error"], "error": str(err)[:250],
            })

    cnt = Counter(label for r in rows for label in r["alerts"])
    cmp_counts = Counter(r["status"] for r in observation_comparisons)
    separately_captured = [
        x for x in observation_comparisons if x.get("elapsedMinutes", 0) >= 1
    ]
    same_source_same_quotes = sum(
        x["status"] == "unchanged_across_separate_captures"
        and x.get("sameSourceTimestamp") is True
        for x in separately_captured
    )
    changed_source_changed_quotes = sum(
        x["status"] == "changed_across_separate_captures"
        and x.get("sameSourceTimestamp") is False
        for x in separately_captured
    )
    ages = [r["sourceAgeMinutes"] for r in rows if r.get("sourceAgeMinutes") is not None]
    return {
        "schemaVersion": 1,
        "auditMode": "read_only_research",
        "alignedGeneratedAtJST": aligned.get("generatedAtJST"),
        "alignedProjectionVerified": True,
        "racesAudited": len(rows),
        "sourceTimestampSemantics": "UNVERIFIED; metadata age is not proof of stale odds",
        "sourceAgeMinutes": {
            "count": len(ages),
            "min": min(ages) if ages else None,
            "mean": round(sum(ages) / len(ages), 2) if ages else None,
            "max": max(ages) if ages else None,
        },
        "alertCounts": dict(sorted(cnt.items())),
        "earlyVersusFormal": {
            "statusCounts": dict(sorted(cmp_counts.items())),
            "separatelyCapturedRaces": len(separately_captured),
            "sameSourceTimeAndUnchangedQuotes": same_source_same_quotes,
            "changedSourceTimeAndChangedQuotes": changed_source_changed_quotes,
            "examples": [
                x for x in observation_comparisons
                if x["status"] in {
                    "unchanged_across_separate_captures",
                    "changed_across_separate_captures",
                    "pair_set_mismatch",
                }
            ][:15],
        },
        "favoritePairPriceOutliers": [
            {"raceDate": r["raceDate"], "raceID": r["raceID"],
             "pairs": r["favoritePairPriceOutliers"]}
            for r in rows if r.get("favoritePairPriceOutliers")
        ],
        "flaggedExamples": [r for r in rows if r["alerts"]][:20],
        "wideReferenceResearch": audit_wide_references(aligned),
        "formalRecommendationsEnabled": False,
        "autobetEnabled": False,
        "mutatedFiles": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    parser.add_argument("--age-alert-min", type=float, default=30.0,
                        help="age of source timestamp that warrants investigation (not proof of stale odds)")
    parser.add_argument("--strict-structure", action="store_true",
                        help="nonzero exit only on structural anomalies, not unverified timestamp age")
    args = parser.parse_args()
    report = audit_repository(args.root, age_alert_min=args.age_alert_min)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    structural = {
        "race_identity_mismatch", "race_date_mismatch", "start_time_mismatch",
        "wrong_bet_type", "not_frozen", "unexpected_api_status",
        "capture_outside_formal_window", "source_timestamp_after_capture",
        "malformed_pair", "pair_horse_mismatch", "duplicate_pair",
        "invalid_odds_range", "incomplete_or_extra_pairs",
        "pair_count_mismatch", "input_file_missing", "input_parse_error",
    }
    if args.strict_structure and (
        any(k in report["alertCounts"] for k in structural)
        or report["wideReferenceResearch"]["errors"]
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
