#!/usr/bin/env python3
"""Publish three independent, read-only research tracks from immutable evidence.

This program does not collect race data, fit probabilities, replay forecasts,
select stakes, replace the formal model, or enable betting. It links existing
strict-audit, retrospective calibration and separately pre-frozen prospective
shadow evidence, without equating them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_market_shrink_shadow import evaluate as evaluate_forward
from inspect_frozen_win_probabilities import build as inspect_frozen


def ensure(condition, reason):
    if not condition:
        raise ValueError("PARALLEL_PREDICTION_RESEARCH_BLOCKED: " + reason)


def build(audit: dict, descriptive: dict, forward: dict) -> dict:
    aligned = audit.get("aligned") or {}
    quality = audit.get("quality") or {}
    ensure(audit.get("projectionState") == "verified" and
           aligned.get("countsAreCurrent") is True,
           "only current verified aligned audit may be analyzed")
    ensure(audit.get("cohortLockRecoveryRequired") is not True,
           "cohort identity requires recovery")
    ensure(isinstance(audit.get("generatedAtJST"), str) and
           audit["generatedAtJST"], "missing audit provenance")
    ensure(descriptive.get("source", {}).get("generatedAtJST") ==
           audit["generatedAtJST"], "historical diagnostic provenance mismatch")
    ensure(forward.get("sourceAuditGeneratedAtJST") ==
           audit["generatedAtJST"], "prospective review provenance mismatch")
    ensure(descriptive.get("coverage", {}).get("matchedAuditedRaces") ==
           aligned.get("matchedResultRaces"),
           "descriptive matched race count inconsistent with audit")
    ensure(descriptive.get("quality", {}).get("newAlignedBetaEligible") is False,
           "descriptive report must not enable beta")
    ensure(forward.get("autoBetEnabled") is False and
           forward.get("formalRecommendationsEnabled") is False,
           "forward evidence must not activate wagering")
    ensure(forward.get("status") in (
        "insufficient_prospective_evidence", "prospective_descriptive_only"),
        "unexpected prospective status")

    cohorts = {}
    for label in ("first100", "confirmation101to200"):
        data = descriptive["probabilities"][label]
        by_event = {}
        for event in ("win", "top2", "top3"):
            e = data[event]
            horse_stats = e["horseProbabilities"]
            paired = e["pairedRaceUncertainty"]
            by_event[event] = {
                "races": e["topPredictedHorseRaces"],
                "topAIHits": e["topPredictedHorsePlaced"],
                "topMarketHits": e["topPredictedMarketHorsePlaced"],
                "brier": {name: (horse_stats[name]["brierScore"]
                                 if horse_stats[name] is not None else None)
                          for name in ("ai", "market", "marketBlend50")},
                "aiMinusMarketBrier": paired["ai"]["meanBrierDelta"],
                "aiMinusMarketBootstrap95": paired["ai"]["bootstrap95Pct"],
                "blend50MinusMarketBrier":
                    paired["marketBlend50"]["meanBrierDelta"],
                "blend50MinusMarketBootstrap95":
                    paired["marketBlend50"]["bootstrap95Pct"],
            }
        cohorts[label] = by_event

    current = aligned.get("racesObserved")
    matched = aligned.get("matchedResultRaces")
    ensure(type(current) is int and type(matched) is int and
           0 <= matched <= current, "invalid verified race counts")
    issue_count = len(quality.get("issues") or [])
    paired_forward = forward["counts"]["pairedAuditedRaces"]
    ensure(type(paired_forward) is int and paired_forward >= 0 and
           paired_forward <= matched, "invalid prospective paired count")

    return {
        "schemaVersion": 1,
        "version": "parallel-prediction-research-v1-20261122",
        "status": "quality_limited_research_only" if issue_count
                  else "descriptive_research_only",
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "researchOnly": True,
        "formalPredictionAIChanged": False,
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "candidatePromotionApproved": False,
        "betaAutomaticallyEnabled": False,
        "tracks": {
            "collection": {
                "verifiedAlignedRaces": current,
                "matchedAlignedRaces": matched,
                "additionalMatchedTo300": max(0, 300 - matched),
                "qualityStatus": quality.get("status"),
                "qualityIssueCount": issue_count,
                "missingFormalCaptures": quality.get("missingCaptureCount"),
                "pendingSelectedSettlements":
                    (aligned.get("overall") or {}).get("resultsPendingSelectedRaces"),
            },
            "historicalDiagnostics": {
                "evidence": "fixed_first100_and_101to200_descriptive_only",
                "evaluatedRaces": descriptive["coverage"]["evaluatedRaces"],
                "cohorts": cohorts,
                "status": descriptive["status"],
            },
            "prospectiveCandidateValidation": {
                "evidence": "separately_frozen_T10_shadow_only",
                "frozenSnapshots": forward["counts"]["frozenSnapshots"],
                "pairedAuditedRaces": paired_forward,
                "minimumDescriptiveRaces": 100,
                "status": forward["status"],
                "byEvent": forward["byEvent"],
                "weightsOnAI": forward["registeredFixedWeightsOnAI"],
                "candidateSelectionAllowed": False,
            },
        },
        "limitations": [
            "Historical calibration is descriptive, never a prospective candidate test.",
            "The 50:50 historical blend and future pre-frozen shadows are different cohorts.",
            "A reported Brier advantage is not a measured change in the production model.",
            "Do not fit or select coefficients on this report's evaluation races.",
            "Missing formal captures, unverified results and pending settlements are not losses.",
            "Horse observations within a race are dependent; compare paired whole races.",
            "No model promotion, formal rule changes, beta activation or auto-betting.",
        ],
    }


def markdown(report):
    collection = report["tracks"]["collection"]
    historical = report["tracks"]["historicalDiagnostics"]
    future = report["tracks"]["prospectiveCandidateValidation"]
    def fmt(v):
        return "—" if v is None else str(v)
    lines = [
        "# KeibaAI Parallel Prediction Research (read-only)",
        "",
        "Status: " + report["status"],
        "Source strict audit: " + report["sourceAuditGeneratedAtJST"],
        "Formal AI / betting unchanged; no automatic candidate promotion.",
        "",
        "## Track 1: Collection and verification",
        "- Verified aligned races: " + str(collection["verifiedAlignedRaces"]),
        "- Independently matched races: " + str(collection["matchedAlignedRaces"]),
        "- Remaining matched races for the 300-R research gate: " +
        str(collection["additionalMatchedTo300"]),
        "- Formal capture misses: " + fmt(collection["missingFormalCaptures"]),
        "- Outstanding quality issues: " + str(collection["qualityIssueCount"]),
        "- Pending selected settlements: " +
        fmt(collection["pendingSelectedSettlements"]),
        "",
        "## Track 2: Fixed historical probability diagnostics",
        "Scored races (within registered first 200 only): " +
        str(historical["evaluatedRaces"]),
        "| Cohort | Event | Races | AI Brier | Market Brier | Fixed 50:50 Brier | AI minus market |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cohort in ("first100", "confirmation101to200"):
        for event in ("win", "top2", "top3"):
            e = historical["cohorts"][cohort][event]
            b = e["brier"]
            lines.append("| " + cohort + " | " + event + " | " +
                         str(e["races"]) + " | " + " | ".join(
                             fmt(b[key]) for key in ("ai", "market", "marketBlend50")
                         ) + " | " + fmt(e["aiMinusMarketBrier"]) + " |")
    lines += [
        "",
        "Lower Brier is better. A negative AI-minus-market delta favors AI.",
        "These historical scores are not evidence that any new model improves future bets.",
        "",
        "## Track 3: Prospectively frozen candidate validation",
        "- Immutable T-10 shadow records: " + str(future["frozenSnapshots"]),
        "- Independently result-matched shadows: " + str(future["pairedAuditedRaces"]),
        "- Current evidence status: " + future["status"],
        "- 100 prospective matched races is only a descriptive review milestone.",
        "| Event | Paired races | Market Brier | 25%-AI Brier | 50%-AI Brier | Existing AI Brier |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for event in ("win", "top2", "top3"):
        e = future["byEvent"][event]
        b = e["brierByModel"]
        lines.append("| " + event + " | " + str(e["pairedRaces"]) +
                     " | " + " | ".join(fmt(b.get(key)) for key in (
                         "marketOnly", "marketBlend25", "marketBlend50", "existingAI")
                     ) + " |")
    lines += [
        "",
        "Do not infer profitability or promote a model from these descriptive comparisons.",
        "Audit quality issues remain visible; the 300-R beta gate is separate.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    audit = json.loads((root / "AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    protocol = json.loads((root / "production/ALIGNED_PORTFOLIO_PROTOCOL.json").read_text(encoding="utf-8"))
    # These readers enforce formal fingerprint, cohort, timing and provenance.
    historical = inspect_frozen(root, audit, protocol)
    prospective = evaluate_forward(root, audit)
    report = build(audit, historical, prospective)
    output = root / "research" / "ParallelPredictionResearch.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 allow_nan=False) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print("PARALLEL_RESEARCH:", report["status"],
          "matched", report["tracks"]["collection"]["matchedAlignedRaces"],
          "prospective", report["tracks"]["prospectiveCandidateValidation"]["pairedAuditedRaces"])


if __name__ == "__main__":
    main()
