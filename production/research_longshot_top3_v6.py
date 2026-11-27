"""V6 research: course and jockey affinity, past going profiles.
2025 development and chronological selection; 2026 fixed V2 comparison.
No actual current going is used or inferred; research only.
"""
from datetime import datetime
import hashlib
import json
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.optimize import minimize
from scipy.special import expit

from train_longshot_top3 import ROOT,HISTORY,JST,logit,prepare_longshot,split_longshot
from train_no_current_odds_challenger import ensure,prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from research_longshot_top3_v2 import (FEATURE_SETS,add_extras,place_history,paired,
    train as train_v2,predict as predict_v2,OUTPUT as V2_OUT,VERSION as V2_VERSION)
from evaluate_no_current_odds_longshot_2026 import paired_hit_ci
from longshot_v5_lap_features import load
from longshot_v6_affinity_features import build,COURSE,JOCKEY,GOING,NEW

OUT=ROOT/"research/model_candidates/longshot_top3_v6"
SPECS={"calibration":(),"course":COURSE,"jockey":JOCKEY,"courseJockey":COURSE+JOCKEY,"withGoingProfile":NEW}
PENALTIES=(.01,.1,1.)


def design(d,base,features):
    a=np.column_stack([logit(base),d.loc[:,list(features)].to_numpy(float)])
    ensure(np.isfinite(a).all(),"bad V6 inputs")
    return a


def fit(d,base,spec,penalty):
    ensure(pd.to_datetime(d["日付"]).dt.year.eq(2025).all(),"only 2025 may train V6")
    mask=np.ones(len(d),dtype=bool)
    ensure(mask.sum()>=1000,"insufficient development rows")
    a=design(d,base,SPECS[spec])[mask]
    y=d["着順"].le(3).to_numpy(float)[mask];offset=logit(base[mask])
    mu=a.mean(axis=0);scale=np.maximum(a.std(axis=0),1e-8)
    X=np.column_stack([np.ones(len(a)),(a-mu)/scale])
    def obj(beta):
        z=offset+X@beta
        loss=np.mean(np.logaddexp(0,z)-y*z)+.5*penalty*np.sum(beta[1:]**2)
        grad=X.T@(expit(z)-y)/len(y);grad[1:]+=penalty*beta[1:]
        return loss,grad
    opt=minimize(obj,np.zeros(X.shape[1]),method="L-BFGS-B",jac=True,
                 options={"maxiter":300,"ftol":1e-10})
    ensure(opt.success and np.isfinite(opt.x).all(),"V6 optimizer")
    return {"spec":spec,"features":list(SPECS[spec]),"penalty":penalty,
            "mean":mu.tolist(),"scale":scale.tolist(),"coefficients":opt.x.tolist()}


def predict(d,base,m):
    ensure(m["features"]==list(SPECS[m["spec"]]),"feature identity")
    mask=np.ones(len(d),dtype=bool)
    a=design(d,base,m["features"])[mask]
    X=np.column_stack([np.ones(len(a)),(a-np.asarray(m["mean"]))/np.asarray(m["scale"])])
    result=base.copy()
    result[mask]=expit(logit(base[mask])+X@np.asarray(m["coefficients"]))
    return result


def brier(d,p):
    return float(pd.Series((p-d["着順"].le(3).to_numpy(float))**2).groupby(
        d["レースID"].astype(str).reset_index(drop=True)).mean().mean())


def evaluate(d,methods):
    f=d[["レースID","日付","馬番","着順"]].reset_index(drop=True).copy()
    f["hit"]=f["着順"].le(3).astype(int)
    out={}; rows=[]
    for name,p in methods.items():
        ensure(len(p)==len(d) and np.isfinite(p).all() and ((p>=0)&(p<=1)).all(),"bad probabilities")
        f[name]=p
        out[name]={"brier":brier(d,p),"pairedBrierVsV2":paired(d,p,methods["v2"]),
                   "picks":{str(k):{"selected":0,"hits":0,"racesHit":0} for k in (1,2,3)}}
    for _,g in f.groupby("レースID",sort=False):
        row={"date":str(g["日付"].iloc[0].date())}
        for name in methods:
            ranked=g.sort_values([name,"馬番"],ascending=[False,True])
            for k in (1,2,3):
                z=ranked.head(k);h=int(z["hit"].sum());s=out[name]["picks"][str(k)]
                s["selected"]+=len(z);s["hits"]+=h;s["racesHit"]+=int(h>0)
                row[f"{name}_{k}"]=int(h>0)
        # Reuse paired helper's expected reference column name.
        for k in (1,2,3): row[f"market_{k}"]=row[f"v2_{k}"]
        rows.append(row)
    for name in methods:
        for k in (1,2,3):
            s=out[name]["picks"][str(k)];s["raceHitRate"]=s["racesHit"]/len(rows)
            s["wholeDateHitDeltaVsV2_95"]=paired_hit_ci(rows,name,k) if name not in ("v2","market") else None
    return {"races":len(rows),"horses":len(d),"sameVenueCoveredHorses":int(d["v6HorseVenueLogStarts"].gt(0).sum()),
            "exactCourseCoveredHorses":int(d["v6HorseCourseLogStarts"].gt(0).sum()),
            "pairCoveredHorses":int(d["v6PairLogStarts"].gt(0).sum()),
            "wetHistoryCoveredHorses":int(d["v6HorseWetLogStarts"].gt(0).sum()),"methods":out}


def join(d,prior,lap):
    q=add_extras(prepare_longshot(d),prior)
    q=q.merge(lap,on=["レースID","馬番"],how="left",sort=False,validate="one_to_one")
    ensure(np.isfinite(q[list(NEW)].values).all(),"affinity join missing")
    return q.reset_index(drop=True)


def main():
    hist=pd.read_parquet(HISTORY)
    records,archive=load(ROOT/"research/laps")
    lap,audit=build(hist,records)
    print("V6_AFFINITY_AUDIT",json.dumps({"archive":archive,"byYear":audit}),flush=True)
    prior=place_history(hist)
    safe=hist.copy();safe["馬体重"]=pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    old=add_extras(prepare_longshot(prepare_history(safe)),prior)
    train,valid,hold=split_longshot(old)
    v2=json.loads((V2_OUT/"research.json").read_text())
    choice=v2["selection2024"]["selected"]
    basefeatures=FEATURE_SETS["place_and_market"]
    ensure(v2["version"]==V2_VERSION and v2["researchOnly"] is True
           and v2["candidatePromotionApproved"] is False
           and choice["featureSet"]=="place_and_market" and v2["selectedFeatures"]==list(basefeatures),"V2 contract")
    # Reproduce <=2023 V2 using the existing locked 2024 early stop;
    # do not train V6 on predictions from a V2 fitted to 2025 outcomes.
    provisional,rounds=train_v2(train,basefeatures,valid)
    ensure(rounds==choice["rounds"],"historical V2 replay changed")
    d=hold.merge(lap,on=["レースID","馬番"],how="left",sort=False,validate="one_to_one").reset_index(drop=True)
    ensure(np.isfinite(d[list(NEW)].values).all(),"2025 affinity join missing")
    base=predict_v2(d,provisional,basefeatures,choice["weight"])
    dev=d["日付"].lt(pd.Timestamp("2025-09-01")).to_numpy()
    a=d.loc[dev].reset_index(drop=True);b=d.loc[~dev].reset_index(drop=True)
    grid=[{"spec":"v2","penalty":None,"brier":brier(b,base[~dev])}]
    for spec in SPECS:
        for penalty in PENALTIES:
            m=fit(a,base[dev],spec,penalty)
            grid.append({"spec":spec,"penalty":penalty,"brier":brier(b,predict(b,base[~dev],m))})
    selected=min(grid,key=lambda x:(x["brier"],len(SPECS.get(x["spec"],())),x["spec"]))
    choices={s:min((v for v in grid if v["spec"]==s),key=lambda x:x["brier"]) for s in SPECS}
    protocol={"selected":selected,"choices":choices,"grid2025":grid,
              "devEnd":"2025-08-31","validationStart":"2025-09-01",
              "developmentRaces":int(a["レースID"].nunique()),"validationRaces":int(b["レースID"].nunique())}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"selection2025.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n")
    print("V6_SELECTION_LOCKED",json.dumps(selected),flush=True)
    final={s:fit(d,base,s,ch["penalty"]) for s,ch in choices.items()}
    (OUT/"model_through2025.json").write_text(json.dumps(final,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    recent=join(prepare_2026(safe),prior,lap)
    frozen=V2_OUT/"model_through2025.txt"
    sha=hashlib.sha256(frozen.read_bytes()).hexdigest()
    ensure(sha==v2["modelHashes"][frozen.name],"frozen V2 digest")
    p=predict_v2(recent,lgb.Booster(model_file=str(frozen)),basefeatures,choice["weight"])
    methods={"v2":p,"market":recent["市場3着内基準"].to_numpy(float)}
    for s,m in final.items(): methods[s]=predict(recent,p,m)
    methods["selected"]=methods[selected["spec"]]
    score=evaluate(recent,methods)
    result={"version":"longshot-v6-affinity-20261124","generatedAtJST":datetime.now(JST).isoformat(),
        "status":"retrospective_research_only","researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "v2ModelSha256":sha,"v6ModelSha256":hashlib.sha256((OUT/"model_through2025.json").read_bytes()).hexdigest(),
        "archive":archive,"affinityAudit":audit,"protocol":protocol,"2026":score,
        "limitations":["Venue/surface/distance is a coarse course key, not inner/outer layout.",
            "Historical going profiles only; actual current going is never used. No claim of current going-match validation.",
            "Jockey name keys separate source eras; abbreviations or namesakes may still cause missed or imperfect matching.",
            "All new statistics use only previous-day outcomes and market residuals; shrink sparse groups.",
            "2025 V6 development uses recreated <=2023 V2, 2026 uses fixed <=2025 V2.",
            "Final odds, previously studied 2026, exploratory only; no financial-return or live claim."]}
    (OUT/"research.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print("V6_RESEARCH_RESULT",json.dumps({"selected":selected,"audit":audit,"2026":score},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
