"""Read-only calibration of frozen aligned portfolio ticket probabilities.

Usage: python3 production/research_ticket_calibration.py --audit AlignedPortfolioReview.json
No network, no data mutation, no model fitting, no retrospective odds fetching.
"""
import argparse
import json
import math
from pathlib import Path


def evaluate(document):
    if document.get("projectionState") != "verified":
        raise ValueError("Audit projection not verified; current performance unavailable")
    aligned = document.get("aligned") or {}
    if aligned.get("countsAreCurrent") is not True:
        raise ValueError("Aligned counts not current; current performance unavailable")
    if document.get("cohortLockRecoveryRequired") is True:
        raise ValueError("Cohort lock recovery required")
    if (document.get("cohortLocks") or {}).get("cohortLockRecoveryRequired") is True:
        raise ValueError("Cohort lock recovery required")
    rows = []
    for race in document.get("races", []):
        if race.get("scope") != "prospective" or race.get("settled") is not True:
            continue
        if race.get("resultState") != "matched":
            continue
        for leg in race.get("legs", []):
            p = leg.get("probabilityPct")
            hit = leg.get("hit")
            if p is None or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 100:
                continue
            if type(hit) is not bool:
                continue
            rows.append({"raceID": race.get("raceID"), "betType": leg.get("betType"),
                         "predictedPct": p, "hit": hit})
    def summary(group):
        n = len(group)
        if not n:
            return {"tickets": 0, "expectedHits": None, "actualHits": None,
                    "meanPredictedPct": None, "observedPct": None, "brier": None}
        probabilities = [x["predictedPct"] / 100 for x in group]
        actual = [int(x["hit"]) for x in group]
        return {"tickets": n, "expectedHits": round(sum(probabilities), 4),
                "actualHits": sum(actual),
                "meanPredictedPct": round(100 * sum(probabilities) / n, 3),
                "observedPct": round(100 * sum(actual) / n, 3),
                "brier": round(sum((p-y)**2 for p, y in zip(probabilities, actual)) / n, 6)}
    types = sorted({x["betType"] for x in rows if x["betType"]})
    bins = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 60), (60, 80), (80, 100.000001)]
    return {"status": "research_only_not_formal_recommendation",
            "auditGeneratedAtJST": document.get("generatedAtJST"),
            "alignedRacesObserved": aligned.get("racesObserved"),
            "overall": summary(rows),
            "byBetType": {kind: summary([x for x in rows if x["betType"] == kind]) for kind in types},
            "byPredictedProbabilityBand": {
                f"{lo}-{min(hi,100):g}%": summary([x for x in rows if lo <= x["predictedPct"] < hi])
                for lo, hi in bins},
            "limitations": ["Only frozen, settled, matched, prospective selected tickets",
                            "Skipped and pending tickets are excluded, not treated as misses",
                            "Small samples cannot establish calibration",
                            "Research-only; does not modify v1 rules or recommend purchases"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=Path("AlignedPortfolioReview.json"))
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.audit.read_text(encoding="utf-8"))),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
