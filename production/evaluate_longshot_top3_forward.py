"""Strict prospective evaluation of first-frozen longshot Top3 probabilities.

Only archived first-formal-T10 longshot snapshots are eligible, matched with
the ORIGINAL formal T10 race and officially matched results. No replays,
backfills, feature reconstruction, ticket signals, or model adoption.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path

import numpy as np

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium, paired_race_uncertainty
from market_shrink_shadow import JST, fingerprint
from longshot_top3_shadow import SHADOW_VERSION, path_for
from train_longshot_top3 import ROOT, METADATA, VERSION

ACTIVATION_DATE="2026-11-23"
OUTPUT=ROOT/"research/LongshotTop3ForwardReview.json"


def ensure(ok,msg):
    if not ok:
        raise ValueError("LONGSHOT_TOP3_FORWARD_BLOCKED: "+msg)


def evaluate(root,audit,meta,load_bundle=capture.load):
    a=audit.get("aligned") or {}
    ensure(audit.get("projectionState")=="verified" and
           a.get("countsAreCurrent") is True and
           audit.get("cohortLockRecoveryRequired") is not True and
           bool(audit.get("generatedAtJST")),"latest strict verified audit required")
    rows=audit.get("races")
    ensure(isinstance(rows,list) and len(rows)==a.get("racesObserved"),
           "strict audited cohort mismatch")
    indexed={(r["raceDate"],r["raceID"]):r for r in rows}
    ensure(len(indexed)==len(rows),"duplicate audited race identities")
    ensure(meta.get("version")==VERSION and
           meta.get("candidatePromotionApproved") is False and
           meta.get("autoBetEnabled") is False and
           meta.get("formalPredictionAIChanged") is False and
           meta.get("researchOnly") is True,
           "longshot research model policy invalid")
    expected={(r["raceDate"],r["raceID"]) for r in rows
              if r["raceDate"]>=ACTIVATION_DATE and r.get("inputFingerprint")}
    excluded={(r["raceDate"],r["raceID"]) for r in
              (audit.get("quality") or {}).get("resultExclusions",[])}
    counts={
        "firstFrozenShadowRaces":0,
        "eligibleAlignedFormalRacesAfterActivation":len(expected),
        "missingShadowRaces":None,
        "candidateCaptureCoveragePct":None,
        "matchedScorableRaces":0,
        "matchedUnderdogHorseRows":0,
        "matchedUnderdogTop3Rows":0,
        "unmatchedOrExcludedShadows":0,
        "noQualifyingLongshots":0,
        "unscorablePodium":0,
    }
    seen=set()
    race_errors=[]
    by_odds= {"10-29.9":{"n":0,"hits":0},
              "30+":{"n":0,"hits":0}}
    scores=[]
    top_hits={"formalAI":0,"T10Market":0,"longshotCandidate":0}
    top_podium_races=0
    positive_factors=Counter()
    for path in sorted((Path(root)/"reports").glob("*/longshot_top3_v1/*.json")):
        doc=json.loads(path.read_text(encoding="utf-8"))
        ident=(doc["raceDate"],doc["raceID"])
        ensure(ident not in seen and ident[0]>=ACTIVATION_DATE,
               "duplicate/retroactive shadow identity")
        seen.add(ident)
        ensure(path==path_for(root,{"raceDate":ident[0],"raceID":ident[1]}),
               "shadow path/identity mismatch")
        ensure(doc.get("version")==SHADOW_VERSION and
               doc.get("status")=="first_frozen_unapproved_research_only" and
               doc.get("researchOnly") is True and
               doc.get("candidatePromotionApproved") is False and
               doc.get("formalPredictionAIChanged") is False and
               doc.get("formalRecommendationsEnabled") is False and
               doc.get("autoBetEnabled") is False and
               doc.get("modelVersion")==meta["version"] and
               doc.get("modelSha256")==meta["modelSha256"] and
               doc.get("residualMultiplier2024")==meta["selectedResidualMultiplier"] and
               doc.get("popularityMinimum")==meta["popularityMinimum"] and
               doc.get("winOddsMinimum")==meta["winOddsMinimum"],
               "first-frozen candidate identity/policy mismatch")
        ensure(doc["recordFingerprint"]==fingerprint({
            k:v for k,v in doc.items() if k!="recordFingerprint"
        }),"candidate snapshot immutable digest mismatch")
        post=datetime.fromisoformat(ident[0]+"T"+doc["startTime"]).replace(tzinfo=JST)
        captured=datetime.fromisoformat(doc["capturedAtJST"])
        recorded=datetime.fromisoformat(doc["recordedAtJST"])
        ensure(captured.utcoffset()==timedelta(hours=9) and
               recorded.utcoffset()==timedelta(hours=9) and
               8<=(post-captured).total_seconds()/60<=12 and
               captured<=recorded<=post-timedelta(minutes=8),
               "shadow not recorded before formal T10 deadline")
        counts["firstFrozenShadowRaces"]+=1
        audited=indexed.get(ident)
        if audited is None or audited.get("resultState")!="matched" or ident in excluded:
            counts["unmatchedOrExcludedShadows"]+=1
            continue
        saved=load_bundle(root,*ident)
        ensure(saved.get("fingerprint")==audited["inputFingerprint"],
               "original formal input fingerprint mismatch")
        original=saved["prediction"]
        ensure(original.get("predictionTiming")=="live_10min" and
               original.get("captureQuality")=="formal_10min" and
               doc["capturedAtJST"]==original["capturedAtJST"] and
               doc["sourcePredictionSha256"]==fingerprint(original),
               "shadow not linked to original T10 race")
        horses=original["horses"]
        found=[h for h in horses
               if h.get("popularity") is not None and h.get("odds") is not None
               and h["popularity"]>=meta["popularityMinimum"]
               and h["odds"]>=meta["winOddsMinimum"]]
        candidates=doc["candidateHorses"]
        ensure([h["number"] for h in found]==[h["number"] for h in candidates] and
               doc["qualifyingLongshotCount"]==len(found) and
               doc["sourceEntrantCount"]==len(horses),
               "mismatch in frozen underdog eligibility")
        if not candidates:
            counts["noQualifyingLongshots"]+=1
            continue
        try:
            podium=official_podium(audited["actualFinish"],
                                   [h["number"] for h in horses],3,ident[1])
        except ValueError as exc:
            if "ambiguous podium dead heat" in str(exc):
                counts["unscorablePodium"]+=1
                continue
            raise
        if podium is None:
            counts["unscorablePodium"]+=1
            continue
        actual=np.asarray([int(h["number"] in podium) for h in candidates],dtype=float)
        probs={
            "formalAI":np.asarray([h["aiTop3Probability"] for h in found],dtype=float)/100,
            "T10Market":np.asarray([h["marketTop3Probability"] for h in found],dtype=float)/100,
            "longshotCandidate":np.asarray([h["researchTop3Pct"] for h in candidates],dtype=float)/100,
        }
        for key,prob in probs.items():
            ensure(np.isfinite(prob).all() and ((prob>=0)&(prob<=1)).all(),
                   "invalid frozen Top3 "+key)
        counts["matchedScorableRaces"]+=1
        counts["matchedUnderdogHorseRows"]+=len(candidates)
        counts["matchedUnderdogTop3Rows"]+=int(actual.sum())
        scores.append({key:float(np.mean((prob-actual)**2)) for key,prob in probs.items()})
        top_podium_races+=int(any(actual))
        for key,prob in probs.items():
            top_hits[key]+=int(actual[int(np.argmax(prob))])
        for j,h in enumerate(candidates):
            group="30+" if h["winOdds"]>=30 else "10-29.9"
            by_odds[group]["n"]+=1
            by_odds[group]["hits"]+=int(actual[j])
            if actual[j]:
                for assoc in h.get("positiveModelAssociations",[]):
                    positive_factors[assoc["feature"]]+=1
    missing=sorted(expected-seen)
    counts["missingShadowRaces"]=len(missing)
    counts["candidateCaptureCoveragePct"]=(
        round(100*len(expected&seen)/len(expected),2) if expected else None
    )
    ensure(counts["matchedScorableRaces"]<=a.get("matchedResultRaces"),
           "unjustified matched race count")
    means={
        key:float(np.mean([x[key] for x in scores])) if scores else None
        for key in top_hits
    }
    return {
        "schemaVersion":1,"version":"longshot-top3-forward-review-v1",
        "status":"insufficient_first_frozen_evidence"
                 if counts["matchedScorableRaces"]<100
                 else "first_frozen_descriptive_only",
        "sourceAuditGeneratedAtJST":audit["generatedAtJST"],
        "modelVersion":meta["version"],"modelSha256":meta["modelSha256"],
        "cohortActivationDate":ACTIVATION_DATE,
        "longshotDefinition":meta["longshotFilter"],
        "qualityIssuesInStrictAudit":len((audit.get("quality") or {}).get("issues") or []),
        "counts":counts,
        "missingShadowRaceIDs":[{"raceDate":a,"raceID":b} for a,b in missing[:40]],
        "top3BrierEqualRace":means,
        "pairedRaceCandidateMinusMarket":paired_race_uncertainty([
            x["longshotCandidate"]-x["T10Market"] for x in scores
        ]),
        "pairedRaceCandidateMinusFormalAI":paired_race_uncertainty([
            x["longshotCandidate"]-x["formalAI"] for x in scores
        ]),
        "topRankedLongshotTop3Hits":top_hits,
        "raceCountAtLeastOneLongshotInTop3":top_podium_races,
        "top3OutcomesByOdds":by_odds,
        "positiveFeatureAssociationsAmongActualLongshotTop3":[
            {"feature":feature,"count":count}
            for feature,count in positive_factors.most_common(12)
        ],
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "limitations":[
            "Only independently first-frozen formal T10 candidates, never historical replays.",
            "Candidates are defined by T10 odds and popularity; historical training used closing odds.",
            "This report shows predictive association, NOT why a horse actually outran expectations.",
            "Actual successful horse associations are post-outcome descriptive analysis.",
            "Risk/profit/payout across ticket types not measured or proven.",
            "No new automated picks, bets or model promotion.",
        ]
    }


def main():
    audit=json.loads((ROOT/"AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    if not METADATA.is_file():
        raise FileNotFoundError("no trained longshot research metadata")
    meta=json.loads(METADATA.read_text(encoding="utf-8"))
    report=evaluate(ROOT,audit,meta)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                 allow_nan=False)+"\n",encoding="utf-8")
    c=report["counts"]
    top_hits=report["topRankedLongshotTop3Hits"]
    lines=[
        "# Longshot Top3: first-frozen T10 future evaluation", "",
        "Status: "+report["status"],
        "Model: "+report["modelVersion"],
        "Verified audit: "+report["sourceAuditGeneratedAtJST"],
        f"Eligible formal races: {c['eligibleAlignedFormalRacesAfterActivation']}",
        f"First-frozen specialist captures: {c['firstFrozenShadowRaces']}",
        f"Capture coverage: {c['candidateCaptureCoveragePct']}%",
        f"Missing specialist captures: {c['missingShadowRaces']}",
        f"Matched races with unpopular runners: {c['matchedScorableRaces']}",
        f"Matched unpopular runners: {c['matchedUnderdogHorseRows']}",
        f"Underdogs actually finishing Top3: {c['matchedUnderdogTop3Rows']}", "",
        "| Metric | Original market | Current AI | Longshot specialist |",
        "|---|---:|---:|---:|",
        f"| Underdog Top3 Brier (equal-race) | "
        f"{report['top3BrierEqualRace']['T10Market']} | "
        f"{report['top3BrierEqualRace']['formalAI']} | "
        f"{report['top3BrierEqualRace']['longshotCandidate']} |",
        f"| Top longshot finishing Top3 | "
        f"{top_hits['T10Market']} | "
        f"{top_hits['formalAI']} | "
        f"{top_hits['longshotCandidate']} |",
        "", "Paired candidate minus T10 market: "
        +str(report["pairedRaceCandidateMinusMarket"]),
        "", "Paired candidate minus formal AI: "
        +str(report["pairedRaceCandidateMinusFormalAI"]),
        "", "Associations are NOT proof of the causes of an upset.",
        "No ROI, auto picks, model activation or betting.", "",
    ]
    OUTPUT.with_suffix(".md").write_text("\n".join(lines),encoding="utf-8")
    print("LONGSHOT_TOP3_FORWARD_REVIEW",report["status"],c)


if __name__=="__main__":
    main()
