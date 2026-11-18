"""No-cost, read-only full-field Wide probability audit from frozen shadow files.

This audits ALL unordered horse pairs in a race instead of selected reference
bets. In any ordinary three-placed-horse race, the number of winning Wide
pairs is *exactly three*. A coherent full-field model also has probabilities
summing to three, so raw expected-vs-observed total hits is a tautology, NOT
evidence of calibration. Assess Brier and log loss vs race-uniform baseline,
probability buckets, and frozen-reference-vs-shadow disagreements instead.

Zero network, zero fees, no odds source claims, no model/betting changes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import itertools
import json
import math
from pathlib import Path

from odds_integrity_audit import read_json, parse_jst

BINS=((0,0.01,"<1%"),(0.01,0.03,"1-<3%"),(0.03,0.05,"3-<5%"),
      (0.05,0.10,"5-<10%"),(0.10,0.20,"10-<20%"),(0.20,1.01,">=20%"))
PAIR_SUM_TOLERANCE=0.0001


def strict_number(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("nonfinite or nonnumeric Wide probability")
    if value<0 or value>1:
        raise ValueError("Wide probability not in [0,1]")
    return float(value)


def exact_pair_key(a,b):
    return f"{min(a,b):02d}{max(a,b):02d}"


def extract_race_pairs(record, race, *, expected_capture=None):
    """Reject incomplete/misaligned matrices; derive outcomes from actualFinish."""
    formal=record.get("formalPrediction")
    if not isinstance(formal,dict):
        raise ValueError("missing formal prediction identity")
    for name in ("raceID","raceDate","startTime"):
        if str(formal.get(name))!=str(race.get(name)):
            raise ValueError("formal prediction identity/time mismatch: "+name)
    if formal.get("captureQuality")!="formal_10min":
        raise ValueError("not a frozen T-10 prediction")
    capture=parse_jst(formal.get("capturedAtJST"))
    if capture is None:
        raise ValueError("prediction capture time missing")
    if expected_capture is not None:
        frozen=parse_jst(expected_capture)
        if frozen is None or abs((capture-frozen).total_seconds())>180:
            raise ValueError("ticket and formal snapshot captures differ over 3 minutes")
    nums=formal.get("horseNumbers")
    if (not isinstance(nums,list) or len(nums)<3 or
        any(type(v) is not int or v<=0 or v>99 for v in nums) or
        len(set(nums))!=len(nums)):
        raise ValueError("invalid or duplicate formal horse numbers")
    nums=sorted(nums)
    matrix=record.get("ticketProbabilities")
    if not isinstance(matrix,dict):
        raise ValueError("missing ticket probability map")
    wide=matrix.get("wide")
    if not isinstance(wide,dict):
        raise ValueError("missing Wide probability matrix")
    pairs=list(itertools.combinations(nums,2))
    expected_keys={exact_pair_key(a,b) for a,b in pairs}
    if set(wide)!=expected_keys:
        raise ValueError("incomplete/extra Wide pair probabilities")
    finish=race.get("actualFinish")
    if not isinstance(finish,dict) or not all(str(n) in finish for n in nums):
        raise ValueError("race finish missing a predicted horse")
    positions={}
    for n in nums:
        value=finish[str(n)]
        if type(value) is not int or value<1:
            raise ValueError("invalid finish position")
        positions[n]=value
    placed=[n for n in nums if positions[n]<=3]
    if len(placed)!=3:
        raise ValueError("not exactly three distinct podium horses")
    rows=[]
    for a,b in pairs:
        key=exact_pair_key(a,b)
        p=strict_number(wide[key])
        hit=positions[a]<=3 and positions[b]<=3
        rows.append({"raceDate":race["raceDate"],"raceID":race["raceID"],
                     "candidateID":key,"probability":p,"hit":hit,
                     "horseCount":len(nums)})
    if abs(math.fsum(x["probability"] for x in rows)-3)>PAIR_SUM_TOLERANCE:
        raise ValueError("Wide full-field probability sum not three")
    if sum(x["hit"] for x in rows)!=3:
        raise ValueError("three winners invariant failed")
    return rows


def score(rows):
    n=len(rows)
    if not n:
        return {"pairs":0,"brier":None,"uniformBrier":None,"logLoss":None,
                "uniformLogLoss":None}
    p0=3/n
    eps=1e-15
    brier=math.fsum((int(x["hit"])-x["probability"])**2 for x in rows)/n
    baseline=math.fsum((int(x["hit"])-p0)**2 for x in rows)/n
    ll=-math.fsum(int(x["hit"])*math.log(max(x["probability"],eps))+
                  (1-int(x["hit"]))*math.log(max(1-x["probability"],eps))
                  for x in rows)/n
    ll0=-math.fsum(int(x["hit"])*math.log(p0)+
                   (1-int(x["hit"]))*math.log(1-p0) for x in rows)/n
    return {"pairs":n,"brier":round(brier,8),
            "uniformBrier":round(baseline,8),
            "logLoss":round(ll,8),"uniformLogLoss":round(ll0,8),
            "brierBetterThanUniform":brier<baseline,
            "logLossBetterThanUniform":ll<ll0}


def audit_repository(root):
    aligned=read_json(root/"AlignedPortfolioReview.json")
    if (aligned.get("projectionState")!="verified"
            or aligned.get("aligned",{}).get("countsAreCurrent") is not True):
        raise ValueError("aligned portfolio not verified/current")
    included=[]
    exceptions=[]
    by_race=[]
    reference_comparison=[]
    for race in aligned.get("races",[]):
        if race.get("resultState")!="matched":
            continue
        day=str(race["raceDate"]).replace("-","")
        rid=str(race["raceID"])
        file=root/"reports"/day/"ticket_shadow_v1"/"prediction"/f"{rid}.json"
        frozen=root/"reports"/day/"all_bet_v1"/"win"/f"{rid}.json"
        if not file.is_file():
            exceptions.append({"raceID":rid,"reason":"missing_frozen_ticket_shadow"})
            continue
        if not frozen.is_file():
            exceptions.append({"raceID":rid,"reason":"missing_frozen_formal_win_record"})
            continue
        try:
            record=read_json(file)
            formal=read_json(frozen).get("prediction",{})
            rows=extract_race_pairs(record,race,
                                    expected_capture=formal.get("capturedAtJST"))
            included.extend(rows)
            by_race.append({"raceDate":race["raceDate"],"raceID":rid,
                            "horseCount":rows[0]["horseCount"],
                            "expectedHits":round(math.fsum(x["probability"] for x in rows),6),
                            "observedHits":sum(x["hit"] for x in rows),
                            **score(rows)})
            lookup={x["candidateID"]:x["probability"] for x in rows}
            for ref in race.get("commonWideReference",[]):
                key=str(ref.get("candidateID",""))
                if key not in lookup:
                    raise ValueError("reference candidate not in full-field matrix")
                val=ref.get("probabilityPct")
                if type(val) not in (int,float) or not math.isfinite(val):
                    raise ValueError("invalid frozen reference probability")
                probability=float(val)/100
                if not 0<=probability<=1:
                    raise ValueError("reference probability outside unit interval")
                reference_comparison.append({
                    "raceDate":race["raceDate"],"raceID":rid,"candidateID":key,
                    "referenceProbabilityPct":round(probability*100,4),
                    "shadowMatrixProbabilityPct":round(lookup[key]*100,4),
                    "absDifferencePercentagePoints":abs(100*(probability-lookup[key])),
                })
        except (KeyError,ValueError,TypeError,OverflowError,IndexError,json.JSONDecodeError) as err:
            # Remove any partial per-race additions on errors, not just mark as bad.
            included=[r for r in included if r["raceID"]!=rid or r["raceDate"]!=race["raceDate"]]
            by_race=[r for r in by_race if r["raceID"]!=rid or r["raceDate"]!=race["raceDate"]]
            reference_comparison=[r for r in reference_comparison if r["raceID"]!=rid or r["raceDate"]!=race["raceDate"]]
            exceptions.append({"raceID":rid,"reason":str(err)[:180]})
    if not by_race:
        return {"schemaVersion":1,"qualityGate":"no_verified_full_field_records",
                "excluded":exceptions,"productionChanged":False}
    totals=sum(x["pairs"] for x in by_race)
    wins=sum(x["observedHits"] for x in by_race)
    predicted=sum(x["expectedHits"] for x in by_race)
    weighted=lambda key:sum(r[key]*r["pairs"] for r in by_race)/totals
    bucket=[]
    for low,high,label in BINS:
        entries=[r for r in included if low<=r["probability"]<high]
        bucket.append({
            "band":label,"pairs":len(entries),
            "races":len({r["raceID"] for r in entries}),
            "predictedHitCount":round(math.fsum(r["probability"] for r in entries),4),
            "observedHitCount":sum(r["hit"] for r in entries),
            "averagePredictedPct":round(100*math.fsum(r["probability"] for r in entries)/len(entries),3) if entries else None,
            "actualHitRatePct":round(100*sum(r["hit"] for r in entries)/len(entries),3) if entries else None,
        })
    dates=sorted({r["raceDate"] for r in included})
    per_date=[]
    for date in dates:
        races=[r for r in by_race if r["raceDate"]==date]
        n=sum(r["pairs"] for r in races)
        per_date.append({
            "raceDate":date,"races":len(races),"pairs":n,
            "brier":round(sum(r["brier"]*r["pairs"] for r in races)/n,6),
            "uniformBrier":round(sum(r["uniformBrier"]*r["pairs"] for r in races)/n,6),
            "racesBrierBetterThanUniform":sum(r["brierBetterThanUniform"] for r in races),
        })
    deviations=sorted(x["absDifferencePercentagePoints"] for x in reference_comparison)
    return {
        "schemaVersion":1,
        "mode":"full_field_wide_shadow_probabilities_vs_podium",
        "candidatePopulation":"all_pairs_not_selected_reference_bets",
        "eligibleMatchedRaces":sum(r.get("resultState")=="matched" for r in aligned["races"]),
        "racesAudited":len(by_race),
        "racesExcluded":len(exceptions),
        "excludedReasons":dict(sorted(Counter(x["reason"] for x in exceptions).items())),
        "excludedExamples":exceptions[:20],
        "totalPairsCompared":totals,
        "fullFieldExpectedHits":round(predicted,4),
        "fullFieldObservedHits":wins,
        "fullFieldExpectedHitTotalIsTautology":True,
        "modelBrier":round(weighted("brier"),7),
        "uniformBrier":round(weighted("uniformBrier"),7),
        "modelLogLoss":round(weighted("logLoss"),7),
        "uniformLogLoss":round(weighted("uniformLogLoss"),7),
        "racesBrierBetterThanUniform":sum(x["brierBetterThanUniform"] for x in by_race),
        "racesLogLossBetterThanUniform":sum(x["logLossBetterThanUniform"] for x in by_race),
        "fixedProbabilityBins":bucket,
        "byRaceDate":per_date,
        "referenceVsFullField":{
            "matchedPairs":len(reference_comparison),
            "meanAbsProbabilityDifferencePp":round(sum(deviations)/len(deviations),4) if deviations else None,
            "maxAbsProbabilityDifferencePp":round(max(deviations),4) if deviations else None,
            "sameProbabilityModelAssumed":False,
        },
        "noStatisticalSignificanceClaimed":True,
        "crossValidationPerformed":False,
        "oddsSourceFreshnessVerified":False,
        "liveRecommendationsChanged":False,
        "productionModelChanged":False,
        "networkRequests":0,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    args=parser.parse_args()
    print(json.dumps(audit_repository(args.root),ensure_ascii=False,
                     indent=2,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
