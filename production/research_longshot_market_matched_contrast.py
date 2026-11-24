"""Descriptive hit/miss contrasts within race and fixed market probability bands.
2025/2026 separately, no fit, no model promotion, no betting decisions.
"""
from datetime import datetime
import hashlib
import json

import numpy as np
import pandas as pd

from feature_engine import add_features, add_rating
from train_no_current_odds_challenger import FEATURES, HISTORY, ROOT, JST, ensure
from train_top2_top3_models import BASE_COLS, plackett_luce_top2_top3

REPORT = ROOT / "research/LongshotMarketMatchedFeatureContrast.json"
BINS = (0., .05, .10, .15, .20, .30, .40, 1.000001)
COMBINATIONS = ("priorTopQuartileAndSurface3", "priorTopQuartileAndPositiveRating",
                "classDropAndPriorTopHalf")
SEED = 20261124


def prepare(history):
    ensure(list(history.columns)==BASE_COLS,"history schema changed")
    d=history.copy()
    d["日付"]=pd.to_datetime(d["日付"],errors="coerce").dt.normalize()
    ensure(d["日付"].notna().all() and d["日付"].dt.year.le(2026).all(),"invalid dates")
    ensure(not d.duplicated(["レースID","馬番"]).any(),"duplicate race horse")
    # No missing body input (global median in legacy feature engine) and no
    # same-day duplicate horse, which could contaminate shift features.
    unsafe=d.duplicated(["日付","馬キー"],keep=False)|d["馬体重"].isna()|d["増減"].isna()
    excluded=set(d.loc[unsafe,"レースID"].astype(str))
    d=d.sort_values(["日付","レースID","馬番"]).reset_index(drop=True)
    d=add_features(add_rating(d))
    d=d.loc[d["日付"].dt.year.isin([2025,2026]) & ~d["レースID"].astype(str).isin(excluded)].copy()
    d=d.loc[d["対象"].fillna(False)&d["着順"].notna()&d["頭数"].ge(3)&d["人気"].notna()
            &d["単勝オッズ"].ge(1)].copy()
    def complete(g):
        n=len(g);f=g["着順"]
        return (g["頭数"].eq(n).all() and g["馬キー"].nunique()==n
                and g["馬番"].nunique()==n and f.between(1,n).all()
                and all(f.eq(k).sum()==1 for k in (1,2,3)))
    d=d.groupby("レースID",sort=False).filter(complete).reset_index(drop=True)
    ensure(d["レースID"].nunique()>100,"insufficient history")
    _,p3=plackett_luce_top2_top3(d)
    d["marketTop3"]=p3
    ensure(np.isfinite(p3).all(),"invalid market")
    d=d.loc[d["人気"].ge(6)&d["単勝オッズ"].ge(10)].copy()
    d["hit"]=d["着順"].le(3)
    prior=d["出走数c"].gt(0)
    d["priorTopQuartileAndSurface3"]=(prior&d["前走着順率f"].le(.25)&d["同芝ダ回数c"].ge(3)).astype(float)
    d["priorTopQuartileAndPositiveRating"]=(prior&d["前走着順率f"].le(.25)&d["点数差"].gt(0)).astype(float)
    d["classDropAndPriorTopHalf"]=(prior&d["降級"].gt(0)&d["前走着順率f"].le(.5)).astype(float)
    return d


def ci_dates(daily):
    if len(daily)<20:
        return None
    rng=np.random.default_rng(SEED)
    values=daily["weightedDiff"].to_numpy()
    weights=daily["weight"].to_numpy()
    picks=rng.integers(0,len(daily),size=(1000,len(daily)))
    boot=values[picks].sum(axis=1)/weights[picks].sum(axis=1)
    return [float(np.quantile(boot,.025)),float(np.quantile(boot,.975))]


def contrasts(d, features):
    ensure(len(d)>0 and d["hit"].isin([True,False]).all(),"missing outcome")
    ensure(d["marketTop3"].between(0,1).all(),"invalid market probability")
    d=d.copy()
    d["band"]=pd.cut(d["marketTop3"],BINS,right=False,labels=False)
    # Fixed probability bands within the same race reduce confounding from
    # field, course, distance and market strength; not exact matching/causality.
    keys=["日付","レースID","band"]
    result={}
    for feature in features:
        v=d.loc[np.isfinite(d[feature]),keys+["hit",feature]].copy()
        if feature in ("前走着順率f","前走秒差f","前走上がり差f","前走人気f","log間隔","乗り替わり"):
            v=v.loc[d.loc[v.index,"出走数c"].gt(0)]
        if v.empty or v["hit"].nunique()!=2:
            continue
        group=v.groupby(keys+["hit"],observed=True)[feature].agg(["mean","size"]).unstack("hit")
        group=group.dropna()
        if group.empty:
            continue
        # Hit-weighted within-stratum mean contrast, not horse-independent CI.
        weight=group[("size",True)]
        diff=group[("mean",True)]-group[("mean",False)]
        mean=float((diff*weight).sum()/weight.sum())
        scale=float(v[feature].std(ddof=0))
        daily=pd.DataFrame({"weightedDiff":diff*weight,"weight":weight}).groupby(level=0).sum()
        interval=ci_dates(daily)
        result[feature]={"allHits":int(v["hit"].sum()),"allMisses":int((~v["hit"]).sum()),
            "rawHitMean":float(v.loc[v["hit"],feature].mean()),
            "rawMissMean":float(v.loc[~v["hit"],feature].mean()),
            "matchedStrata":len(group),"matchedRaces":int(group.index.get_level_values("レースID").nunique()),
            "matchedHits":int(weight.sum()),"matchedMisses":int(group[("size",False)].sum()),
            "matchedHitCoverage":float(weight.sum()/v["hit"].sum()),
            "hitMinusMissMatchedMean":mean,"standardizedContrast":mean/scale if scale>0 else None,
            "wholeDateBootstrap95":interval}
    return result


def report(d):
    yearly={}
    for year in (2025,2026):
        v=d.loc[d["日付"].dt.year.eq(year)]
        yearly[str(year)]={"races":int(v["レースID"].nunique()),"horses":len(v),
            "hits":int(v["hit"].sum()),"contrasts":contrasts(v,FEATURES+COMBINATIONS)}
    a,b=yearly["2025"]["contrasts"],yearly["2026"]["contrasts"]
    consistent=[]
    for f in a.keys()&b.keys():
        x,y=a[f],b[f]
        i,j=x["wholeDateBootstrap95"],y["wholeDateBootstrap95"]
        if (i is not None and j is not None and x["matchedHits"]>=100 and y["matchedHits"]>=100
            and x["standardizedContrast"] is not None and y["standardizedContrast"] is not None
            and ((i[0]>0 and j[0]>0) or (i[1]<0 and j[1]<0))):
            consistent.append({"feature":f,"effect2025":x["standardizedContrast"],
                "effect2026":y["standardizedContrast"],"matchedHits2025":x["matchedHits"],
                "matchedHits2026":y["matchedHits"],"rawCI2025":i,"rawCI2026":j})
    consistent.sort(key=lambda r:(-min(abs(r["effect2025"]),abs(r["effect2026"])),r["feature"]))
    return {"version":"longshot-market-matched-contrast-v1","researchOnly":True,
        "candidatePromotionApproved":False,"formalPredictionAIChanged":False,"autoBetEnabled":False,
        "marketTop3Bands":list(BINS),"yearly":yearly,"directionallyReplicatedExploratory":consistent,
        "limitations":["Final odds define eligible horses and matching bands; not a T10 backtest.",
            "Same race and probability band do not eliminate confounding. Contrasts are associations.",
            "Multiple comparisons; intervals are unadjusted and descriptive, not confirmatory.",
            "Matched subset may differ from all hits. No 2026 fitting, no strategy chosen."]}


def main():
    hist=pd.read_parquet(HISTORY)
    d=prepare(hist)
    out=report(d)
    out["generatedAtJST"]=datetime.now(JST).isoformat()
    out["sourceHistorySha256"]=hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    REPORT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("FEATURE_CONTRAST_REVIEW",json.dumps({"yearly":{k:{n:v[n] for n in ("races","horses","hits")}
        for k,v in out["yearly"].items()},"replicated":out["directionallyReplicatedExploratory"]},ensure_ascii=False))


if __name__=="__main__":
    main()
