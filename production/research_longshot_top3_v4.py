"""V4 isolated, paired ablation of race context and form dynamics."""
from datetime import datetime
import hashlib
import json
import numpy as np
import pandas as pd
import lightgbm as lgb

from train_longshot_top3 import ROOT,HISTORY,JST,PARAMS,WEIGHTS,logit,sigmoid,prepare_longshot,split_longshot,training_targets
from train_no_current_odds_challenger import ensure,prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from research_longshot_top3_v2 import (FEATURE_SETS,add_extras,place_history,paired,OUTPUT as V2_OUTPUT,
                                        VERSION as V2_VERSION,predict as v2_predict)
from evaluate_no_current_odds_longshot_2026 import paired_hit_ci
from longshot_v4_context_features import build,STYLE,FORM,NEW

OUT=ROOT/"research/model_candidates/longshot_top3_v4"
BASE=FEATURE_SETS["place_and_market"]
SPECS={"v2MatchedControl":BASE,"style":BASE+STYLE,"form":BASE+FORM,"both":BASE+NEW}


def x(d,spec):
    a=d.loc[:,list(SPECS[spec])].astype(np.float32)
    ensure(np.isfinite(a.values).all(),"nonfinite feature matrix")
    return a


def fit(d,spec,valid=None,rounds=None):
    year=pd.to_datetime(d["日付"]).dt.year
    ensure(year.le(2023 if valid is not None else 2025).all(),"training boundary")
    y,b=training_targets(d)
    ds=lgb.Dataset(x(d,spec),label=y,init_score=logit(b))
    if valid is not None:
        ensure(pd.to_datetime(valid["日付"]).dt.year.eq(2024).all(),"validation boundary")
        yy,bb=training_targets(valid)
        val=lgb.Dataset(x(valid,spec),label=yy,init_score=logit(bb),reference=ds)
        model=lgb.train(PARAMS,ds,num_boost_round=300,valid_sets=[val],
                       callbacks=[lgb.early_stopping(45,verbose=False),lgb.log_evaluation(0)])
        return model,int(model.best_iteration or 300)
    ensure(type(rounds) is int and 1<=rounds<=300,"invalid frozen round count")
    return lgb.train(PARAMS,ds,num_boost_round=rounds),rounds


def predict(d,model,spec,weight):
    ensure(model.feature_name()==list(SPECS[spec]) and weight in WEIGHTS,"model identity")
    baseline=d["市場3着内基準"].to_numpy(float)
    return sigmoid(logit(baseline)+weight*model.predict(x(d,spec),raw_score=True,validate_features=True,num_threads=2))


def brier(d,p):
    return float(pd.Series((p-d["着順"].le(3).to_numpy(float))**2).groupby(
        d["レースID"].astype(str).reset_index(drop=True)).mean().mean())


def compare(d,methods,reference):
    frame=d[["レースID","日付","馬番","着順"]].reset_index(drop=True).copy()
    frame["hit"]=frame["着順"].le(3).astype(int)
    for m,p in methods.items():
        ensure(len(p)==len(frame) and np.isfinite(p).all() and ((p>=0)&(p<=1)).all(),"invalid prediction")
        frame[m]=p
    out={m:{"brier":brier(d,p),"picks":{str(k):{"hits":0,"selected":0,"racesHit":0} for k in (1,2,3)}} for m,p in methods.items()}
    rows=[]
    for _,g in frame.groupby("レースID",sort=False):
        row={"date":str(g["日付"].iloc[0].date())}
        for m in methods:
            ranked=g.sort_values([m,"馬番"],ascending=[False,True])
            for k in (1,2,3):
                pick=ranked.head(k);h=int(pick["hit"].sum())
                s=out[m]["picks"][str(k)]
                s["hits"]+=h;s["selected"]+=len(pick);s["racesHit"]+=int(h>0)
                row[f"{m}_{k}"]=int(h>0)
        rows.append(row)
    for m in methods:
        for k in (1,2,3):
            s=out[m]["picks"][str(k)]
            s["precision"]=s["hits"]/s["selected"]
            s["raceHitRate"]=s["racesHit"]/len(rows)
            s["wholeDateHitDeltaVsMarket95"]=paired_hit_ci(rows,m,k) if m!="market" else None
        if m!=reference:
            out[m]["pairedBrierVsControl"]=paired(d,methods[m],methods[reference])
    return {"races":len(rows),"horses":len(d),"actualPodium":int(frame["hit"].sum()),"methods":out}


def join(d,prior,new):
    q=add_extras(prepare_longshot(d),prior)
    q=q.merge(new,on=["レースID","馬番"],how="left",sort=False,validate="one_to_one")
    ensure(np.isfinite(q[list(NEW)].values).all(),"new feature join missing")
    return q.reset_index(drop=True)


def main():
    hist=pd.read_parquet(HISTORY)
    prior=place_history(hist)
    new=build(hist)
    safe=hist.copy()
    safe["馬体重"]=pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    train,valid,test25=split_longshot(join(prepare_history(safe),prior,new))
    models={};choices={};grid=[]
    for spec in SPECS:
        model,rounds=fit(train,spec,valid)
        models[spec]=model
        g=[{"spec":spec,"weight":w,"rounds":rounds,"brier":brier(valid,predict(valid,model,spec,w))} for w in WEIGHTS]
        choices[spec]=min(g,key=lambda r:(r["brier"],r["weight"]))
        grid.extend(g)
        print("V4_FIT",json.dumps(choices[spec]),flush=True)
    selected=min(choices.values(),key=lambda r:(r["brier"],len(SPECS[r["spec"]]),r["weight"]))
    OUT.mkdir(parents=True,exist_ok=True)
    protocol={"choices":choices,"selected":selected,"grid2024":grid,"trainingThrough":"2023-12-31","selectionYear":2024}
    (OUT/"selection2024.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n")
    print("V4_SELECTION_LOCKED",json.dumps(selected),flush=True)
    p25={"market":test25["市場3着内基準"].to_numpy(float)}
    for s in SPECS:
        p25[s]=predict(test25,models[s],s,choices[s]["weight"])
    p25["selected"]=p25[selected["spec"]]
    score25=compare(test25,p25,"v2MatchedControl")
    full=pd.concat([train,valid,test25],ignore_index=True)
    recent=join(prepare_2026(safe),prior,new)
    p26={"market":recent["市場3着内基準"].to_numpy(float)}
    for s in SPECS:
        m,_=fit(full,s,rounds=choices[s]["rounds"])
        m.save_model(str(OUT/(s+"_through2025.txt")))
        p26[s]=predict(recent,m,s,choices[s]["weight"])
    p26["selected"]=p26[selected["spec"]]
    v2=json.loads((V2_OUTPUT/"research.json").read_text())
    path=V2_OUTPUT/"model_through2025.txt"
    ensure(v2["version"]==V2_VERSION and v2["researchOnly"] is True
        and v2["modelHashes"][path.name]==hashlib.sha256(path.read_bytes()).hexdigest(),"frozen V2 identity")
    v2choice=v2["selection2024"]["selected"]
    p26["v2Frozen"]=v2_predict(recent,lgb.Booster(model_file=str(path)),
                             FEATURE_SETS[v2choice["featureSet"]],v2choice["weight"])
    score26=compare(recent,p26,"v2Frozen")
    report={"version":"longshot-v4-context-dynamics-20261124","generatedAtJST":datetime.now(JST).isoformat(),
        "status":"exploratory_retrospective_only","researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),"protocol":protocol,
        "styleFeatures":list(STYLE),"formFeatures":list(FORM),
        "modelHashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob("*.txt")},
        "2025":score25,"2026":score26,"limitations":[
        "Style pressure is a proxy, not actual current-race sectional pace.",
        "New history features are emitted before any outcome on the date; current results are not inputs.",
        "Current market uses final odds; no T10 or financial-return claim.",
        "2025/26 previously studied; selection only 2024 but not untouched project-wide holdouts.",
        "Matched control retrained with same protocol; frozen V2 evaluated only in 2026."]}
    (OUT/"research.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print("V4_RESEARCH_RESULT",json.dumps({"selected":selected,"2025":score25,"2026":score26},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
