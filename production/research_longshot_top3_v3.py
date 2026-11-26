"""V3: compare low-dimensional and full linear probability corrections.
Exploratory 2025/26 evaluation, never a prospective or betting result.
Feature hypothesis comes from already examined years; selection of penalty
and specification uses only 2024. No production model writes.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

from train_longshot_top3 import (prepare_longshot, split_longshot, training_targets,
                                 logit, ROOT, HISTORY, JST, FEATURES, ensure)
from train_no_current_odds_challenger import prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from research_longshot_top3_v2 import (add_extras, place_history, predict as v2_predict,
    FEATURE_SETS, OUTPUT as V2_OUTPUT, VERSION as V2_VERSION)
from evaluate_no_current_odds_longshot_2026 import paired_hit_ci

OUTPUT = ROOT / "research/model_candidates/longshot_top3_v3"
FOCUS = ("点数差","同距離帯回数c","同芝ダ平均","同芝ダ回数c","出走数c")
SPECS = {"calibration": (), "focused5": FOCUS, "full36": tuple(FEATURES)}
PENALTIES = (.0001,.001,.01,.1)
VERSION = "longshot-top3-v3-regularized-market-20261124"


def inputs(d, features):
    a=np.column_stack([logit(d["市場3着内基準"].to_numpy(float)),
                       d.loc[:,list(features)].to_numpy(float)])
    ensure(np.isfinite(a).all(),"invalid pre-race input")
    return a


def fit(d, name, penalty):
    ensure(name in SPECS and penalty in PENALTIES,"unregistered specification")
    ensure(pd.to_datetime(d["日付"]).dt.year.le(2025).all(),"2026 forbidden in training")
    a=inputs(d,SPECS[name]); y,b=training_targets(d)
    mu=a.mean(axis=0); sd=a.std(axis=0); sd=np.where(sd>1e-8,sd,1.)
    X=np.column_stack([np.ones(len(a)),(a-mu)/sd])
    offset=logit(b)
    def objective(beta):
        z=offset+X@beta
        loss=np.mean(np.logaddexp(0,z)-y*z)+.5*penalty*np.sum(beta[1:]**2)
        grad=X.T@(expit(z)-y)/len(y)
        grad[1:]+=penalty*beta[1:]
        return loss,grad
    opt=minimize(objective,np.zeros(X.shape[1]),method="L-BFGS-B",jac=True,
                 options={"maxiter":300,"ftol":1e-11})
    ensure(opt.success and np.isfinite(opt.x).all(),"optimizer failed")
    return {"spec":name,"features":list(SPECS[name]),"penalty":penalty,
            "mean":mu.tolist(),"scale":sd.tolist(),"coefficients":opt.x.tolist()}


def predict(d, model):
    name=model["spec"]
    ensure(model["features"]==list(SPECS[name]),"feature identity changed")
    a=inputs(d,SPECS[name])
    X=np.column_stack([np.ones(len(a)),(a-np.asarray(model["mean"]))/np.asarray(model["scale"])])
    p=expit(logit(d["市場3着内基準"].to_numpy(float))+X@np.asarray(model["coefficients"]))
    ensure(np.isfinite(p).all(),"invalid probability")
    return p


def equal_brier(d,p):
    return float(pd.Series((np.asarray(p)-d["着順"].le(3).to_numpy(int))**2)
                 .groupby(d["レースID"].astype(str).reset_index(drop=True)).mean().mean())


def select(train,valid):
    ensure(pd.to_datetime(train["日付"]).dt.year.le(2023).all()
           and pd.to_datetime(valid["日付"]).dt.year.eq(2024).all(),"selection boundary")
    models={};grid=[]
    for name in SPECS:
        for penalty in PENALTIES:
            m=fit(train,name,penalty)
            grid.append({"spec":name,"penalty":penalty,"brier":equal_brier(valid,predict(valid,m))})
            models[(name,penalty)]=m
    # Include doing nothing. If none beat market, do not use a correction.
    grid.append({"spec":"market","penalty":None,"brier":equal_brier(valid,valid["市場3着内基準"])})
    chosen=min(grid,key=lambda r:(r["brier"],len(SPECS.get(r["spec"],())),r["spec"]))
    best_by_spec={name:min((r for r in grid if r["spec"]==name),key=lambda r:r["brier"])
                  for name in SPECS}
    return chosen,best_by_spec,grid,models


def compare(d,methods):
    data=d[["レースID","日付","馬番","着順"]].reset_index(drop=True).copy()
    data["hit"]=data["着順"].le(3).astype(int)
    for name,p in methods.items():
        ensure(len(p)==len(data) and np.isfinite(p).all() and ((p>=0)&(p<=1)).all(),"invalid scores")
        data[name]=p
    out={};rows=[]
    for name,p in methods.items():
        out[name]={"equalRaceBrier":equal_brier(d,p),"picks":{str(k):{"selected":0,"hits":0,"racesHit":0}
                                                               for k in (1,2,3)}}
    for _,g in data.groupby("レースID",sort=False):
        row={"date":str(g["日付"].iloc[0].date())}
        for name in methods:
            ranked=g.sort_values([name,"馬番"],ascending=[False,True])
            for k in (1,2,3):
                selected=ranked.head(k);hits=int(selected["hit"].sum());s=out[name]["picks"][str(k)]
                s["selected"]+=len(selected);s["hits"]+=hits;s["racesHit"]+=int(hits>0)
                row[f"{name}_{k}"]=int(hits>0)
        rows.append(row)
    n=len(rows)
    for name in methods:
        for k in (1,2,3):
            s=out[name]["picks"][str(k)]
            s["precision"]=s["hits"]/s["selected"]
            s["raceHitRate"]=s["racesHit"]/n
            s["pairedRaceHitDeltaVsMarket"]=s["raceHitRate"]-out["market"]["picks"][str(k)]["racesHit"]/n
            s["wholeDateBootstrap95"]=paired_hit_ci(rows,name,k) if name!="market" else None
    return {"races":n,"horses":len(data),"actualPodium":int(data["hit"].sum()),"methods":out}


def main():
    hist=pd.read_parquet(HISTORY)
    safe=hist.copy()
    safe["馬体重"]=pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    old=prepare_longshot(prepare_history(safe))
    train,valid,test25=split_longshot(old)
    chosen,bests,grid,models=select(train,valid)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    protocol={"chosen":chosen,"bestBySpec":bests,"grid2024":grid,
        "selectionRule":"minimum equally weighted race Brier; market included",
        "trainingThrough":"2023-12-31","selectionYear":2024}
    (OUTPUT/"selection2024.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("V3_SELECTION_LOCKED",json.dumps(chosen),flush=True)
    p25={"market":test25["市場3着内基準"].to_numpy(float)}
    for name,r in bests.items():
        p25[name]=predict(test25,models[(name,r["penalty"])])
    p25["v3Selected"]=p25[chosen["spec"]]
    score25=compare(test25,p25)
    full=pd.concat([train,valid,test25],ignore_index=True)
    final={name:fit(full,name,r["penalty"]) for name,r in bests.items()}
    (OUTPUT/"model_through2025.json").write_text(json.dumps(final,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    recent=prepare_longshot(prepare_2026(safe))
    p26={"market":recent["市場3着内基準"].to_numpy(float)}
    for name,m in final.items():
        p26[name]=predict(recent,m)
    p26["v3Selected"]=p26[chosen["spec"]]
    # Existing frozen V2, evaluated on the same race/horse set.
    spec=json.loads((V2_OUTPUT/"research.json").read_text(encoding="utf-8"))
    v2path=V2_OUTPUT/"model_through2025.txt"
    ensure(spec["version"]==V2_VERSION and spec["researchOnly"] is True
        and spec["candidatePromotionApproved"] is False
        and spec["modelHashes"][v2path.name]==hashlib.sha256(v2path.read_bytes()).hexdigest(),"V2 identity mismatch")
    v2choice=spec["selection2024"]["selected"]
    extras=add_extras(recent,place_history(hist))
    ensure(extras[["レースID","馬番"]].reset_index(drop=True).equals(
        recent[["レースID","馬番"]].reset_index(drop=True)),"V2 row order changed")
    p26["v2Frozen"]=v2_predict(extras,lgb.Booster(model_file=str(v2path)),
                              FEATURE_SETS[v2choice["featureSet"]],v2choice["weight"])
    score26=compare(recent,p26)
    report={"version":VERSION,"generatedAtJST":datetime.now(JST).isoformat(),
        "status":"exploratory_retrospective_only","researchOnly":True,
        "candidatePromotionApproved":False,"formalPredictionAIChanged":False,"autoBetEnabled":False,
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "modelSha256":hashlib.sha256((OUTPUT/"model_through2025.json").read_bytes()).hexdigest(),
        "v2ModelSha256":spec["modelHashes"][v2path.name],
        "protocol":protocol,"2025":score25,"2026":score26,
        "limitations":["Focused features suggested by already examined 2025/26 outcomes; not independent holdouts.",
            "All training and parameter selection exclude 2026. 2025 model fit through 2023; 2026 refit through 2025.",
            "Market uses closing odds; no T10 or profit claims. Only 2026 frozen V2 comparison.",
            "Compare calibration-only against feature corrections; no automatic model adoption."]}
    (OUTPUT/"research.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("V3_RESEARCH_RESULT",json.dumps({"chosen":chosen,"2025":score25,"2026":score26},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
