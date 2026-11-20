"""Evaluate only prospectively frozen market/AI shrinkage shadows.

Read-only study. A past audit observation without a first-T10 shadow cannot
be reconstructed and is never counted as a candidate's prospective result.
No odds requests, model training, bet selection, or report publishing here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
import math
from pathlib import Path

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium
from market_shrink_shadow import (AI_WEIGHTS, FIELDS, JST, VERSION,
                                  canonical, fingerprint)

SERIES = tuple(AI_WEIGHTS)


def ensure(ok, reason):
    if not ok:
        raise ValueError("MARKET_SHRINK_REVIEW_BLOCKED: " + reason)


def expected_window(race: dict, shadow: dict) -> None:
    day = str(race["raceDate"])
    post = datetime.fromisoformat(day + "T" + str(race["startTime"])).replace(tzinfo=JST)
    captured = datetime.fromisoformat(str(shadow["capturedAtJST"]))
    recorded = datetime.fromisoformat(str(shadow["recordedAtJST"]))
    ensure(captured.utcoffset() == timedelta(hours=9) and
           recorded.utcoffset() == timedelta(hours=9),
           "source and shadow must use JST")
    ensure(8 <= (post - captured).total_seconds() / 60 <= 12,
           "source prediction was not formal T-10")
    ensure(captured <= recorded <= post - timedelta(minutes=8),
           "shadow not prospectively frozen")
    ensure(shadow["capturedAtJST"] == race["capturedAtJST"],
           "frozen timestamp mismatch")


def _read_shadow(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    ensure(value.get("version") == VERSION and
           value.get("status") == "prospective_shadow_only",
           "unknown shadow version or status")
    ensure(value.get("recordFingerprint") == fingerprint({
        k: v for k, v in value.items() if k != "recordFingerprint"
    }), "sha256 mismatch")
    ensure(value.get("weightsOnAI") == AI_WEIGHTS, "changed challenger weights")
    ensure(value.get("autoBetEnabled") is False and
           value.get("formalRecommendationsEnabled") is False and
           value.get("researchOnly") is True, "shadow must not activate bets")
    return value


def _event_scores(shadow, positions, rid):
    horses = shadow["horses"]
    numbers = [h["number"] for h in horses]
    ensure(all(type(n) is int for n in numbers) and len(set(numbers)) == len(numbers),
           "duplicate or invalid horse numbers")
    results = {}
    for event, (_, _market_field) in FIELDS.items():
        places = {"win": 1, "top2": 2, "top3": 3}[event]
        podium = official_podium(positions, numbers, places, rid)
        if podium is None:
            results[event] = None
            continue
        scores = {}
        for name in SERIES:
            probs = [h["probabilitiesPct"][event][name] / 100.0 for h in horses]
            ensure(all(type(p) is float and math.isfinite(p) and 0 <= p <= 1
                       for p in probs), "invalid shadow probabilities")
            scores[name] = sum((p - int(number in podium)) ** 2
                               for p, number in zip(probs, numbers)) / len(horses)
        results[event] = scores
    return results


def evaluate(root: Path, audit: dict, load_bundle=capture.load) -> dict:
    root = Path(root)
    aligned = audit.get("aligned") or {}
    ensure(audit.get("projectionState") == "verified", "audit not verified")
    ensure(aligned.get("countsAreCurrent") is True, "audit counts not current")
    ensure(audit.get("cohortLockRecoveryRequired") is not True, "cohort recovery")
    ensure(audit.get("generatedAtJST"), "missing audit provenance")
    audit_rows = audit.get("races")
    ensure(isinstance(audit_rows, list) and
           len(audit_rows) == aligned.get("racesObserved"), "audit summary mismatch")

    keyed = {(r["raceDate"], r["raceID"]): r for r in audit_rows}
    ensure(len(keyed) == len(audit_rows), "duplicate audit race identity")
    excluded = {(r["raceDate"], r["raceID"]) for r in
                (audit.get("quality", {}).get("resultExclusions") or [])}
    grouped = defaultdict(list)
    counts = {"frozenSnapshots": 0, "pairedAuditedRaces": 0,
              "notAuditedOrNotSettled": 0, "resultExcluded": 0,
              "raceEventsUnavailable": 0}
    firsts = set()
    for path in sorted((root / "reports").glob("*/market_shrink_shadow_v1/*.json")):
        shadow = _read_shadow(path)
        day, rid = shadow["raceDate"], shadow["raceID"]
        identity = (day, rid)
        ensure(identity not in firsts, "duplicate shadow identity")
        firsts.add(identity)
        ensure(path == root / "reports" / day.replace("-", "") /
               "market_shrink_shadow_v1" / (rid + ".json"), "shadow file path mismatch")
        counts["frozenSnapshots"] += 1
        row = keyed.get(identity)
        if row is None or row.get("resultState") != "matched":
            counts["notAuditedOrNotSettled"] += 1
            continue
        if identity in excluded:
            counts["resultExcluded"] += 1
            continue
        source = load_bundle(root, day, rid)
        race = source["prediction"]
        ensure(source["fingerprint"] == row["inputFingerprint"],
               "audit / formal bundle input mismatch")
        ensure(race.get("predictionTiming") == "live_10min" and
               shadow.get("sourcePredictionSha256") == fingerprint(race),
               "frozen source prediction hash mismatch")
        ensure(shadow.get("predictionTiming") == "live_10min" and
               shadow["startTime"] == race["startTime"], "race/post identity mismatch")
        expected_window(race, shadow)
        shadow_horses = shadow["horses"]
        original_horses = race["horses"]
        ensure([h["number"] for h in shadow_horses] ==
               [h["number"] for h in original_horses], "entrants mismatch")

        # Reconstruct only arithmetic checks using the *original* frozen
        # input. No historical candidate records are generated or edited.
        for saved, original in zip(shadow_horses, original_horses):
            for event, (ai_field, market_field) in FIELDS.items():
                ai = float(original[ai_field])
                market = float(original[market_field])
                for name, weight in AI_WEIGHTS.items():
                    value = saved["probabilitiesPct"][event][name]
                    expected = round(ai * weight + market * (1 - weight), 6)
                    ensure(type(value) in (int, float) and
                           math.isclose(value, expected, rel_tol=0, abs_tol=1e-6),
                           "shadow/source arithmetic mismatch")
        actual = row.get("actualFinish")
        ensure(isinstance(actual, dict), "missing audited results")
        per_event = _event_scores(shadow, actual, rid)
        counts["pairedAuditedRaces"] += 1
        for event, scores in per_event.items():
            if scores is None:
                counts["raceEventsUnavailable"] += 1
                continue
            grouped[event].append(scores)

    by_event = {}
    for event in FIELDS:
        rows = grouped[event]
        means = {name: round(sum(row[name] for row in rows) / len(rows), 8)
                 if rows else None for name in SERIES}
        by_event[event] = {
            "pairedRaces": len(rows),
            "brierByModel": means,
            "brierDeltaVsMarket": {
                name: (round(means[name] - means["marketOnly"], 8)
                       if rows else None) for name in SERIES if name != "marketOnly"
            },
        }
    n = counts["pairedAuditedRaces"]
    return {
        "schemaVersion": 1, "version": "market-shrink-forward-review-v1-20261121",
        "status": ("insufficient_prospective_evidence" if n < 100 else
                   "prospective_descriptive_only"),
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "qualityIssueCount": len((audit.get("quality") or {}).get("issues") or []),
        "registeredFixedWeightsOnAI": AI_WEIGHTS,
        "counts": counts, "byEvent": by_event,
        "formalRecommendationsEnabled": False, "autoBetEnabled": False,
        "limitations": [
            "Only separately first-frozen prospective T-10 shadow records enter this series.",
            "An old audit or missing shadow must never be repaired from a later result.",
            "Same-race horses are correlated; do not treat horse samples as independent.",
            "100 shadow races is a descriptive minimum, NOT approval or proof of profitability.",
            "No coefficient selection, retraining, betting or beta activation.",
        ],
    }


def main():
    root = Path(__file__).resolve().parent.parent
    audit = json.loads((root / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    report = evaluate(root, audit)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
