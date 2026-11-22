#!/usr/bin/env python3
"""Retrospective SAME-TIMESTAMP comparison on truly archived formal T-10 races.

Compare a market-only calibration selected using 2024 (never 2026) to the
EXISTING frozen formal AI and market, on identical results and race entrants.
The historical-enriched 2% challenger is NOT evaluated here: its old
per-race feature matrix was not first-frozen in 2026. Do not fabricate it.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium, paired_race_uncertainty
from market_shrink_shadow import JST
from train_market_anchored_challenger import (
    ROOT, VERSION as VERSION_ANCHOR, POWERS, renorm
)
from train_top2_top3_models import plackett_luce_top2_top3

STRATEGY = ROOT / "research/model_candidates/market_anchored_v1/frozen_strategy.json"
OUTPUT = ROOT / "research" / "MarketCalibratedFormalT10Review.json"
EVENTS = {
    "win": ("aiProbability", "marketProbability", 1),
    "top2": ("aiTop2Probability", "marketTop2Probability", 2),
    "top3": ("aiTop3Probability", "marketTop3Probability", 3),
}


def ensure(ok, message):
    if not ok:
        raise ValueError("MARKET_CALIBRATED_FORMAL_T10_BLOCKED: " + message)


def load_strategy(file_path=STRATEGY):
    strategy = json.loads(Path(file_path).read_text(encoding="utf-8"))
    p = (strategy.get("strategies") or {}).get("marketCalibrated")
    ensure(strategy.get("version") == VERSION_ANCHOR and
           strategy.get("parametersSelectedFrom") == "2024-only" and
           strategy.get("researchOnly") is True and
           strategy.get("candidatePromotionApproved") is False and
           strategy.get("autoBetEnabled") is False and
           strategy.get("formalPredictionAIChanged") is False,
           "untrusted strategy provenance/policy")
    ensure(isinstance(p, dict) and p.get("historicalWeight") == 0.0 and
           p.get("method") == "linear" and p.get("marketPower") in POWERS,
           "unexpected market-only coefficient identity")
    return strategy


def calibrated_probabilities(numbers, market, power):
    ensure(len(numbers) == len(market) and len(numbers) >= 3 and
           len(numbers) == len(set(numbers)), "invalid entrant identity")
    values = np.asarray(market, dtype=np.float64)
    ensure(np.isfinite(values).all() and (values > 0).all() and
           (values <= 1).all(), "invalid frozen T10 market probabilities")
    win = renorm(np.power(values, power), ["race"] * len(numbers))
    df = pd.DataFrame({"レースID": ["race"]*len(numbers), "市場確率": win})
    top2, top3 = plackett_luce_top2_top3(df)
    return {"win": win, "top2": top2, "top3": top3}


def evaluate(root, audit, strategy, load_bundle=capture.load):
    aligned = audit.get("aligned") or {}
    ensure(audit.get("projectionState") == "verified" and
           aligned.get("countsAreCurrent") is True and
           audit.get("cohortLockRecoveryRequired") is not True,
           "current verified formal audit required")
    records = audit.get("races")
    ensure(isinstance(records, list) and
           len(records) == aligned.get("racesObserved"),
           "audit has inconsistent race count")
    params = strategy["strategies"]["marketCalibrated"]
    ensure(params["historicalWeight"] == 0 and
           params["marketPower"] in POWERS and
           strategy["parametersSelectedFrom"] == "2024-only",
           "parameter selection provenance changed")
    excluded = {
        (r["raceDate"], r["raceID"])
        for r in (audit.get("quality") or {}).get("resultExclusions", [])
    }
    per_event = {event: [] for event in EVENTS}
    counts = {
        "auditedRaces": len(records),
        "matchedResultRaces": aligned["matchedResultRaces"],
        "validPairedRacesWin": 0,
        "unmatchedOrExcludedRaces": 0,
        "podiumUnavailableByEvent": {e: 0 for e in EVENTS},
    }
    for row in records:
        ident = (row["raceDate"], row["raceID"])
        if row.get("resultState") != "matched" or ident in excluded:
            counts["unmatchedOrExcludedRaces"] += 1
            continue
        saved = load_bundle(root, *ident)
        ensure(saved["fingerprint"] == row["inputFingerprint"],
               "formal prediction fingerprint mismatch")
        race = saved["prediction"]
        ensure(race.get("predictionTiming") == "live_10min" and
               race.get("captureQuality") == "formal_10min",
               "not valid pre-race T10 capture")
        horses = race["horses"]
        numbers = [h["number"] for h in horses]
        market = np.asarray([h.get("marketProbability") for h in horses],
                            dtype=float) / 100
        candidate = calibrated_probabilities(numbers, market,
                                            params["marketPower"])
        outcomes = row.get("actualFinish")
        ensure(isinstance(outcomes, dict), "no official finish mapping")
        for event, (ai_key, market_key, places) in EVENTS.items():
            try:
                podium = official_podium(outcomes, numbers, places, row["raceID"])
            except ValueError as exc:
                if "ambiguous podium dead heat" in str(exc):
                    counts["podiumUnavailableByEvent"][event] += 1
                    continue
                raise
            if podium is None:
                counts["podiumUnavailableByEvent"][event] += 1
                continue
            true = np.asarray([int(n in podium) for n in numbers], dtype=float)
            ai = np.asarray([h.get(ai_key) for h in horses], dtype=float) / 100
            mkt = np.asarray([h.get(market_key) for h in horses], dtype=float) / 100
            ensure(np.isfinite(ai).all() and np.isfinite(mkt).all() and
                   np.min(ai) >= 0 and np.max(ai) <= 1 and
                   np.min(mkt) >= 0 and np.max(mkt) <= 1,
                   "invalid stored T10 reference probabilities")
            estimates = {"formalAI": ai, "T10Market": mkt,
                         "marketCalibrated": candidate[event]}
            per_event[event].append({
                name: float(np.mean((probs - true) ** 2))
                for name, probs in estimates.items()
            })
        counts["validPairedRacesWin"] = len(per_event["win"])
    ensure(counts["validPairedRacesWin"] <= counts["matchedResultRaces"],
           "paired count exceeds verified matched results")
    out = {}
    for event, rows in per_event.items():
        means = {name: float(np.mean([r[name] for r in rows]))
                 if rows else None
                 for name in ("formalAI", "T10Market", "marketCalibrated")}
        out[event] = {
            "pairedRaces": len(rows),
            "equalRaceBrier": means,
            "candidateMinusFormalAI": paired_race_uncertainty([
                r["marketCalibrated"] - r["formalAI"] for r in rows
            ]),
            "candidateMinusT10Market": paired_race_uncertainty([
                r["marketCalibrated"] - r["T10Market"] for r in rows
            ]),
        }
    return {
        "schemaVersion": 1, "version": "market-calibrated-formal-T10-review-v1",
        "generatedAtJST": datetime.now(JST).isoformat(),
        "status": "retrospective_same_timestamp_not_prospective_promotion",
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "parameterSource": "2024-only-closing-odds-validation",
        "marketPower": params["marketPower"],
        "selectionAndScoringSeparated": True,
        "researchOnly": True, "candidatePromotionApproved": False,
        "formalPredictionAIChanged": False, "autoBetEnabled": False,
        "counts": counts, "byEvent": out,
        "limitations": [
            "Predictions share the same original formal T10 market snapshot.",
            "Market calibration coefficients were fitted on historical 2024 closing odds.",
            "Retrospective 2026 formal captures are NOT freshly preregistered prospective trials.",
            "No 2026 results used to change candidate parameters.",
            "The historical ensemble requires the separate future first-frozen no-odds model and cannot be reconstructed here.",
            "The archive has existing quality issues; matched/excluded races stay separate.",
            "Uncertainty resamples whole races; not independent horse rows.",
            "No automatic model promotion, betting or ROI claims.",
        ],
    }


def main():
    audit = json.loads((ROOT/"AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    report = evaluate(ROOT, audit, load_strategy())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 allow_nan=False)+"\n", encoding="utf-8")
    lines = [
        "# Frozen T10 retrospective benchmark: historical 2024 market calibration",
        "",
        "Status: " + report["status"],
        "Source aligned audit: " + report["sourceAuditGeneratedAtJST"],
        f"2024-selected market power: {report['marketPower']}",
        f"Matched formal win races: {report['counts']['validPairedRacesWin']}",
        "", "| Event | Races | Existing formal AI | Original T10 market | Calibrated T10 market | Candidate - existing AI | Whole-race CI 95% |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for event in ("win", "top2", "top3"):
        e = report["byEvent"][event]
        b = e["equalRaceBrier"]
        lines.append(f"| {event} | {e['pairedRaces']} | {b['formalAI']} | "
                     f"{b['T10Market']} | {b['marketCalibrated']} | "
                     f"{e['candidateMinusFormalAI']['meanBrierDelta']} | "
                     f"{e['candidateMinusFormalAI']['bootstrap95Pct']} |")
    lines += [
        "", "Lower is better. A negative candidate-minus-formal-AI delta favors calibration.",
        "This is a retrospective comparison on originally frozen T10 inputs.",
        "It is NOT a first-frozen prospective test of the new strategy or an ROI claim.",
        "",
    ]
    OUTPUT.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print("MARKET_CALIBRATED_T10_ARCHIVE_COMPARISON",
          report["counts"],report["byEvent"]["win"])


if __name__ == "__main__":
    main()
