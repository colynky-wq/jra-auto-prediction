"""Freeze isolated longshot Top3 predictions ONLY at original formal T-10 time.

Prediction uses prior-form-only 36-feature matrix and the market Top3
probability available at that T-10 snapshot. Feature contributions indicate
model associations, NOT causes of an upset. No backfills, no real betting.
"""
from __future__ import annotations
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from market_shrink_shadow import JST, fingerprint, _write_first
from train_longshot_top3 import (
    VERSION, METADATA, MODEL_PATH, FEATURES, WEIGHTS,
    POPRANK_MIN, WINODDS_MIN, odds_underdog, infer_without_results,
)

SHADOW_VERSION="longshot-top3-first-T10-v1-20261123"
LABELS={
    "1着率":"過去5走の1着率",
    "前走上がり差f":"前走の末脚",
    "前走着順率f":"前走の着順",
    "前走秒差f":"前走の着差",
    "前走人気f":"前走の人気",
    "同芝ダ平均":"同じ芝・ダートの過去成績",
    "同距離帯平均":"近い距離の過去成績",
    "同距離帯回数c":"近い距離の出走経験",
    "点数差":"過去レースからの能力評価",
    "脚質初角":"過去の先行・追走傾向",
    "騎手勝率":"騎手の最近の勝率",
    "調教師勝率":"調教師の最近の勝率",
    "斤量変化":"斤量の変化",
    "枠位置":"枠順",
    "距離変化":"距離の変更",
    "昇級":"昇級",
    "降級":"降級",
}


def path_for(root,race):
    return (Path(root)/"reports"/str(race["raceDate"]).replace("-","")/
            "longshot_top3_v1"/(str(race["raceID"])+".json"))


def verify_candidate():
    if not METADATA.is_file() or not MODEL_PATH.is_file():
        raise FileNotFoundError("longshot research model not published")
    meta=json.loads(METADATA.read_text(encoding="utf-8"))
    raw=MODEL_PATH.read_bytes()
    if (meta.get("version")!=VERSION or
            meta.get("modelSha256")!=hashlib.sha256(raw).hexdigest() or
            meta.get("features")!=list(FEATURES) or
            meta.get("featureCount")!=len(FEATURES) or
            meta.get("selectedResidualMultiplier") not in WEIGHTS or
            meta.get("popularityMinimum")!=POPRANK_MIN or
            meta.get("winOddsMinimum")!=WINODDS_MIN or
            meta.get("refitThroughFor2026Test")!="2025-12-31" or
            meta.get("researchOnly") is not True or
            meta.get("candidatePromotionApproved") is not False or
            meta.get("autoBetEnabled") is not False or
            meta.get("formalPredictionAIChanged") is not False):
        raise ValueError("research-only longshot candidate policy/identity changed")
    model=lgb.Booster(model_file=str(MODEL_PATH))
    if model.feature_name()!=list(FEATURES):
        raise ValueError("longshot model feature identity mismatch")
    return meta,model


def build(race, features,meta,model,*,clock=lambda:datetime.now(JST)):
    if (race.get("predictionTiming")!="live_10min" or
            race.get("captureQuality")!="formal_10min"):
        raise ValueError("formal first-T10 prediction required")
    date=str(race["raceDate"]);rid=str(race["raceID"])
    post=datetime.fromisoformat(date+"T"+str(race["startTime"])).replace(tzinfo=JST)
    captured=datetime.fromisoformat(str(race["capturedAtJST"]))
    now=clock().astimezone(JST)
    if (captured.utcoffset()!=timedelta(hours=9) or
            now.utcoffset()!=timedelta(hours=9) or
            captured.date().isoformat()!=date or
            not 8 <= (post-captured).total_seconds()/60 <=12 or
            not captured<=now<=post-timedelta(minutes=8)):
        raise ValueError("first-T10 capture window expired")
    if (meta.get("version")!=VERSION or
            meta.get("features")!=list(FEATURES) or
            meta.get("selectedResidualMultiplier") not in WEIGHTS or
            meta.get("candidatePromotionApproved") is not False):
        raise ValueError("candidate model not pre-registered")
    horses=race.get("horses")
    if not isinstance(horses,list) or len(horses)<3 or not isinstance(features,dict):
        raise ValueError("incomplete original frozen race features")
    numbers=[h.get("number") for h in horses]
    if (any(type(x) is not int or x<1 for x in numbers) or
            len(numbers)!=len(set(numbers)) or
            features.get("featureNames")!=list(FEATURES) or
            features.get("horseNumbers")!=numbers):
        raise ValueError("entrant ordering/feature schema mismatch")
    rows=features.get("rows")
    if (not isinstance(rows,list) or len(rows)!=len(numbers) or
            any(not isinstance(x,list) or len(x)!=len(FEATURES) for x in rows)):
        raise ValueError("incomplete original 36-feature inputs")
    X=pd.DataFrame(rows,columns=list(FEATURES),dtype=np.float32)
    if not np.isfinite(X.to_numpy()).all():
        raise ValueError("invalid source features")
    pop=np.array([h.get("popularity") for h in horses],dtype=float)
    odds=np.array([h.get("odds") for h in horses],dtype=float)
    mask=odds_underdog(pop,odds)
    indices=np.flatnonzero(mask)
    weight=meta["selectedResidualMultiplier"]
    predicted=[]
    if len(indices):
        base=np.array([horses[i].get("marketTop3Probability") for i in indices],
                      dtype=float)/100
        current=np.array([horses[i].get("aiTop3Probability") for i in indices],
                         dtype=float)/100
        if not (np.isfinite(current).all() and
                ((current>=0)&(current<=1)).all()):
            raise ValueError("invalid existing formal AI Top3")
        x=X.iloc[indices].reset_index(drop=True)
        candidate=infer_without_results(x,base,model,weight)
        if weight>0:
            contrib=np.asarray(model.predict(x,pred_contrib=True,
                                                validate_features=True),
                               dtype=float)
            raw=np.asarray(model.predict(x,raw_score=True,
                                            validate_features=True),dtype=float)
            if (contrib.shape!=(len(indices),len(FEATURES)+1) or
                    not np.isfinite(contrib).all() or
                    not np.allclose(contrib.sum(axis=1),raw,rtol=1e-5,atol=1e-5)):
                raise ValueError("local longshot factor attribution mismatch")
        else:
            contrib=np.zeros((len(indices),len(FEATURES)+1))
        for j,i in enumerate(indices):
            positives=sorted(
                ((f,weight*float(contrib[j,k])) for k,f in enumerate(FEATURES)
                 if weight*float(contrib[j,k])>0),
                key=lambda x:-x[1]
            )
            negatives=sorted(
                ((f,weight*float(contrib[j,k])) for k,f in enumerate(FEATURES)
                 if weight*float(contrib[j,k])<0),
                key=lambda x:x[1]
            )
            def factors(pairs):
                return [{"feature":feature,"label":LABELS.get(feature,feature),
                         "modelLogOddsContribution":round(value,6)}
                        for feature,value in pairs[:3]]
            predicted.append({
                "number":numbers[i], "name":str(horses[i].get("name") or ""),
                "popularity":int(pop[i]),"winOdds":float(odds[i]),
                "marketTop3Pct":round(float(base[j])*100,6),
                "formalAITop3Pct":round(float(current[j])*100,6),
                "researchTop3Pct":round(float(candidate[j])*100,6),
                "researchMinusMarketPercentagePoints":round(
                    float((candidate[j]-base[j])*100),6),
                "positiveModelAssociations":factors(positives),
                "negativeModelAssociations":factors(negatives),
            })
    record={
        "schemaVersion":1,"version":SHADOW_VERSION,
        "status":"first_frozen_unapproved_research_only",
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "formalRecommendationsEnabled":False,
        "raceDate":date,"raceID":rid,"startTime":race["startTime"],
        "capturedAtJST":race["capturedAtJST"],
        "recordedAtJST":now.isoformat(),
        "predictionTiming":"live_10min",
        "sourcePredictionSha256":fingerprint(race),
        "featureInputSha256":hashlib.sha256(json.dumps(
            features,ensure_ascii=False,sort_keys=True,
            separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest(),
        "modelVersion":meta["version"],"modelSha256":meta["modelSha256"],
        "residualMultiplier2024":weight,
        "popularityMinimum":POPRANK_MIN,"winOddsMinimum":WINODDS_MIN,
        "sourceEntrantCount":len(numbers),"qualifyingLongshotCount":len(indices),
        "candidateHorses":predicted,
        "attributionWarning":"Predictive associations only, NOT proven causes or future profits.",
    }
    record["recordFingerprint"]=fingerprint(record)
    return record


def record_best_effort(root,race,features,*,first_formal_capture,
                       clock=lambda:datetime.now(JST)):
    try:
        path=path_for(root,race)
        if path.exists():
            old=json.loads(path.read_text(encoding="utf-8"))
            if (old.get("sourcePredictionSha256")!=fingerprint(race) or
                    old.get("recordFingerprint")!=fingerprint({
                        k:v for k,v in old.items() if k!="recordFingerprint"
                    })):
                raise ValueError("immutable longshot snapshot conflict")
            return {"status":"already_frozen","path":str(path)}
        if not first_formal_capture:
            return {"status":"missing_first_capture_no_backfill","path":None}
        if not METADATA.is_file() or not MODEL_PATH.is_file():
            return {"status":"candidate_not_trained_yet","path":None}
        meta,model=verify_candidate()
        content=build(race,features,meta,model,clock=clock)
        _write_first(path,content)
        return {"status":"first_frozen","qualifying":content["qualifyingLongshotCount"],
                "path":str(path)}
    except Exception as exc:
        return {"status":"error_no_formal_impact","path":None,
                "errorType":type(exc).__name__,"reason":str(exc)[:200]}
