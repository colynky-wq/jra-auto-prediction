"""V7: change learning objective, not feature information.
Compare within-race ranking objectives on identical V2 47-feature cohorts.
Raw ranking scores are NOT calibrated probabilities.
"""
from datetime import datetime
import hashlib
import json
import lightgbm as lgb
import numpy as np
import pandas as pd
from train_longshot_top3 import ROOT,HISTORY,JST,PARAMS,logit,prepare_longshot,split_longshot
from train_no_current_odds_challenger import ensure,prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from research_longshot_top3_v2 import (FEATURE_SETS,add_extras,place_history,
    train as train_v2,predict as predict_v2,OUTPUT as V2_OUT,VERSION as V2_VERSION)
from evaluate_no_current_odds_longshot_2026 import paired_hit_ci

OUT=ROOT/"research/model_candidates/longshot_top3_v7"
FEATURES=FEATURE_SETS["place_and_market"]
OBJECTIVES=("lambdarank","rank_xendcg")
ROUNDS=(80,160,240)
BLENDS=(.25,.5,1.)
K=(1,2,3)


def matrix(d):
    x=d.loc[:,list(FEATURES)].astype(np.float32)
    ensure(np.isfinite(x.values).all(),"nonfinite ranking features")
    return x


def grouped(d):
    ensure(not d.duplicated(["レースID","馬番"]).any(),"duplicate ranking runner")
    ordered=d.sort_values(["日付","レースID","馬番"],kind="stable").reset_index(drop=True)
    sizes=ordered.groupby("レースID",sort=False).size().to_numpy(int)
    ensure(sizes.sum()==len(d) and len(sizes)>0,"invalid ranking groups")
    # Verify each query is a contiguous complete eligible candidate group.
    starts=np.r_[0,np.cumsum(sizes)[:-1]]
    ensure(np.array_equal(ordered["レースID"].to_numpy()[starts],ordered["レースID"].drop_duplicates().to_numpy()),"noncontiguous queries")
    return ordered,sizes


def fit(d,objective,rounds,through):
    ensure(objective in OBJECTIVES and rounds in ROUNDS and through in (2023,2025),"unregistered rank model")
    ensure(pd.to_datetime(d["日付"]).dt.year.le(through).all(),"future rank training")
    g,sizes=grouped(d); y=g["着順"].le(3).to_numpy(int)
    ensure(y.sum()>0 and y.sum()<len(y),"degenerate ranking labels")
    params={**PARAMS,"objective":objective,"metric":"ndcg","ndcg_eval_at":[1,2,3],
            "label_gain":[0,1],"lambdarank_truncation_level":4}
    data=lgb.Dataset(matrix(g),label=y,group=sizes,feature_name=list(FEATURES))
    return lgb.train(params,data,num_boost_round=rounds)


def raw(d,model,rounds):
    ensure(rounds in ROUNDS and model.feature_name()==list(FEATURES),"rank identity")
    p=np.asarray(model.predict(matrix(d),num_iteration=rounds,raw_score=True,
                               validate_features=True,num_threads=2),float)
    ensure(len(p)==len(d) and np.isfinite(p).all(),"bad ranking score")
    return p


def within_race(d,s):
    a=pd.Series(np.asarray(s,float))
    keys=d["レースID"].reset_index(drop=True)
    g=a.groupby(keys,sort=False)
    return ((a-g.transform("mean"))/g.transform("std",ddof=0).clip(lower=1e-8)).to_numpy(float)


def blend(d,p,s,alpha):
    ensure(alpha in (0.,)+BLENDS,"unknown blend weight")
    ensure(len(p)==len(d)==len(s) and np.isfinite(p).all() and np.isfinite(s).all(),"invalid score inputs")
    if alpha==0:
        return np.asarray(p,float).copy()
    return (1-alpha)*within_race(d,logit(p))+alpha*within_race(d,s)


def race_index(d):
    # Stable tie-breaker: lowest horse number. Never use finishing order.
    codes,ids=pd.factorize(d["レースID"].astype(str),sort=True)
    numbers=d["馬番"].to_numpy(int)
    return codes,numbers,len(ids)


def picks(d,s):
    codes,numbers,n=race_index(d)
    ensure(len(s)==len(d) and np.isfinite(s).all(),"invalid score")
    order=np.lexsort((numbers,-np.asarray(s,float),codes))
    counts=np.bincount(codes,minlength=n)
    starts=np.r_[0,np.cumsum(counts)[:-1]]
    pos=np.arange(len(d))-np.repeat(starts,counts)
    return order,pos,codes,n


def hit_arrays(d,s):
    order,pos,codes,n=picks(d,s)
    y=d["着順"].le(3).to_numpy(int)
    out={}
    for k in K:
        ix=order[pos<k]
        h=np.bincount(codes[ix],weights=y[ix],minlength=n).astype(int)
        out[k]=(len(ix),h)
    return out


def quality(d,s):
    a=hit_arrays(d,s)
    return {f"hit{k}":float(np.mean(a[k][1]>0)) for k in K}


def selection(train,valid,p):
    ensure(pd.to_datetime(valid["日付"]).dt.year.eq(2024).all(),"validation year changed")
    grid=[{"objective":"v2","rounds":0,"alpha":0.,**quality(valid,p)}]
    models={}
    for objective in OBJECTIVES:
        model=fit(train,objective,max(ROUNDS),2023);models[objective]=model
        for rounds in ROUNDS:
            s=raw(valid,model,rounds)
            for a in BLENDS:
                grid.append({"objective":objective,"rounds":rounds,"alpha":a,
                             **quality(valid,blend(valid,p,s,a))})
        print("V7_FIT",objective,flush=True)
    # User's primary research use case is two longshot candidates, then one.
    # No payout or ticket claim; simpler V2 wins exact ties.
    def key(r):
        return (-r["hit2"],-r["hit1"],-r["hit3"],r["alpha"],r["rounds"],r["objective"])
    chosen={o:min((r for r in grid if r["objective"]==o),key=key) for o in OBJECTIVES}
    selected=min(grid,key=key)
    return selected,chosen,grid,models


def evaluate(d,methods):
    d=d.reset_index(drop=True)
    n=d["レースID"].nunique()
    race_days=d.assign(_race=d["レースID"].astype(str)).groupby("_race",sort=True)["日付"].first()
    dates=[str(x.date()) for x in pd.to_datetime(race_days)]
    values={name:hit_arrays(d,s) for name,s in methods.items()}
    rows=[{"date":day,**{f"{name}_{k}":int(v[k][1][i]>0) for name,v in values.items() for k in K}} for i,day in enumerate(dates)]
    # Helper's reference column is named market; overwrite in an independent
    # copy so the actual market result remains separately visible.
    reference=[{**r,**{f"market_{k}":r[f"v2_{k}"] for k in K}} for r in rows]
    out={}
    for name,v in values.items():
        out[name]={}
        for k,(selected,h) in v.items():
            out[name][str(k)]={"selected":selected,"hits":int(h.sum()),"racesHit":int((h>0).sum()),
                "raceHitRate":float(np.mean(h>0)),"racesWithTwoOrMoreHits":int((h>=2).sum()),
                "raceTwoOrMoreHitRate":float(np.mean(h>=2)),
                "pairedHitDeltaVsV2_95":paired_hit_ci(reference,name,k) if name not in ("market","v2") else None}
    return {"races":int(n),"horses":len(d),"actualPodium":int(d["着順"].le(3).sum()),"methods":out}


def segments(d,methods):
    odds=d["単勝オッズ"].to_numpy(float)
    out={}
    for name,mask in (("10toUnder20",(odds>=10)&(odds<20)),("20toUnder50",(odds>=20)&(odds<50)),("50plus",odds>=50)):
        if mask.any():
            # Rerank within each band, conditional descriptive comparison.
            out[name]=evaluate(d.loc[mask],{k:v[mask] for k,v in methods.items() if k in ("market","v2","selected")})
    return out


def main():
    hist=pd.read_parquet(HISTORY);prior=place_history(hist)
    safe=hist.copy();safe["馬体重"]=pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    train,valid,hold=split_longshot(add_extras(prepare_longshot(prepare_history(safe)),prior))
    report=json.loads((V2_OUT/"research.json").read_text())
    choice=report["selection2024"]["selected"]
    ensure(report["version"]==V2_VERSION and report["researchOnly"] is True
           and report["candidatePromotionApproved"] is False and report["selectedFeatures"]==list(FEATURES)
           and choice["featureSet"]=="place_and_market","frozen V2 contract")
    v2,rounds=train_v2(train,FEATURES,valid)
    ensure(rounds==choice["rounds"],"V2 replay rounds changed")
    pval=predict_v2(valid,v2,FEATURES,choice["weight"])
    selected,choices,grid,models=selection(train,valid,pval)
    OUT.mkdir(parents=True,exist_ok=True)
    protocol={"selected":selected,"choices":choices,"grid2024":grid,
              "selectionRule":"2024 top2 race hit rate, top1, top3, then smaller blend and fewer trees",
              "trainingThrough":"2023-12-31","selectionYear":2024,
              "rankFeatures":list(FEATURES)}
    (OUT/"selection2024.json").write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+"\n")
    print("V7_SELECTION_LOCKED",json.dumps(selected),flush=True)
    p25=predict_v2(hold,v2,FEATURES,choice["weight"])
    m25={"market":hold["市場3着内基準"].to_numpy(float),"v2":p25}
    for obj,c in choices.items():
        r=raw(hold,models[obj],c["rounds"])
        m25[obj]=blend(hold,p25,r,c["alpha"])
        m25[obj+"Pure"]=r
    m25["selected"]=m25[selected["objective"]]
    score25=evaluate(hold,m25)
    full=pd.concat([train,valid,hold],ignore_index=True)
    recent=add_extras(prepare_longshot(prepare_2026(safe)),prior)
    frozen=V2_OUT/"model_through2025.txt";sha=hashlib.sha256(frozen.read_bytes()).hexdigest()
    ensure(sha==report["modelHashes"][frozen.name],"V2 model digest")
    p26=predict_v2(recent,lgb.Booster(model_file=str(frozen)),FEATURES,choice["weight"])
    m26={"market":recent["市場3着内基準"].to_numpy(float),"v2":p26}
    for obj,c in choices.items():
        model=fit(full,obj,c["rounds"],2025)
        model.save_model(str(OUT/(obj+"_through2025.txt")))
        r=raw(recent,model,c["rounds"])
        m26[obj]=blend(recent,p26,r,c["alpha"]);m26[obj+"Pure"]=r
    m26["selected"]=m26[selected["objective"]]
    score26=evaluate(recent,m26)
    result={"version":"longshot-v7-ranking-objective-20261124","generatedAtJST":datetime.now(JST).isoformat(),
        "status":"retrospective_research_only","researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),"v2ModelSha256":sha,
        "rankModelHashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob("*.txt")},
        "protocol":protocol,"2025":score25,"2026":score26,
        "oddsBands2026":segments(recent,m26),
        "limitations":["Ranking scores are not probabilities; no Brier comparison for rank scores.",
            "Two or more selected podium horses is only a historical hit-count diagnostic, not wide ticket ROI.",
            "Same 47 V2 features; no new input information. Final odds, not verified T10.",
            "2025/26 already examined by project; exploratory comparisons, not independent prospective evidence.",
            "Pure ranking and odds-band comparisons descriptive; no post-hoc promotion."]}
    (OUT/"research.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print("V7_RESEARCH_RESULT",json.dumps({"selected":selected,"choices":choices,"2025":score25,"2026":score26,
        "oddsBands2026":result["oddsBands2026"]},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
