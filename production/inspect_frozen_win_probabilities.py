#!/usr/bin/env python3
"""Offline horse-level probability diagnostics on strictly frozen T-10 evidence.

This is an observational report, not a new model, a trading signal, or beta
acceptance. An audit with outstanding capture problems produces a visibly
quality-limited report; failed or stale audit prerequisites produce NO report.
Never downloads odds, rebuilds predictions, or reconstructs missing records.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import formal_portfolio_capture as capture

PROBABILITIES = {
    "win": ("aiProbability", "marketProbability", 1),
    "top2": ("aiTop2Probability", "marketTop2Probability", 2),
    "top3": ("aiTop3Probability", "marketTop3Probability", 3),
}
BINS = ((0, 5, "0-5%"), (5, 10, "5-10%"), (10, 20, "10-20%"),
        (20, 40, "20-40%"), (40, 101, "40-100%"))


def check(condition, message):
    if not condition:
        raise ValueError("FROZEN_PROBABILITY_BLOCKED: " + message)


def prob(raw, key):
    check(type(raw) in (int, float) and math.isfinite(raw) and 0 <= raw <= 100,
          "invalid " + key)
    return float(raw) / 100


def bucket(p):
    for low, high, label in BINS:
        if low <= p * 100 < high:
            return label
    raise ValueError("invalid probability bin")


def summarize(samples):
    result = {}
    for source in ("ai", "market"):
        ss = [r for r in samples if r["source"] == source]
        if not ss:
            result[source] = None
            continue
        groups = defaultdict(list)
        for row in ss:
            groups[bucket(row["p"])].append(row)
        def stats(rows):
            n = len(rows)
            return {
                "horses": n,
                "expectedHits": round(sum(r["p"] for r in rows), 3),
                "observedHits": sum(r["y"] for r in rows),
                "meanPredictedPct": round(100 * sum(r["p"] for r in rows) / n, 3),
                "observedRatePct": round(100 * sum(r["y"] for r in rows) / n, 3),
                "brierScore": round(sum((r["p"] - r["y"]) ** 2 for r in rows) / n, 6),
            }
        result[source] = {
            **stats(ss),
            "reliabilityBins": {k: stats(groups[k]) for k in sorted(groups)},
        }
    return result


def build(root, audit, protocol, load_bundle=capture.load):
    check(isinstance(audit, dict) and isinstance(protocol, dict), "invalid inputs")
    aligned = audit.get("aligned") or {}
    quality = audit.get("quality") or {}
    check(audit.get("projectionState") == "verified", "projection not verified")
    check(aligned.get("countsAreCurrent") is True, "counts not current")
    check(audit.get("cohortLockRecoveryRequired") is not True, "cohort lock recovery")
    check(audit.get("protocolVersion") == protocol.get("version"), "protocol mismatch")
    check(protocol.get("policy", {}).get("noV1Changes") is True,
          "unfrozen protocol")
    rows = audit.get("races")
    check(isinstance(rows, list) and len(rows) == aligned.get("racesObserved"),
          "audit race count mismatch")
    check(audit.get("generatedAtJST"), "missing audit provenance")

    cohorts = aligned.get("first100") or {}
    confirm = aligned.get("confirmation101to200") or {}
    first_ids = set(cohorts.get("raceIDs") or [])
    confirm_ids = set(confirm.get("raceIDs") or [])
    check(not first_ids.intersection(confirm_ids), "cohorts overlap")
    check(len(first_ids) == cohorts.get("races") and
          len(confirm_ids) == confirm.get("races"), "cohort identity mismatch")

    exclusions = {(r["raceDate"], r["raceID"]) for r in
                  (quality.get("resultExclusions") or [])}
    samples = defaultdict(lambda: defaultdict(list))
    top_picks = defaultdict(lambda: [0, 0])
    counts = {"matchedAuditedRaces": 0, "pendingOrExcludedRaces": 0,
              "evaluatedRaces": 0, "evaluatedHorses": 0}
    seen = set()

    for row in rows:
        day, rid = row.get("raceDate"), row.get("raceID")
        check(bool(day) and bool(rid) and (day, rid) not in seen, "duplicate race")
        seen.add((day, rid))
        if row.get("resultState") != "matched" or (day, rid) in exclusions:
            counts["pendingOrExcludedRaces"] += 1
            continue
        counts["matchedAuditedRaces"] += 1
        record = load_bundle(root, day, rid)
        check(record.get("fingerprint") == row.get("inputFingerprint"),
              "frozen input fingerprint mismatch: " + rid)
        race = record["prediction"]
        check(race.get("predictionTiming") == "live_10min" and
              race.get("raceDate") == day and race.get("raceID") == rid,
              "not the original formal T-10 prediction: " + rid)
        horses = race.get("horses")
        check(isinstance(horses, list) and len(horses) > 2, "missing horses: " + rid)
        positions = row.get("actualFinish")
        check(isinstance(positions, dict), "missing official finishing order: " + rid)
        try:
            podium = {int(positions[str(k)]) for k in (1, 2, 3)}
            winner = int(positions["1"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("FROZEN_PROBABILITY_BLOCKED: incomplete finish: " + rid) from exc
        check(len(podium) == 3, "ambiguous podium: " + rid)
        numbers = [int(h["number"]) for h in horses]
        check(len(numbers) == len(set(numbers)) and podium.issubset(numbers),
              "horse/result identity mismatch: " + rid)
        group = "first100" if rid in first_ids else "confirmation101to200" if rid in confirm_ids else None
        check(group is not None, "unlocked cohort identity: " + rid)

        for key, (ai_key, market_key, places) in PROBABILITIES.items():
            top_number = max(horses, key=lambda h: prob(h[ai_key], ai_key))["number"]
            top_picks[(group, key)][0] += 1
            top_picks[(group, key)][1] += int(int(top_number) in
                                                  {int(positions[str(k)]) for k in range(1, places + 1)})
            for horse in horses:
                number = int(horse["number"])
                y = int(number in {int(positions[str(k)]) for k in range(1, places + 1)})
                samples[(group, key)]["all"].append({"source": "ai", "p": prob(horse[ai_key], ai_key), "y": y})
                samples[(group, key)]["all"].append({"source": "market", "p": prob(horse[market_key], market_key), "y": y})
        counts["evaluatedRaces"] += 1
        counts["evaluatedHorses"] += len(horses)

    check(counts["matchedAuditedRaces"] == aligned.get("matchedResultRaces"),
          "matched-result count mismatch")
    data = {}
    for cohort in ("first100", "confirmation101to200"):
        data[cohort] = {}
        for key in PROBABILITIES:
            n, hits = top_picks[(cohort, key)]
            data[cohort][key] = {
                "topPredictedHorsePlaced": hits,
                "topPredictedHorseRaces": n,
                "horseProbabilities": summarize(samples[(cohort, key)]["all"]),
            }

    issues = quality.get("issues")
    check(isinstance(issues, list), "missing quality issues ledger")
    return {
        "schemaVersion": 1,
        "status": "quality_limited_descriptive_only" if issues else "frozen_evidence_descriptive_only",
        "source": {"file": "AlignedPortfolioReview.json",
                   "generatedAtJST": audit["generatedAtJST"],
                   "protocolVersion": audit["protocolVersion"]},
        "quality": {"existingIssueCount": len(issues),
                    "newAlignedBetaEligible": False,
                    "researchConditionChanges": False},
        "coverage": counts,
        "probabilities": data,
        "limitations": [
            "Strictly frozen input rows only; no retrospective odds, changes or missing-race rescue.",
            "Past projection timestamp is preserved; this is not a claim of live current results.",
            "Exclude unmatched, dead-heat and changed-entrant outcomes; never count missing as losses.",
            "Brier/reliability is descriptive; horses within the same race are correlated.",
            "No threshold fitting, betting changes or beta activation.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", help="optional research-only JSON path")
    args = parser.parse_args()
    root = Path(args.root)
    audit = json.loads((root / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    protocol = json.loads((root / "production/ALIGNED_PORTFOLIO_PROTOCOL.json").read_text(encoding="utf-8"))
    report = build(root, audit, protocol)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
