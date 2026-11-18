"""Read-only reproduction of the frozen two-stage Wide reference selector.

Check that the saved portfolio signal, frozen registered selector and settled
commonWideReference agree. Diagnose impact of the top-20 display window,
probability/EV gates and Kelly-derived ranking; these are retrospective
diagnostics on locked cohorts, NOT a new optimized ticket policy.

This script never fetches external odds, changes formal tickets, or enables bets.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path

import formal_portfolio_capture as capture
import portfolio_shadow as selector
from odds_integrity_audit import age_minutes, read_json

RULE_MIN_PROB = 5.0
RULE_MIN_EV = 1.10
RULE_MAX_PAIRS = 2
DISPLAY_WINDOW = 20


def pair_id(pair):
    a,b=pair["number1"],pair["number2"]
    if type(a) is not int or type(b) is not int or not 1<=a<b<=99:
        raise ValueError("noncanonical pair in frozen signal")
    return f"{a:02d}{b:02d}"


def numeric(value, name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError("invalid number: "+name)
    return float(value)


def validate_signal_pairs(evaluation):
    full=evaluation.get("validationPairs")
    display=evaluation.get("pairs")
    if not isinstance(full,list) or not isinstance(display,list):
        raise ValueError("signal requires display and full validation pair lists")
    identities={}
    for idx,row in enumerate(full,1):
        pid=pair_id(row)
        if pid in identities:
            raise ValueError("duplicate full signal pair")
        rank=row.get("practicalRank")
        if type(rank) is not int or rank!=idx:
            raise ValueError("full signal practical ranks not sorted")
        p=numeric(row.get("aiWideProbability"),"probability")
        odds=numeric(row.get("wideOddsLow"),"wideOddsLow")
        ev=numeric(row.get("conservativeExpectedValue"),"EV")
        score=numeric(row.get("practicalScore"),"practicalScore")
        if not 0<=p<=100 or odds<1 or ev<0 or score<0:
            raise ValueError("impossible signal odds/probability")
        if abs(ev-p/100*odds)>0.007:
            raise ValueError("unbound EV and price/probability")
        identities[pid]=row
    if len(display)!=min(DISPLAY_WINDOW,len(full)):
        raise ValueError("unexpected display pair window")
    if [pair_id(x) for x in display] != [pair_id(x) for x in full[:len(display)]]:
        raise ValueError("display window differs from full ranking prefix")
    return full,display,identities


def selected_ids(signal,rid,*,all_pairs=False):
    if not all_pairs:
        data=signal
    else:
        data=deepcopy(signal)
        w=data["wideEvaluation"]
        w["pairs"]=w["validationPairs"]
    return [
        leg["candidateID"] for leg in selector.choose_wides(
            rid,data,{},RULE_MIN_PROB,RULE_MIN_EV,limit=RULE_MAX_PAIRS)
    ]


def race_selection(record, aligned_race):
    signal=record["signal"]
    eval=signal.get("wideEvaluation") or {}
    if eval.get("status")!="live_odds_connected":
        raise ValueError("frozen Wide signal not live_odds_connected")
    full,display,index=validate_signal_pairs(eval)
    rid=aligned_race["raceID"]
    saved=[row["candidateID"] for row in aligned_race["commonWideReference"]]
    actual=selected_ids(signal,rid)
    all_pair_choice=selected_ids(signal,rid,all_pairs=True)
    if saved!=actual:
        raise ValueError("registered reference selection does not match saved signal")
    qualified=[
        row for row in full if
        row["aiWideProbability"] >= RULE_MIN_PROB and
        row["conservativeExpectedValue"] >= RULE_MIN_EV
    ]
    displayed_ids={pair_id(x) for x in display}
    hidden=[row for row in qualified if pair_id(row) not in displayed_ids]
    candidates=[{
        "candidateID":pair_id(row),
        "practicalRank":row["practicalRank"],
        "probabilityPct":row["aiWideProbability"],
        "conservativeExpectedValue":row["conservativeExpectedValue"],
        "oddsLow":row["wideOddsLow"],
        "practicalScore":row["practicalScore"],
        "marketWideProbabilityPct":row.get("marketWideProbability"),
        "aiToMarketProbabilityRatio":row.get("aiMarketProbabilityRatio"),
        "inTop20":pair_id(row) in displayed_ids,
        "inRegisteredReference":pair_id(row) in saved,
    } for row in qualified]
    source_age=age_minutes(eval.get("capturedAtJST"),
                           eval.get("officialDatetime"))
    return {
        "raceDate":aligned_race["raceDate"],
        "raceID":rid,
        "fullPairs":len(full),
        "displayPairs":len(display),
        "qualifyingFullPairs":len(qualified),
        "qualifyingDisplayPairs":len(qualified)-len(hidden),
        "qualifiedHiddenBeyondDisplay":len(hidden),
        "savedReferencePairs":saved,
        "allPairCounterfactualPairs":all_pair_choice,
        "top20WindowChangedSelectedPairs": saved!=all_pair_choice,
        "selectedPracticalRanks":[index[p]["practicalRank"] for p in saved],
        "sourceMetadataAgeMinutes":source_age,
        "sourceTimestampAuthenticated":False,
        "qualifiedCandidates":candidates,
    }


def summarize_candidates(rows):
    xs=list(rows)
    if not xs:
        return {"pairs":0}
    def avg(field):
        v=[numeric(r[field],field) for r in xs if r.get(field) is not None]
        return round(math.fsum(v)/len(v),4) if v else None
    return {
        "pairs":len(xs),
        "meanProbabilityPct":avg("probabilityPct"),
        "meanOddsLow":avg("oddsLow"),
        "meanConservativeEV":avg("conservativeExpectedValue"),
        "meanPracticalRank":avg("practicalRank"),
        "meanPracticalScore":avg("practicalScore"),
        "meanAiToMarketProbabilityRatio":avg("aiToMarketProbabilityRatio"),
        "withAIProbabilityHigherThanMarket":sum(
            r.get("aiToMarketProbabilityRatio") is not None and
            numeric(r["aiToMarketProbabilityRatio"],"aiToMarketProbabilityRatio")>1
            for r in xs),
    }


def audit(root, *, cohort=None):
    aligned=cohort if cohort is not None else read_json(root/"AlignedPortfolioReview.json")
    if aligned.get("projectionState")!="verified" or (
       aligned.get("aligned",{}).get("countsAreCurrent") is not True):
        raise ValueError("unverified aligned source; abort")
    from aligned_portfolio_review import protocol
    proto=protocol()
    params=proto["commonWideReference"]
    if (params.get("minProbabilityPct")!=RULE_MIN_PROB or
        params.get("minExpectedMultiplier")!=RULE_MIN_EV or
        params.get("maxPairs")!=RULE_MAX_PAIRS):
        raise ValueError("frozen registered reference thresholds changed")
    included=[]
    excluded=[]
    for row in aligned.get("races",[]):
        rid=row["raceID"]
        day=row["raceDate"]
        try:
            saved=capture.load(root,day,rid)
            found=race_selection(saved,row)
            included.append(found)
        except (ValueError,TypeError,KeyError,FileNotFoundError,
                json.JSONDecodeError) as err:
            excluded.append({"raceDate":day,"raceID":rid,
                             "reason":type(err).__name__+":"+str(err)[:130]})
    selected=[x for r in included for x in r["qualifiedCandidates"]
              if x["inRegisteredReference"]]
    unselected=[x for r in included for x in r["qualifiedCandidates"]
                if not x["inRegisteredReference"]]
    chosen_full=[x for r in included for x in r["allPairCounterfactualPairs"]]
    output={
        "schemaVersion":1,"mode":"preregistered_Wide_reference_selector_replay",
        "protocolSha256":"55ee84560014f5f4dec94996da103855c0d6a926f386973934922e894a293d79",
        "referenceRules":{"minProbabilityPct":RULE_MIN_PROB,
                          "minExpectedMultiplier":RULE_MIN_EV,
                          "maxPairs":RULE_MAX_PAIRS,
                          "displayTopK":DISPLAY_WINDOW},
        "alignedRaces":len(aligned.get("races",[])),
        "racesReplayed":len(included),
        "racesExcluded":len(excluded),
        "exclusions":excluded[:20],
        "qualifyingPairsInFullSignal":sum(x["qualifyingFullPairs"] for x in included),
        "qualifyingPairsOnDisplay":sum(x["qualifyingDisplayPairs"] for x in included),
        "qualifyingPairsHiddenBeyondTop20":sum(x["qualifiedHiddenBeyondDisplay"] for x in included),
        "racesWithHiddenQualifiedPairs":sum(x["qualifiedHiddenBeyondDisplay"]>0 for x in included),
        "racesWhereTop20ChangesReferences":sum(x["top20WindowChangedSelectedPairs"] for x in included),
        "top20ChangedExamples":[{
            "raceID":x["raceID"],"saved":x["savedReferencePairs"],
            "all":x["allPairCounterfactualPairs"],
        } for x in included if x["top20WindowChangedSelectedPairs"]][:15],
        "savedSelectedReferences":len(selected),
        "allFieldCounterfactualSelectedReferences":len(chosen_full),
        "selectedCandidateSummary":summarize_candidates(selected),
        "qualifiedButNotSelectedSummary":summarize_candidates(unselected),
        "metadataAgeMinutesRange":[
            min(x["sourceMetadataAgeMinutes"] for x in included
                if x["sourceMetadataAgeMinutes"] is not None),
            max(x["sourceMetadataAgeMinutes"] for x in included
                if x["sourceMetadataAgeMinutes"] is not None),
        ] if any(x["sourceMetadataAgeMinutes"] is not None for x in included) else None,
        "referenceSelectionIdentityVerified":len(excluded)==0,
        "readOnlyCounterfactualOnly":True,
        "profitabilityValidated":False,
        "independentQuoteTimingVerified":False,
        "newPurchaseRuleProposed":False,
        "productionModelChanged":False,
        "networkRequests":0,
    }
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path("."))
    args=p.parse_args()
    print(json.dumps(audit(args.root),ensure_ascii=False,indent=2,
                     allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
