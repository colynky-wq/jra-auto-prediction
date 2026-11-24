"""Historical joint-wide probability research; no ticket recommendations.
Fit on 2025 out-of-training predictions, test 2026. Actual payout evidence
restricted to archived validated pairs, never fabricated from odds.
"""
import argparse
from datetime import datetime
from itertools import combinations
from pathlib import Path
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
from research_longshot_top3_v2 import (FEATURE_SETS,add_extras,place_history,
    train as train_v2,predict as predict_v2,OUTPUT as V2_OUT,VERSION as V2_VERSION)
from research_longshot_top3_v7 import fit as rank_fit,raw as rank_raw,blend,within_race

OUT=ROOT/"research/model_candidates/longshot_wide_joint"
SEED=20261124
FEATURES=FEATURE_SETS["place_and_market"]
SPECS={"calibration":(),"v2Joint":("longshots","residualSum","residualMin","residualMax","residualProduct"),
       "v7Joint":("longshots","residualSum","residualMin","residualMax","residualProduct","rankSum","rankMin","rankMax")}
PENALTIES=(.001,.01,.1)
METHODS=("market","v2Joint","v7Joint","selected")


def joint(q):
    """Exact PL probability both horses occupy the top three, summed over orders."""
    q=np.asarray(q,float);ensure(len(q)>=3 and np.isfinite(q).all() and (q>0).all(),"bad full field")
    q=q/q.sum();n=len(q)
    i,j,k=np.ogrid[:n,:n,:n]
    den=(1-q[:,None,None])*(1-q[:,None,None]-q[None,:,None])
    distinct=(i!=j)&(i!=k)&(j!=k)
    p=np.divide(q[:,None,None]*q[None,:,None]*q[None,None,:],den,
                out=np.zeros((n,n,n)),where=distinct&(den>0))
    ordered=p.sum(axis=2)+p.sum(axis=1)+p.sum(axis=0)
    ans=ordered+ordered.T
    ensure(abs(np.triu(ans,1).sum()-3)<1e-8,"joint mass mismatch")
    return ans


def pair_frame(full,short,p2,rank):
    sh=short[["レースID","馬番","市場3着内基準"]].copy()
    sh["residual"]=logit(p2)-logit(sh["市場3着内基準"])
    sh["rankScore"]=within_race(short,rank)
    by={rid:g.set_index("馬番") for rid,g in sh.groupby("レースID",sort=False)}
    rows=[];excluded=0
    for rid,g in full.groupby("レースID",sort=False):
        if rid not in by:continue
        n=len(g);g=g.sort_values("馬番")
        ranks=pd.to_numeric(g["着順"],errors="coerce").to_numpy(float)
        if (n<8 or not g["頭数"].eq(n).all() or not g["馬番"].is_unique
            or not np.isfinite(ranks).all() or not np.all(ranks==np.floor(ranks))
            or not np.all((ranks>=1)&(ranks<=n)) or not all(np.sum(ranks==i)==1 for i in (1,2,3))):
            excluded+=1;continue
        numbers=g["馬番"].to_numpy(int);lookup=by[rid]
        ensure(set(lookup.index)<=set(numbers),"short roster mismatch")
        q=g["市場確率"].to_numpy(float);p=joint(q)
        for a,b in combinations(range(n),2):
            na,nb=int(numbers[a]),int(numbers[b]);la,lb=na in lookup.index,nb in lookup.index
            if not (la or lb):continue
            x,y=(float(lookup.at[na,"residual"]) if la else 0.,float(lookup.at[nb,"residual"]) if lb else 0.)
            u,v=(float(lookup.at[na,"rankScore"]) if la else 0.,float(lookup.at[nb,"rankScore"]) if lb else 0.)
            rows.append((str(rid),pd.Timestamp(g["日付"].iloc[0]),na,nb,
                str(g["馬名"].iloc[a]),str(g["馬名"].iloc[b]),float(p[a,b]),int(ranks[a]<=3 and ranks[b]<=3),
                int(la)+int(lb),x+y,min(x,y),max(x,y),x*y,u+v,min(u,v),max(u,v)))
    d=pd.DataFrame(rows,columns=["raceID","date","a","b","nameA","nameB","market","hit","longshots",
        "residualSum","residualMin","residualMax","residualProduct","rankSum","rankMin","rankMax"])
    ensure(len(d)>0 and not d.duplicated(["raceID","a","b"]).any(),"pair identity")
    return d,excluded


def matrix(d,spec):
    return np.column_stack([logit(d["market"]),d[list(SPECS[spec])].to_numpy(float)])


def fit(d,spec,penalty):
    ensure(d["date"].dt.year.eq(2025).all(),"pair training boundary")
    a=matrix(d,spec);mu=a.mean(axis=0);sd=np.maximum(a.std(axis=0),1e-8)
    x=np.column_stack([np.ones(len(d)),(a-mu)/sd]);y=d["hit"].to_numpy(float)
    offset=logit(d["market"]);w=1/d.groupby("raceID")["hit"].transform("size").to_numpy(float);w=w/w.sum()
    def objective(beta):
        z=offset+x@beta
        loss=np.sum(w*(np.logaddexp(0,z)-y*z))+.5*penalty*np.sum(beta[1:]**2)
        grad=x.T@(w*(expit(z)-y));grad[1:]+=penalty*beta[1:]
        return loss,grad
    res=minimize(objective,np.zeros(x.shape[1]),jac=True,method="L-BFGS-B",options={"maxiter":300,"ftol":1e-11})
    ensure(res.success and np.isfinite(res.x).all(),"pair optimization failed")
    return {"spec":spec,"penalty":penalty,"mean":mu.tolist(),"scale":sd.tolist(),"coefficients":res.x.tolist()}


def predict(d,m):
    x=np.column_stack([np.ones(len(d)),(matrix(d,m["spec"])-m["mean"])/m["scale"]])
    return expit(logit(d["market"])+x@np.asarray(m["coefficients"]))


def brier(d,p):
    return float(pd.Series((p-d["hit"].to_numpy())**2).groupby(d["raceID"].reset_index(drop=True)).mean().mean())


def bootstrap(day,values,denom):
    a=pd.DataFrame({"day":day,"value":values,"denom":denom}).groupby("day").sum()
    if len(a)<10:return None
    rng=np.random.default_rng(SEED);ix=rng.integers(0,len(a),(1500,len(a)))
    v=a["value"].to_numpy()[ix].sum(axis=1)/a["denom"].to_numpy()[ix].sum(axis=1)
    return [float(np.quantile(v,.025)),float(np.quantile(v,.975))]


def probability_report(d,methods):
    out={};y=d["hit"].to_numpy()
    for name,p in methods.items():
        ensure(np.isfinite(p).all() and ((p>=0)&(p<=1)).all(),"bad pair probability")
        delta=pd.DataFrame({"race":d["raceID"],"date":d["date"],"loss":(p-y)**2-(methods["market"]-y)**2}).groupby("race").agg({"date":"first","loss":"mean"})
        bins=[]
        for lo,hi in zip([0,.01,.025,.05,.1,.2],[.01,.025,.05,.1,.2,1.01]):
            mask=(p>=lo)&(p<hi)
            if mask.any():bins.append({"lower":lo,"upper":hi,"pairs":int(mask.sum()),"predicted":float(p[mask].mean()),"observed":float(y[mask].mean())})
        out[name]={"equalRaceBrier":brier(d,p),"meanProbability":float(p.mean()),"observedHitRate":float(y.mean()),
            "pairedBrierDeltaVsMarket95":bootstrap(delta["date"],delta["loss"],np.ones(len(delta))),
            "calibration":bins}
    return out


def payout_inventory(root):
    by={};bad=set();files=0
    for path in sorted((root/"reports").glob("*/WideValidation.json")):
        files+=1;doc=json.loads(path.read_text())
        for r in doc.get("races",[]):
            rid=str(r.get("raceID",""))
            if rid in by or rid in bad:
                by.pop(rid,None);bad.add(rid);continue
            if str(r.get("raceDate","")).replace("-","")!=path.parent.name:
                bad.add(rid);continue
            rows={}
            try:
                for p in r["pairs"]:
                    a,b=int(p["number1"]),int(p["number2"]);na,nb=p["name1"],p["name2"]
                    if a>b:a,b,na,nb=b,a,nb,na
                    value=p["payoutPer100Yen"]
                    if a==b or (a,b) in rows or type(value) is not int or value<0 or (value>0 and value<100) or p["hit"] is not (value>0):
                        raise ValueError("bad payout row")
                    rows[(a,b)]=(str(na),str(nb),value)
            except (KeyError,ValueError,TypeError):
                bad.add(rid);continue
            by[rid]={"date":r["raceDate"],"pairs":rows,"path":str(path.relative_to(root))}
    return by,{"files":files,"raceRecords":len(by),"invalidOrDuplicateRaces":len(bad)}


def normalize(name):
    import unicodedata
    return "".join(unicodedata.normalize("NFKC",name).split())


def attach_payout(d,archive):
    pieces=[];excluded={"notArchived":0,"identityOrResultMismatch":0,"noCommonPairs":0}
    for rid,g in d.groupby("raceID",sort=False):
        if rid not in archive:excluded["notArchived"]+=1;continue
        r=archive[rid]
        if pd.Timestamp(r["date"])!=g["date"].iloc[0]:excluded["identityOrResultMismatch"]+=1;continue
        pay=[];bad=False
        for row in g.itertuples(index=False):
            p=r["pairs"].get((row.a,row.b))
            if p is None:pay.append(np.nan);continue
            a,b,v=p
            if (normalize(a)!=normalize(row.nameA) or normalize(b)!=normalize(row.nameB)
                or bool(v>0)!=bool(row.hit)):
                bad=True;break
            pay.append(float(v))
        if bad:excluded["identityOrResultMismatch"]+=1;continue
        z=g.assign(payout=pay).dropna(subset=["payout"])
        if z.empty:excluded["noCommonPairs"]+=1;continue
        pieces.append(z)
    return (pd.concat(pieces) if pieces else d.iloc[:0].assign(payout=[])),excluded


def settlement(d,methods,with_payout=False):
    out={}
    for k in (1,3):
        data=d.groupby("raceID").filter(lambda g:len(g)>=k)
        if data.empty:continue
        results={};daily={}
        for m in methods:
            pick=data.sort_values(["raceID",m,"a","b"],ascending=[True,False,True,True]).groupby("raceID").head(k)
            g=pick.groupby("raceID").agg(day=("date","first"),hits=("hit","sum"),predicted=(m,"sum"))
            r={"races":len(g),"bets":len(pick),"hits":int(pick["hit"].sum()),
               "ticketHitRate":float(pick["hit"].mean()),"predictedMean":float(pick[m].mean()),
               "racesHit":int(g["hits"].gt(0).sum()),"raceHitRate":float(g["hits"].gt(0).mean())}
            if with_payout:
                g["payout"]=pick.groupby("raceID")["payout"].sum()
                stake=100*len(pick);ret=int(g["payout"].sum())
                r.update(stakeYen=stake,payoutYen=ret,netYen=ret-stake,returnPct=100*ret/stake,
                         hitButBelowStakeRaces=int(((g["hits"]>0)&(g["payout"]<100*k)).sum()),
                         profitableRaces=int((g["payout"]>100*k).sum()),
                         roi95=bootstrap(g["day"],g["payout"],np.full(len(g),k)))
            results[m]=r;daily[m]=g
        if with_payout:
            for m,g in daily.items():
                if m=="market":continue
                results[m]["roiDeltaVsMarket95"]=bootstrap(g["day"],g["payout"]-daily["market"]["payout"],np.full(len(g),k))
        out[str(k)]=results
    return out


def coherent_report(full25, full26, d25, d26, archive, provenance, *, compare_portfolios=False):
    from research_longshot_wide_coherent import evaluate

    frame = d26.copy()
    models, study, races = evaluate(full25, full26, d25, frame)
    method_names = ("market", "v2Joint", "v7Joint", "temperature", "coherentV2", "coherentV7")
    methods = {name: frame[name].to_numpy() for name in method_names}
    covered, excluded = attach_payout(frame, archive)
    historical = settlement(frame, method_names)
    for method, distribution in study["coherentDistribution2026"].items():
        for k, portfolio in distribution["portfolio"].items():
            ensure(
                portfolio["racesHit"] == historical[k][method]["racesHit"],
                f"coherent and marginal ticket selections disagree: {method}, {k}",
            )
    quality = probability_report(frame, methods)
    # Pair the loss differences by race before resampling whole race days.
    losses = frame[["raceID", "date"]].copy()
    v2_losses = np.square(methods["v2Joint"] - frame["hit"])
    for name in models:
        losses["delta"] = np.square(methods[name] - frame["hit"]) - v2_losses
        races = losses.groupby("raceID").agg(day=("date", "first"), delta=("delta", "mean"))
        quality[name]["pairedBrierDeltaVsIndependentV2"] = float(races["delta"].mean())
        quality[name]["pairedBrierDeltaVsIndependentV2_95"] = bootstrap(
            races["day"], races["delta"], np.ones(len(races)),
        )

    output = OUT.with_name("longshot_wide_coherent")
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "version": "longshot-wide-coherent-v1",
        "researchOnly": True,
        "candidatePromotionApproved": False,
        "formalPredictionAIChanged": False,
        "autoBetEnabled": False,
        "provenance": provenance,
        **study,
        "2026": {
            "races": int(frame["raceID"].nunique()),
            "pairs": len(frame),
            "firstDate": frame["date"].min().date().isoformat(),
            "lastDate": frame["date"].max().date().isoformat(),
            "quality": quality,
            "historicalHitDiagnostics": historical,
        },
        "payoutCommonSupport": {
            "races": int(covered["raceID"].nunique()),
            "pairs": len(covered),
            "excluded": excluded,
            "settlement": settlement(covered, method_names, True),
        },
        "limitations": [
            "Uses final win odds and final fields; no timestamped pre-race wide prices.",
            "Evaluated tickets include at least one longshot; complete fields of eight or more; no podium ties.",
            "Actual payouts are restricted to verified pairs in the prior archive.",
            "The 2026 history was used in earlier research and is a retrospective comparison.",
        ],
    }
    (output / "models_through2025.json").write_text(json.dumps(models, indent=2, allow_nan=False) + "\n")
    (output / "research.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    columns = ["raceID", "date", "a", "b", "nameA", "nameB", "hit", *method_names]
    frame[columns].to_csv(output / "pair_probabilities_2026.csv", index=False)
    covered[[*columns, "payout"]].to_csv(output / "verified_payout_pairs_2026.csv", index=False)
    print("WIDE_COHERENT_COMPLETE", json.dumps(study["selected2025"]), flush=True)
    if compare_portfolios:
        from research_longshot_wide_portfolio import write_comparison

        comparison = write_comparison(
            OUT.with_name("longshot_wide_portfolio_selection"), races, frame, covered, models, bootstrap,
        )
        for name, metrics in comparison["historical2026"].items():
            baseline = study["coherentDistribution2026"][name]["portfolio"]["3"]
            ensure(metrics["pairRank"]["racesHit"] == baseline["racesHit"], f"portfolio baseline differs: {name}")
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coherent", action="store_true", help="also evaluate coherent top-three models")
    parser.add_argument("--odds-replay", action="store_true", help="replay archived pre-race odds with coherent models")
    parser.add_argument("--portfolio-selection", action="store_true", help="compare exact three-ticket joint coverage")
    args = parser.parse_args()
    if args.portfolio_selection and not args.coherent:
        parser.error("--portfolio-selection requires --coherent")
    if args.odds_replay and not args.coherent:
        parser.error("--odds-replay requires --coherent")

    hist=pd.read_parquet(HISTORY);prior=place_history(hist);safe=hist.copy()
    safe["馬体重"]=pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480.)
    older=prepare_history(safe);train,val,hold=split_longshot(add_extras(prepare_longshot(older),prior))
    meta=json.loads((V2_OUT/"research.json").read_text());choice=meta["selection2024"]["selected"]
    ensure(meta["version"]==V2_VERSION and meta["researchOnly"] is True and meta["selectedFeatures"]==list(FEATURES),"V2 contract")
    v7=json.loads((ROOT/"research/LongshotV7VerifiedSummary.json").read_text())["results"]["selected"]
    ensure(v7["objective"]=="rank_xendcg" and v7["rounds"]==240 and v7["alpha"]==.25,"V7 frozen selection")
    v2,rounds=train_v2(train,FEATURES,val);ensure(rounds==choice["rounds"],"V2 replay rounds")
    p25=predict_v2(hold,v2,FEATURES,choice["weight"])
    rank=rank_fit(train,v7["objective"],v7["rounds"],2023)
    r25=blend(hold,p25,rank_raw(hold,rank,v7["rounds"]),v7["alpha"])
    full25=older.loc[pd.to_datetime(older["日付"]).dt.year.eq(2025)]
    d25,e25=pair_frame(full25,hold,p25,r25)
    dev=d25["date"].lt(pd.Timestamp("2025-09-01"))
    a,b=d25.loc[dev].reset_index(drop=True),d25.loc[~dev].reset_index(drop=True)
    grid=[{"spec":"market","penalty":None,"brier":brier(b,b["market"].to_numpy())}]
    for spec in SPECS:
        for penalty in PENALTIES:
            m=fit(a,spec,penalty);grid.append({"spec":spec,"penalty":penalty,"brier":brier(b,predict(b,m))})
    selected=min(grid,key=lambda r:(r["brier"],len(SPECS.get(r["spec"],())),r["spec"]))
    choices={s:min((x for x in grid if x["spec"]==s),key=lambda x:x["brier"]) for s in SPECS}
    OUT.mkdir(parents=True,exist_ok=True)
    final={s:fit(d25,s,c["penalty"]) for s,c in choices.items()}
    (OUT/"model_through2025.json").write_text(json.dumps(final,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print("WIDE_JOINT_SELECTION_LOCKED",json.dumps(selected),flush=True)
    full26=prepare_2026(safe);short=add_extras(prepare_longshot(full26),prior)
    frozen=V2_OUT/"model_through2025.txt";sha=hashlib.sha256(frozen.read_bytes()).hexdigest()
    ensure(sha==meta["modelHashes"][frozen.name],"frozen V2 digest")
    p26=predict_v2(short,lgb.Booster(model_file=str(frozen)),FEATURES,choice["weight"])
    rank26=rank_fit(pd.concat([train,val,hold],ignore_index=True),v7["objective"],v7["rounds"],2025)
    r26=blend(short,p26,rank_raw(short,rank26,v7["rounds"]),v7["alpha"])
    d,e26=pair_frame(full26,short,p26,r26)
    methods={"market":d["market"].to_numpy()}
    for s,m in final.items():methods[s]=predict(d,m)
    methods["selected"]=methods[selected["spec"]]
    quality=probability_report(d,methods)
    for s,p in methods.items():d[s]=p
    archive,inventory=payout_inventory(ROOT)
    covered,excluded=attach_payout(d,archive)
    result={"version":"longshot-wide-joint-v1-20261124","researchOnly":True,
        "candidatePromotionApproved":False,"formalPredictionAIChanged":False,"autoBetEnabled":False,
        "generatedAtJST":datetime.now(JST).isoformat(),"sourceHistorySha256":hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "v2ModelSha256":sha,"selected":selected,"grid2025":grid,"choices":choices,
        "trainingPairs2025":len(d25),"2026":{"races":int(d["raceID"].nunique()),"pairs":len(d),"hits":int(d["hit"].sum()),
            "excludedIncompleteOrTiedRaces":e26,"quality":quality,"historicalHitDiagnostics":settlement(d,METHODS)},
        "payoutArchive":inventory,"payoutExcluded":excluded,
        "payoutCommonSupport":{"races":int(covered["raceID"].nunique()),"pairs":len(covered),
            "firstDate":str(covered["date"].min().date()) if len(covered) else None,
            "lastDate":str(covered["date"].max().date()) if len(covered) else None,
            "settlement":settlement(covered,METHODS,True)},
        "limitations":["Final win odds and final field, not timestamped T10.",
            "At least one longshot per pair; no dead heats, >=8 runners; separate pair marginal estimates need not sum to three.",
            "Payouts only archived WideValidation rows with names and finish agreement; common-support subset selected by prior system, not full season.",
            "No estimated payout from win odds; no missing payout treated as zero; hypothetical fixed 100 yen each, not actual trades.",
            "2026 already examined; no promotion or proof of future profit. No actual pre-race wide price used in pair selection."]}
    (OUT/"research.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    d.to_csv(OUT/"pair_probabilities_2026.csv",index=False)
    covered.to_csv(OUT/"verified_payout_pairs_2026.csv",index=False)
    print("WIDE_JOINT_RESULT",json.dumps(result,ensure_ascii=False),flush=True)
    if args.coherent:
        coherent_models = coherent_report(full25, full26, d25, d, archive, {
            "sourceHistorySha256": result["sourceHistorySha256"], "v2ModelSha256": sha,
            "v7Selection": v7, "candidateSelectionYear": 2025,
        }, compare_portfolios=args.portfolio_selection)
        if args.odds_replay:
            from research_longshot_wide_odds_replay import run as replay
            replay(ROOT, OUT.with_name("longshot_wide_odds_replay"), coherent_models,
                   rank26, v7, datetime.now(JST))

if __name__=="__main__":
    main()
