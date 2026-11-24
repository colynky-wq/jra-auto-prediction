"""Frozen 2026 longshot diagnostic; no fitting, tuning, live claims or tickets."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF
from evaluate_no_current_odds_2026_history import prepare_2026, verified_candidate
from train_no_current_odds_challenger import (
    FEATURES, HISTORY, MODEL_PATH, REPORT_PATH, ROOT, JST,
    probability_triplet, normalized_win, ensure,
)
from train_top2_top3_models import plackett_luce_top2_top3
from topk_runtime import logit, sigmoid, normalize_probability_total

REPORT = ROOT / "research/NoCurrentOddsLongshot2026Review.json"
FORMAL = ROOT / "production/lightgbm_top3_fixed_2025.txt"
FORMAL_META = FORMAL.with_name("lightgbm_top3_fixed_2025_info.json")
METHODS = ("market", "formalReplay", "independent", "independentMinusMarket")
SEED = 20261124


def full_fields(data, history):
    # Exclude incomplete fields and missing current body weights, rather than
    # accepting whole-table median imputation using future data. Also exclude
    # any duplicated horse-day, which could let legacy shifts see same-day
    # outcomes. Selection uses availability/identity, not model performance.
    current = history.loc[pd.to_datetime(history["日付"]).dt.year == 2026].copy()
    duplicate = current.duplicated(["日付", "馬キー"], keep=False)
    unsafe = set(current.loc[duplicate | current["馬体重"].isna()
                             | current["増減"].isna(), "レースID"].astype(str))
    d = data.loc[~data["レースID"].astype(str).isin(unsafe)].copy()
    d = d.groupby("レースID",sort=False).filter(
        lambda g: g["頭数"].eq(len(g)).all()
        and g["馬番"].nunique()==len(g)
        and g["馬キー"].nunique()==len(g)
        and g["着順"].between(1,len(g)).all()
        and g["人気"].notna().all()
        and g["単勝オッズ"].ge(1).all())
    return d.reset_index(drop=True)


def probabilities(data, independent, formal):
    ensure(list(formal.feature_name()) == list(ALLF), "formal model feature identity")
    candidate = probability_triplet(data, independent)["top3"]
    market_win = normalized_win(data["市場確率"], data["レースID"])
    _, market = plackett_luce_top2_top3(pd.DataFrame(
        {"レースID":data["レースID"],"市場確率":market_win}))
    X = data[list(ALLF)].astype(np.float32)
    ensure(np.isfinite(X.values).all(), "nonfinite formal features")
    correction = formal.predict(X, raw_score=True, validate_features=True, num_threads=2)
    raw = sigmoid(logit(market)+correction)
    replay = np.empty(len(data))
    for _, pos in data.groupby("レースID",sort=False).indices.items():
        replay[pos] = normalize_probability_total(raw[pos],3)
    return {"market":market, "formalReplay":replay, "independent":candidate,
            "independentMinusMarket":candidate-market}


def paired_hit_ci(rows, method, k):
    # Resample full dates, maintaining within-day correlation.
    d=pd.DataFrame(rows)
    if d.empty:
        return None
    d["delta"]=d[f"{method}_{k}"]-d[f"market_{k}"]
    daily=d.groupby("date")["delta"].agg(["sum","count"])
    if len(daily)<20:
        return None
    rng=np.random.default_rng(SEED)
    sums,counts=daily["sum"].to_numpy(),daily["count"].to_numpy()
    draws=rng.integers(0,len(daily),size=(1500,len(daily)))
    values=sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
    return [float(np.quantile(values,.025)),float(np.quantile(values,.975))]


def evaluate(data, probs):
    ensure(set(probs)==set(METHODS),"method set differs")
    d=data[["レースID","日付","馬番","人気","単勝オッズ","着順"]].copy().reset_index(drop=True)
    for m,p in probs.items():
        ensure(len(p)==len(d) and np.isfinite(p).all(),"invalid scores")
        if m!="independentMinusMarket":
            ensure(((p>=0)&(p<=1.00000001)).all(),"invalid probabilities")
        d[m]=p
    d["hit"]=(d["着順"]<=3).astype(int)
    d=d.loc[d["人気"].ge(6)&d["単勝オッズ"].ge(10)].copy()
    ensure(not d.empty,"no longshots")
    output={}
    for label,group in (("all",d),("10toUnder20",d.loc[d["単勝オッズ"]<20]),
                        ("20toUnder50",d.loc[d["単勝オッズ"].between(20,50,inclusive="left")]),
                        ("50plus",d.loc[d["単勝オッズ"]>=50])):
        if group.empty:
            continue
        rows=[]
        sums={m:{k:{"selections":0,"hits":0,"raceHitCount":0} for k in (1,2,3)}
              for m in METHODS}
        for rid,g in group.groupby("レースID",sort=False):
            r={"date":str(g["日付"].iloc[0].date())}
            for m in METHODS:
                ranked=g.sort_values([m,"馬番"],ascending=[False,True])
                for k in (1,2,3):
                    selected=ranked.head(k)
                    hits=int(selected["hit"].sum())
                    r[f"{m}_{k}"]=int(hits>0)
                    s=sums[m][k];s["selections"]+=len(selected);s["hits"]+=hits
                    s["raceHitCount"]+=int(hits>0)
            rows.append(r)
        n=len(rows)
        for m in METHODS:
            for k in (1,2,3):
                s=sums[m][k]
                s["precision"]=s["hits"]/s["selections"]
                s["raceHitRate"]=s["raceHitCount"]/n
                s["recallOfEligiblePodiumHorses"]=s["hits"]/int(group["hit"].sum()) if group["hit"].sum() else None
                s["pairedRaceHitDeltaVsMarket"]=s["raceHitRate"]-sums["market"][k]["raceHitCount"]/n
                s["wholeDateBootstrap95"]=paired_hit_ci(rows,m,k) if m!="market" else None
        losses={}
        for m in METHODS[:3]:
            sq=(group[m]-group["hit"])**2
            losses[m]=float(sq.groupby(group["レースID"]).mean().mean())
        output[label]={"races":n,"horses":len(group),"actualPodiumHorses":int(group["hit"].sum()),
                       "equalRaceBrier":losses,"fixedShortlistSizes":sums}
    return output


def main():
    meta=json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    verified_candidate(meta,MODEL_PATH.read_bytes())
    fm=json.loads(FORMAL_META.read_text(encoding="utf-8"))
    ensure(fm["trainingData"]=="2014-01-01 through 2025-12-31"
           and fm["data2026Used"] is False and fm["features"]==list(ALLF),
           "formal replay training contract")
    history=pd.read_parquet(HISTORY)
    data=full_fields(prepare_2026(history),history)
    ensure(data["レースID"].nunique()>80,"too few complete races")
    scores=probabilities(data,lgb.Booster(model_file=str(MODEL_PATH)),
                         lgb.Booster(model_file=str(FORMAL)))
    result={"version":"no-current-odds-longshot-2026-v1","status":"retrospective_research_only",
            "generatedAtJST":datetime.now(JST).isoformat(),
            "researchOnly":True,"candidatePromotionApproved":False,"autoBetEnabled":False,
            "formalPredictionAIChanged":False,"modelSha256":meta["modelSha256"],
            "formalReplayModelSha256":hashlib.sha256(FORMAL.read_bytes()).hexdigest(),
            "sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
            "period":{"start":str(data["日付"].min().date()),"end":str(data["日付"].max().date())},
            "scoredRaces":int(data["レースID"].nunique()),"scoredHorses":len(data),
            "eligible":"final popularity >=6 and final win odds >=10",
            "limitations":["Historical final odds only, not T10 or live forecasts.",
                "Independent model excludes current odds, but eligible shortlist and market comparison use final odds.",
                "Fixed 2025 models only; no fitting or tuning on 2026.",
                "Three fixed shortlist sizes and odds bands are descriptive multiple comparisons, not a chosen strategy.",
                "No payout/return, ticket advice, or automatic promotion."],
            "groups":evaluate(data,scores)}
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    g=result["groups"]["all"]
    print("LONGSHOT_REVIEW",json.dumps({"races":result["scoredRaces"],"eligibleRaces":g["races"],
          "horses":g["horses"],"actualPodium":g["actualPodiumHorses"],"brier":g["equalRaceBrier"],
          "onePerRace":{m:g["fixedShortlistSizes"][m][1] for m in METHODS},
          "twoPerRace":{m:g["fixedShortlistSizes"][m][2] for m in METHODS}},ensure_ascii=False))


if __name__=="__main__":
    main()
