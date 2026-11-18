"""Zero-cost read-only Wide reference-odds stress sensitivity analysis.

A haircut is a hypothetical price stress, NOT a reconstructed T-10 market
quote. It does not change saved predictions, portfolio decisions, models, odds,
staking or actual payouts. Registered aligned inputs remain immutable.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from odds_integrity_audit import read_json

HAIRCUTS=(0.0,0.1,0.2,0.3,0.5)
EV_THRESHOLDS=(1.0,1.1,1.3,1.5)


def finite_number(value):
    if type(value) not in (int, float):
        raise ValueError("finite numeric field required")
    number=float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite numeric field")
    return number


def collect_frozen_wide_reference(aligned):
    if aligned.get("projectionState")!="verified" or (
            aligned.get("aligned",{}).get("countsAreCurrent") is not True):
        raise ValueError("aligned projection is not verified/current")
    rows=[]
    unsettled=0
    for race in aligned.get("races",[]):
        refs=race.get("commonWideReference") or []
        if race.get("resultState")!="matched" or race.get("referenceSettled") is not True:
            unsettled+=len(refs)
            continue
        finish=race.get("actualFinish")
        if not isinstance(finish,dict):
            raise ValueError("settled reference is missing finish evidence")
        race_id=str(race["raceID"])
        day=str(race["raceDate"])
        for ref in refs:
            candidate=str(ref.get("candidateID",""))
            if len(candidate)!=4 or not candidate.isascii() or not candidate.isdigit():
                raise ValueError("invalid Wide candidate identity")
            a,b=int(candidate[:2]),int(candidate[2:])
            if a<1 or b<1 or a>=b or str(a) not in finish or str(b) not in finish:
                raise ValueError("Wide candidate not resolved against actualFinish")
            odds=finite_number(ref.get("oddsLow"))
            p=finite_number(ref.get("probabilityPct"))/100
            if odds<1 or not 0<p<1:
                raise ValueError("invalid reference price/probability")
            report_ev=finite_number(ref.get("expectedMultiplierLow"))
            if abs(report_ev-odds*p)>0.005:
                raise ValueError("reference EV mismatch")
            actual=(int(finish[str(a)])<=3 and int(finish[str(b)])<=3)
            if type(ref.get("hit")) is not bool or actual!=ref["hit"]:
                raise ValueError("wide reference hit mismatched with finish")
            rows.append({
                "raceID":race_id, "raceDate":day,
                "candidateID":candidate,
                "probability":p,"oddsLow":odds,
                "frozenEV":p*odds,"hit":actual
            })
    return rows,unsettled


def sensitivity(aligned, *, haircuts=HAIRCUTS, thresholds=EV_THRESHOLDS):
    bets,unsettled=collect_frozen_wide_reference(aligned)
    if not all(type(x) in (int,float) and 0<=x<1 for x in haircuts):
        raise ValueError("invalid stress haircut")
    output=[]
    for haircut in haircuts:
        threshold_rows=[]
        for threshold in thresholds:
            if type(threshold) not in (int,float) or threshold<1:
                raise ValueError("invalid EV threshold")
            eligible=[
                r for r in bets
                if r["frozenEV"]*(1-haircut)+1e-12 >= threshold
            ]
            threshold_rows.append({
                "frozenReferenceEVThreshold":threshold,
                "candidateCount":len(eligible),
                "raceCount":len({r["raceID"] for r in eligible}),
                "observedHits":sum(r["hit"] for r in eligible),
                "sumFrozenPredictedHitProbabilities":round(
                    sum(r["probability"] for r in eligible),4),
            })
        output.append({
            "assumedQuoteHaircutPct":int(round(haircut*100)),
            "hypotheticalOnly":True,
            "quotationsAtExactObservationTimeKnown":False,
            "thresholds":threshold_rows,
        })
    by_day=[]
    for day in sorted({r["raceDate"] for r in bets}):
        subset=[r for r in bets if r["raceDate"]==day]
        by_day.append({
            "raceDate":day,
            "candidateCount":len(subset),
            "raceCount":len({r["raceID"] for r in subset}),
            "sumFrozenPredictedHits":round(sum(r["probability"] for r in subset),4),
            "observedHits":sum(r["hit"] for r in subset),
        })
    return {
        "schemaVersion":1,
        "researchMode":"counterfactual_wide_lower_quote_sensitivity",
        "byRaceDateCalibrationDiagnostic":by_day,
        "alignedVerified":True,
        "referenceCandidatesSettled":len(bets),
        "referenceCandidatesNotYetSettled":unsettled,
        "referenceRacesSettled":len({r["raceID"] for r in bets}),
        "allReferencePredictedHits":round(sum(r["probability"] for r in bets),4),
        "allReferenceObservedHits":sum(r["hit"] for r in bets),
        "scenarios":output,
        "historicalResultsUsedToSelectThresholds":False,
        "marketPriceAtT10Verified":False,
        "marketSourceFreshnessVerified":False,
        "expectedValueAdvantageValidated":False,
        "actualPurchasesReconstructed":False,
        "productionModelChanged":False,
        "bettingEnabled":False,
        "networkRequests":0,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    args=parser.parse_args()
    aligned=read_json(args.root/"AlignedPortfolioReview.json")
    result=sensitivity(aligned)
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
