"""Explain frozen Wide candidate gates without tuning or new market data.

Partition every T-10 Wide pair by the already-registered probability >=5%
and conservative EV >=1.10 flags. Check outcome calibration for each quadrant
using the SAME frozen signal probability family. A selection association is
not a causal estimate or proof of stale odds. Results are research-only.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from itertools import combinations
import json
import math
from pathlib import Path

import formal_portfolio_capture as capture
from odds_integrity_audit import read_json
from wide_reference_selector_replay import (
    RULE_MIN_PROB,RULE_MIN_EV,numeric,validate_signal_pairs,pair_id,
)

GROUP_LABELS=("p>=5_ev>=1.1","p>=5_ev<1.1","p<5_ev>=1.1","p<5_ev<1.1")


def gate_label(probability_pct, conservative_ev):
    p=numeric(probability_pct,"probability")
    ev=numeric(conservative_ev,"conservativeEV")
    if not 0<=p<=100 or ev<0:
        raise ValueError("invalid probability or EV for gate")
    return ("p>=5" if p>=RULE_MIN_PROB else "p<5")+(
        "_ev>=1.1" if ev>=RULE_MIN_EV else "_ev<1.1")


def qualified_pair_outcomes(signal, result, horses):
    if result.get("resultState")!="matched":
        return None,"not_matched"
    finish=result.get("actualFinish")
    if not isinstance(finish,dict) or not isinstance(horses,list):
        return None,"missing_finish_or_entrants"
    numbers=[h["number"] for h in horses]
    if (len(numbers)<3 or len(set(numbers))!=len(numbers)
            or any(type(n) is not int or n<=0 for n in numbers)):
        return None,"invalid_horses"
    positions={n:finish.get(str(n)) for n in numbers}
    if any(type(p) is not int or p<=0 for p in positions.values()):
        return None,"incomplete_finish"
    if sum(p<=3 for p in positions.values())!=3:
        return None,"invalid_podium"
    evaluation=signal.get("wideEvaluation") or {}
    full,_,_=validate_signal_pairs(evaluation)
    keys={pair_id(row) for row in full}
    expected={f"{a:02d}{b:02d}" for a,b in combinations(sorted(numbers),2)}
    if keys!=expected:
        raise ValueError("full Wide signal pair coverage mismatch")
    out=[]
    for row in full:
        key=pair_id(row)
        a,b=int(key[:2]),int(key[2:])
        p=numeric(row.get("aiWideProbability"),"aiWideProbability")
        ev=numeric(row.get("conservativeExpectedValue"),"EV")
        odds=numeric(row.get("wideOddsLow"),"Wide odds")
        market=row.get("marketWideProbability")
        market_p=(numeric(market,"marketWideProbability")
                  if market is not None else None)
        out.append({
            "raceDate":result["raceDate"],"raceID":result["raceID"],
            "candidateID":key,
            "probability":p/100,
            "EV":ev,
            "oddsLow":odds,
            "marketWideProbabilityPct":market_p,
            "gate":gate_label(p,ev),
            "hit":positions[a]<=3 and positions[b]<=3,
        })
    return out,None


def statistics(rows):
    xs=list(rows)
    count=len(xs)
    exp=math.fsum(x["probability"] for x in xs)
    hits=sum(x["hit"] for x in xs)
    market_values=[x for x in xs if x["marketWideProbabilityPct"] is not None]
    model_not_higher=sum(
        100*x["probability"] <= x["marketWideProbabilityPct"]
        for x in market_values
    )
    return {
        "pairs":count,
        "expectedHits":round(exp,4),
        "observedHits":hits,
        "observedToExpectedRatio":round(hits/exp,4) if exp else None,
        "averageProbabilityPct":round(100*exp/count,3) if count else None,
        "observedHitRatePct":round(100*hits/count,3) if count else None,
        "aiProbabilityNoHigherThanWinDerivedMarketCount":model_not_higher,
        "marketProbabilitySampleSize":len(market_values),
    }


def report(root, cohort=None):
    aligned=cohort if cohort is not None else read_json(root/"AlignedPortfolioReview.json")
    if aligned.get("projectionState")!="verified" or (
            aligned.get("aligned",{}).get("countsAreCurrent") is not True):
        raise ValueError("unverified aligned projection")
    from aligned_portfolio_review import protocol
    params=protocol()["commonWideReference"]
    if (params["minProbabilityPct"]!=RULE_MIN_PROB
            or params["minExpectedMultiplier"]!=RULE_MIN_EV):
        raise ValueError("registered selection gate has changed")
    rows=[]
    excluded=Counter()
    audited=0
    for race in aligned.get("races",[]):
        day=race["raceDate"]
        rid=race["raceID"]
        try:
            record=capture.load(root,day,rid)
            candidates,reason=qualified_pair_outcomes(
                record["signal"],race,record["prediction"]["horses"])
            if reason:
                excluded[reason]+=1
                continue
            rows.extend(candidates)
            audited+=1
        except (ValueError,KeyError,TypeError,FileNotFoundError,
                json.JSONDecodeError) as err:
            excluded["invalid_file_or_gate:"+type(err).__name__]+=1
    groups={name:statistics(x for x in rows if x["gate"]==name)
            for name in GROUP_LABELS}
    pge=[x for x in rows if x["probability"]*100 >= RULE_MIN_PROB]
    plo=[x for x in rows if x["probability"]*100 < RULE_MIN_PROB]
    by_date=[]
    for date in sorted({x["raceDate"] for x in rows}):
        group=[x for x in rows if x["raceDate"]==date]
        by_date.append({
            "raceDate":date,
            "races":len({x["raceID"] for x in group}),
            **{k:statistics(x for x in group if x["gate"]==k)
               for k in GROUP_LABELS},
        })
    return {
        "schemaVersion":1,
        "mode":"frozen_wide_probability_and_price_threshold_gate",
        "formalGate":"aligned_verified_counts_current",
        "registeredRules":{"minProbabilityPct":RULE_MIN_PROB,
                           "minConservativeEV":RULE_MIN_EV},
        "racesAudited":audited,
        "racesExcluded":sum(excluded.values()),
        "excludedReasons":dict(sorted(excluded.items())),
        "allPairs":statistics(rows),
        "probabilityAtLeastFivePct":statistics(pge),
        "probabilityBelowFivePct":statistics(plo),
        "twoByTwoGates":groups,
        "byRaceDate":by_date,
        "marketProbabilitySource":"win_derived_proxy_not_independent_Wide_quote",
        "metadataTimestampSemanticsVerified":False,
        "sourcePriceFreshnessVerified":False,
        "selectionCausalEffectEstablished":False,
        "noRetrospectiveRuleOptimisation":True,
        "formalRecommendationsChanged":False,
        "liveBetsEnabled":False,
        "modelFilesChanged":False,
        "networkRequests":0,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    args=parser.parse_args()
    print(json.dumps(report(args.root),ensure_ascii=False,indent=2,
                     allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
