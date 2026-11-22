"""Strict independent audit of immutable market-anchored first-T10 research.

Never calculate or backfill a candidate from old formal races; only score
already saved before-post immutable shadows on independently matched results.
"""
from __future__ import annotations
from datetime import datetime,timedelta
import json
from pathlib import Path

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium, paired_race_uncertainty
from market_shrink_shadow import JST, fingerprint
from market_anchor_shadow import (
    VERSION, output_path, verify_strategy, STRATEGY_PATH,
)
from no_current_odds_shadow import shadow_path as no_odds_path
from train_market_anchored_challenger import ROOT

ACTIVATION_DATE = "2026-11-23"
EVENTS = {
    "win": ("aiProbability","marketProbability","winPct",1),
    "top2": ("aiTop2Probability","marketTop2Probability","top2Pct",2),
    "top3": ("aiTop3Probability","marketTop3Probability","top3Pct",3),
}
STRATEGIES = ("marketCalibrated","historyAugmented")
OUTPUT = ROOT / "research/MarketAnchoredForwardReview.json"


def ensure(ok,why):
    if not ok:
        raise ValueError("MARKET_ANCHORED_FORWARD_BLOCKED: "+why)


def evaluate(root,audit,strategy,sha,load_bundle=capture.load):
    aligned=audit.get("aligned") or {}
    ensure(audit.get("projectionState")=="verified" and
           aligned.get("countsAreCurrent") is True and
           audit.get("cohortLockRecoveryRequired") is not True,
           "latest verified audit required")
    rows=audit.get("races")
    ensure(isinstance(rows,list) and len(rows)==aligned.get("racesObserved"),
           "audited cohort identity/size mismatch")
    indexed={(x["raceDate"],x["raceID"]):x for x in rows}
    ensure(len(indexed)==len(rows),"duplicate audited race identities")
    expected={
        (x["raceDate"],x["raceID"])
        for x in rows if x["raceDate"]>=ACTIVATION_DATE and x.get("inputFingerprint")
    }
    excluded={
        (x["raceDate"],x["raceID"])
        for x in (audit.get("quality") or {}).get("resultExclusions",[])
    }
    seen=set()
    grouped={e:{name:[] for name in STRATEGIES} for e in EVENTS}
    count={"firstFrozenSnapshots":0,"pairedMatchedWithCalibrated":0,
           "pairedMatchedWithHistory":0,"unmatchedOrExcludedSnapshots":0,
           "eligibleAlignedSinceActivation":len(expected),
           "missingCandidateSinceActivation":None,
           "candidateCaptureCoveragePct":None}
    for path in sorted((Path(root)/"reports").glob("*/market_anchored_v1/*.json")):
        v=json.loads(path.read_text(encoding="utf-8"))
        identity=(v["raceDate"],v["raceID"])
        ensure(identity not in seen and identity[0]>=ACTIVATION_DATE,
               "duplicate/too-early market-anchored shadow")
        seen.add(identity)
        ensure(path==output_path(root,{"raceDate":identity[0],"raceID":identity[1]}),
               "shadow path differs from original identity")
        ensure(v.get("version")==VERSION and
               v.get("status")=="first_frozen_future_research_only" and
               v.get("researchOnly") is True and
               v.get("candidatePromotionApproved") is False and
               v.get("formalPredictionAIChanged") is False and
               v.get("formalRecommendationsEnabled") is False and
               v.get("autoBetEnabled") is False and
               v.get("strategySHA256")==sha and
               v.get("strategyVersion")==strategy["version"] and
               v.get("independentHistoricalModelSHA256")==strategy["independentModelSHA256"],
               "research-only original strategy/version mismatch")
        ensure(v.get("recordFingerprint")==fingerprint({
            k:val for k,val in v.items() if k!="recordFingerprint"
        }),"shadow fingerprint mismatch")
        post=datetime.fromisoformat(v["raceDate"]+"T"+v["startTime"]).replace(tzinfo=JST)
        captured=datetime.fromisoformat(v["capturedAtJST"])
        recorded=datetime.fromisoformat(v["recordedAtJST"])
        ensure(captured.utcoffset()==timedelta(hours=9) and
               recorded.utcoffset()==timedelta(hours=9) and
               8<=(post-captured).total_seconds()/60<=12 and
               captured<=recorded<=post-timedelta(minutes=8),
               "shadow capture was not prospective T10")
        forecast=v["candidateHorsesByStrategy"]
        ensure("marketCalibrated" in forecast and
               set(forecast).issubset(set(STRATEGIES)),
               "missing or unexpected strategy")
        if "historyAugmented" in forecast:
            saved=no_odds_path(root,{"raceDate":identity[0],"raceID":identity[1]})
            ensure(saved.is_file(),"missing original independent no-odds snapshot")
            original_hist=json.loads(saved.read_text(encoding="utf-8"))
            ensure(original_hist.get("recordFingerprint")==
                   v.get("sourceNoOddsShadowFingerprint") and
                   original_hist.get("recordFingerprint")==fingerprint({
                       k:val for k,val in original_hist.items() if k!="recordFingerprint"
                   }),"invalid independent history provenance")
            ensure(v.get("independentHistoricalStatus")==
                   "valid_first_frozen_no_odds_snapshot",
                   "historical model not from original capture")
        count["firstFrozenSnapshots"]+=1
        audit_row=indexed.get(identity)
        if not audit_row or audit_row.get("resultState")!="matched" or identity in excluded:
            count["unmatchedOrExcludedSnapshots"]+=1
            continue
        bundled=load_bundle(root,*identity)
        ensure(bundled["fingerprint"]==audit_row["inputFingerprint"],
               "formal portfolio fingerprint mismatched")
        formal=bundled["prediction"]
        ensure(formal.get("predictionTiming")=="live_10min" and
               formal.get("captureQuality")=="formal_10min" and
               v["sourcePredictionSha256"]==fingerprint(formal) and
               v["capturedAtJST"]==formal["capturedAtJST"],
               "candidate not linked to same original formal T10 prediction")
        numbers=[h["number"] for h in formal["horses"]]
        ensure(len(numbers)>=3 and len(numbers)==len(set(numbers)),
               "invalid original entrants")
        for name, entries in forecast.items():
            ensure([x["number"] for x in entries]==numbers,
                   "candidate entrant mismatch")
        positions=audit_row.get("actualFinish")
        ensure(isinstance(positions,dict),"official result missing")
        counted=False
        for event,(formal_key,market_key,candidate_key,places) in EVENTS.items():
            try:
                podium=official_podium(positions,numbers,places,identity[1])
            except ValueError as exc:
                if "ambiguous podium dead heat" in str(exc):
                    continue
                raise
            if podium is None:
                continue
            for name,entries in forecast.items():
                scores={"formalAI":0.0,"T10Market":0.0,"candidate":0.0}
                for horse,pred in zip(formal["horses"],entries):
                    actual=int(horse["number"] in podium)
                    for score_name,value in (
                        ("formalAI",horse[formal_key]),
                        ("T10Market",horse[market_key]),
                        ("candidate",pred[candidate_key]),
                    ):
                        ensure(type(value) in (int,float) and 0<=value<=100,
                               "invalid frozen event probabilities")
                        scores[score_name]+=(value/100.0-actual)**2/len(numbers)
                grouped[event][name].append(scores)
            if event=="win":
                counted=True
        if counted:
            count["pairedMatchedWithCalibrated"]+=1
            if "historyAugmented" in forecast:
                count["pairedMatchedWithHistory"]+=1
    missing=sorted(expected-seen)
    count["missingCandidateSinceActivation"]=len(missing)
    count["candidateCaptureCoveragePct"]=(
        round(100*len(expected & seen)/len(expected),2) if expected else None
    )
    ensure(count["pairedMatchedWithCalibrated"]<=aligned.get("matchedResultRaces"),
           "paired counts exceed verified results")
    summaries={}
    for event,variants in grouped.items():
        summaries[event]={}
        for name,samples in variants.items():
            summaries[event][name]={
                "pairedRaces":len(samples),
                "meanBrier":{
                    key:float(sum(x[key] for x in samples)/len(samples))
                    if samples else None
                    for key in ("formalAI","T10Market","candidate")
                },
                "candidateMinusFormalAI":paired_race_uncertainty([
                    x["candidate"]-x["formalAI"] for x in samples
                ]),
                "candidateMinusMarket":paired_race_uncertainty([
                    x["candidate"]-x["T10Market"] for x in samples
                ]),
            }
    return {
        "schemaVersion":1,"version":"market-anchored-forward-review-v1",
        "status":"insufficient_future_evidence" if count["pairedMatchedWithCalibrated"]<100
                 else "prospective_descriptive_only",
        "sourceAuditGeneratedAtJST":audit["generatedAtJST"],
        "candidateActivationDate":ACTIVATION_DATE,
        "strategySHA256":sha,
        "strategyVersion":strategy["version"],
        "qualityIssueCount":len((audit.get("quality") or {}).get("issues") or []),
        "counts":count,
        "missingCaptureRaceIDs":[{"raceDate":x[0],"raceID":x[1]} for x in missing[:40]],
        "events":summaries,
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "limitations":[
            "Only first-frozen futures recorded independently before start time.",
            "Market calibration coefficients chosen on 2024 closing odds, not T10.",
            "Independent 2% history method only scored when its original separate no-odds shadow was frozen in the same T10 window.",
            "Missing shadows cannot be reconstructed from later results.",
            "Whole-race bootstrap uncertainty; no claim of positive profit or model promotion.",
        ],
    }


def main():
    audit=json.loads((ROOT/"AlignedPortfolioReview.json").read_text(encoding="utf-8"))
    strategy,sha=verify_strategy()
    report=evaluate(ROOT,audit,strategy,sha)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                 allow_nan=False)+"\n",encoding="utf-8")
    c=report["counts"]
    lines=[
        "# Market-anchored prospective first-T10 research", "",
        "Status: "+report["status"],
        "Audit: "+report["sourceAuditGeneratedAtJST"],
        f"First-frozen market snapshots: {c['firstFrozenSnapshots']}",
        f"Matched market-calibrated: {c['pairedMatchedWithCalibrated']}",
        f"Matched history-augmented: {c['pairedMatchedWithHistory']}",
        f"Expected aligned formal captures: {c['eligibleAlignedSinceActivation']}",
        f"Missing market-anchor shadows: {c['missingCandidateSinceActivation']}",
        f"Candidate capture coverage: {c['candidateCaptureCoveragePct']}%", "",
        "| Event | Strategy | Paired Races | Formal AI Brier | T10 Market Brier | Candidate Brier | Candidate - AI | Race CI 95% |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for event in ("win","top2","top3"):
        for name in STRATEGIES:
            s=report["events"][event][name]
            b=s["meanBrier"]
            d=s["candidateMinusFormalAI"]
            lines.append(f"| {event} | {name} | {s['pairedRaces']} | "
                         f"{b['formalAI']} | {b['T10Market']} | "
                         f"{b['candidate']} | {d['meanBrierDelta']} | "
                         f"{d['bootstrap95Pct']} |")
    lines+=["", "A numeric improvement is not proof of a generalizable advantage.",
             "Model adoption and bets remain disabled.", ""]
    OUTPUT.with_suffix(".md").write_text("\n".join(lines),encoding="utf-8")
    print("MARKET_ANCHORED_FORWARD_REVIEW",report["status"],c)


if __name__=="__main__":
    main()
