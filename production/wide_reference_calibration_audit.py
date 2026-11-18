"""Read-only, race-cluster-aware calibration diagnostics of frozen Wide candidates.

Purpose: test whether the *selected reference candidates*' event probabilities
track observed hit frequencies. Do not reinterpret the 165 overlapping Wide
tickets as independent Bernoulli trials. All reported bootstrap intervals are
DESCRIPTIVE resampling intervals, not formal confidence intervals or evidence
of a profitable betting strategy.

Only the verified/current aligned archival cohort is used. No provider API
access, odds edits, retraining, model promotion or wagering.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random

from odds_integrity_audit import read_json
from zero_cost_wide_stress_audit import collect_frozen_wide_reference

VERSION = "wide-reference-calibration-race-cluster-v1"
SEED = 20261118
REPS = 4000
# Fixed before inspecting results. Avoid selecting cutoffs by realized outcomes.
BINS = ((0.0, 0.05, "p<5%"),
        (0.05, 0.10, "5%-<10%"),
        (0.10, 0.20, "10%-<20%"),
        (0.20, 1.0, "20%-100%"))


def group_by_race(rows):
    grouped = defaultdict(list)
    seen = set()
    for row in rows:
        key = (row["raceDate"], row["raceID"])
        pair = (key, row["candidateID"])
        if pair in seen:
            raise ValueError("duplicated Wide reference pair within one race")
        seen.add(pair)
        grouped[key].append(row)
    return dict(sorted(grouped.items()))


def moment(rows):
    n = len(rows)
    observed = sum(int(x["hit"]) for x in rows)
    predicted = math.fsum(x["probability"] for x in rows)
    brier = (math.fsum((int(x["hit"])-x["probability"])**2 for x in rows)/n
             if n else None)
    baseline = observed/n if n else None
    return {
        "candidateCount": n,
        "predictedHitCount": round(predicted, 4),
        "observedHitCount": observed,
        "predictedHitRatePct": round(100*predicted/n, 3) if n else None,
        "observedHitRatePct": round(100*observed/n, 3) if n else None,
        "observedMinusPredictedHits": round(observed-predicted, 4),
        "observedToPredictedHitRatio": (
            round(observed/predicted, 4) if predicted else None
        ),
        "brierScoreSelectedCandidates": round(brier, 5) if brier is not None else None,
        "withinSampleConstantRateBrier": (
            round(baseline*(1-baseline), 5) if baseline is not None else None
        ),
    }


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = q*(len(values)-1)
    lower = int(position)
    upper = min(lower+1, len(values)-1)
    weight = position-lower
    return values[lower]*(1-weight)+values[upper]*weight


def bootstrap_by_cluster(groups, *, reps=REPS, seed=SEED):
    """Bootstrap entire race blocks (never individual overlapping pairs)."""
    if not groups:
        return {"clusterCount":0,"resamples":0,"description":"no data"}
    if type(reps) is not int or not 1<=reps<=100000:
        raise ValueError("bad bootstrap replication count")
    keys = sorted(groups)
    rng = random.Random(seed)
    ratios = []
    gaps = []
    brier = []
    for _ in range(reps):
        sample = [row for _ in keys
                  for row in groups[keys[rng.randrange(len(keys))]]]
        predicted = math.fsum(x["probability"] for x in sample)
        observed = sum(x["hit"] for x in sample)
        ratios.append(observed/predicted if predicted>0 else 0.0)
        gaps.append(100*(observed-predicted)/len(sample))
        brier.append(math.fsum((int(x["hit"])-x["probability"])**2
                               for x in sample)/len(sample))
    def interval(xs, scale=1):
        return [round(scale*percentile(xs,0.025),4),
                round(scale*percentile(xs,0.975),4)]
    return {
        "clusterCount":len(keys),
        "resamples":reps,"fixedSeed":seed,
        "resampleUnit":"entire_race_keeping_all_correlated_tickets",
        "observedToPredictedHitRatioDescriptivePercentiles":interval(ratios),
        "hitRateGapPpDescriptivePercentiles":interval(gaps),
        "brierDescriptivePercentiles":interval(brier),
        "formalConfidenceInterval":False,
        "preregisteredCalibrationTest":False,
        "caveat":"Resampling assumes races representative; day-level drift and selection bias remain",
    }


def report(aligned, *, reps=REPS, seed=SEED):
    rows,unsettled=collect_frozen_wide_reference(aligned)
    clusters=group_by_race(rows)
    if any(sum(x["hit"] for x in sample)>3 for sample in clusters.values()):
        raise ValueError("more than 3 winning Wide pairs within one race")
    dates=sorted({key[0] for key in clusters})
    by_day=[]
    leave_out=[]
    for date in dates:
        this=[x for x in rows if x["raceDate"]==date]
        rest=[x for x in rows if x["raceDate"]!=date]
        day=moment(this)
        by_day.append({"raceDate":date,
                       "raceCount":len({x["raceID"] for x in this}),**day})
        leave_out.append({"excludedRaceDate":date,
                          "includedRaceDates":len(dates)-1,**moment(rest)})
    buckets=[]
    for low,high,label in BINS:
        matches=[x for x in rows if low<=x["probability"]<high or (
            high==1.0 and x["probability"]==1.0)]
        buckets.append({"probabilityBand":label,
                        "raceCount":len({(x["raceDate"],x["raceID"]) for x in matches}),
                        **moment(matches)})
    # Unlike a naive per-ticket binomial test, the resampling unit here is
    # the race. With only four dates even race-block percentiles must not
    # be represented as robust time-series calibration inference.
    result = {
        "schemaVersion":1, "version":VERSION,
        "cohortGate":"verified_and_counts_current",
        "referencePopulation":"frozen_selected_Wide_reference_candidates_only",
        "allCandidateStatistics":moment(rows),
        "settledReferenceRaceCount":len(clusters),
        "settledReferenceDayCount":len(dates),
        "unsettledReferenceCandidatesExcluded":unsettled,
        "byDay":by_day,
        "leaveOneDayOut":leave_out,
        "fixedProbabilityBins":buckets,
        "raceClusterBootstrap":bootstrap_by_cluster(
            clusters,reps=reps,seed=seed),
        "candidateEventsIndependentAssumption":False,
        "historicalResultsUsedToSelectThresholds":False,
        "formalStatisticalSignificanceEstablished":False,
        "marketOddsFreshnessVerified":False,
        "marketExpectedValueValidated":False,
        "liveBettingEnabled":False,
        "productionModelChanged":False,
        "networkRequests":0,
    }
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--reps",type=int,default=REPS)
    args=parser.parse_args()
    aligned=read_json(args.root/"AlignedPortfolioReview.json")
    print(json.dumps(report(aligned,reps=args.reps),ensure_ascii=False,
                     indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
