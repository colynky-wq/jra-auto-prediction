from __future__ import annotations
import json, math
from pathlib import Path
from itertools import permutations
import lightgbm as lgb
import numpy as np
import pandas as pd
from feature_engine import ALLF, add_features, add_rating
from topk_runtime import market_top2_top3, normalize_probability_total, sigmoid, logit
from ticket_probability_runtime import distributions
HERE=Path(__file__).resolve().parent
HIST=HERE/'production_history.parquet'; WIN=HERE/'lightgbm_fixed_2025.txt'; TOP2=HERE/'lightgbm_top2_fixed_2025.txt'; TOP3=HERE/'lightgbm_top3_fixed_2025.txt'
OUT=HERE.parent/'reports'/'model_research'/'ticket_oos_compare_2026.json'; EPS=1e-12
START=pd.Timestamp('2026-01-01'); END=pd.Timestamp('2026-10-25')
def pred_model(model,X,base,total):
    corr=model.predict(X,raw_score=True,validate_features=True); return normalize_probability_total(sigmoid(logit(base)+corr),total)
def pl_from_win(nums,w):
    w=np.asarray(w,float); w=w/w.sum(); n=len(w); ex={}; qn={}; tf={}; tr={}
    for i,j in permutations(range(n),2):
        p=w[i]*w[j]/max(1-w[i],EPS); ex[(nums[i],nums[j])]=p; u=tuple(sorted((nums[i],nums[j]))); qn[u]=qn.get(u,0)+p
    for i,j,k in permutations(range(n),3):
        p=w[i]*w[j]/max(1-w[i],EPS)*w[k]/max(1-w[i]-w[j],EPS); key=(nums[i],nums[j],nums[k]); tf[key]=p; u=tuple(sorted(key)); tr[u]=tr.get(u,0)+p
    wd={pair:sum(p for key,p in tr.items() if pair[0] in key and pair[1] in key) for pair in qn}
    return {'wide':wd,'quinella':qn,'exacta':ex,'trio':tr,'trifecta':tf}
def event_key(kind,finish):
    a,b,c=finish[:3]
    return tuple(sorted((a,b))) if kind=='quinella' else (a,b) if kind=='exacta' else tuple(sorted((a,b,c))) if kind=='trio' else (a,b,c) if kind=='trifecta' else None
def bern(y,p):
    p=min(max(float(p),EPS),1-EPS); return -(y*math.log(p)+(1-y)*math.log(1-p))
def multiclass_nll(p): return -math.log(max(float(p),EPS))
def prepare_oos():
    d=pd.read_parquet(HIST); d['日付']=pd.to_datetime(d['日付'],errors='coerce'); d=d.sort_values(['日付','レースID','馬番']).reset_index(drop=True)
    miss={'日付','レースID','馬番','着順','単勝オッズ','馬キー'}-set(d.columns)
    if miss: raise RuntimeError(f'missing raw columns: {sorted(miss)}')
    # Build features on the complete chronology so prior starts are available.
    d=add_rating(d); d=add_features(d)
    d=d[(d['日付']>=START)&(d['日付']<=END)].copy()
    eligible=d['対象'].fillna(False).astype(bool)&d['着順'].notna()&d['市場確率'].notna()&(~d['障害'].fillna(False).astype(bool))
    d=d.loc[eligible].copy(); numeric=d[ALLF].apply(pd.to_numeric,errors='coerce'); d=d.loc[np.isfinite(numeric.to_numpy(float)).all(axis=1)].copy()
    return d.reset_index(drop=True)
def main():
    d=prepare_oos(); wm=lgb.Booster(model_file=str(WIN)); m2=lgb.Booster(model_file=str(TOP2)); m3=lgb.Booster(model_file=str(TOP3))
    sums={k:{'base':0.0,'new':0.0,'n':0} for k in ['top2','top3','wide','quinella','exacta','trio','trifecta']}; races=0; skipped=0
    for _,g in d.groupby('レースID',sort=False):
        g=g.sort_values('馬番').reset_index(drop=True)
        if len(g)<3 or g['着順'].isna().any() or g['馬番'].duplicated().any(): skipped+=1; continue
        X=g[ALLF].astype(np.float32); market=g['市場確率'].to_numpy(float); market=market/market.sum()
        raw=wm.predict(X,raw_score=True,validate_features=True); aiwin=normalize_probability_total(sigmoid(g['市場ロジット'].to_numpy(float)+raw),1)
        a2,a3=market_top2_top3(aiwin); market2,market3=market_top2_top3(market)
        # Dedicated Top2/Top3 models correct market-derived marginals in production.
        n2=pred_model(m2,X,market2,2); n3=pred_model(m3,X,market3,3)
        nums=g['馬番'].astype(int).tolist(); base=pl_from_win(nums,aiwin); new=distributions(nums,aiwin,n2,n3)
        finish=g.sort_values('着順')['馬番'].astype(int).tolist(); top2=set(finish[:2]); top3=set(finish[:3])
        for i,no in enumerate(nums):
            for kind,bb,nn,truth in [('top2',a2[i],n2[i],no in top2),('top3',a3[i],n3[i],no in top3)]:
                sums[kind]['base']+=bern(int(truth),bb); sums[kind]['new']+=bern(int(truth),nn); sums[kind]['n']+=1
        for pair,pb in base['wide'].items():
            y=int(pair[0] in top3 and pair[1] in top3); sums['wide']['base']+=bern(y,pb); sums['wide']['new']+=bern(y,new['wide'][pair]); sums['wide']['n']+=1
        for kind in ['quinella','exacta','trio','trifecta']:
            key=event_key(kind,finish); sums[kind]['base']+=multiclass_nll(base[kind][key]); sums[kind]['new']+=multiclass_nll(new[kind][key]); sums[kind]['n']+=1
        races+=1
    if not races: raise RuntimeError('no eligible OOS races')
    out={'protocol':{'period':f'{START.date()}..{END.date()}','baseline':'formal A win probabilities -> Plackett-Luce','candidate':'formal A win + fixed Top2/Top3 market-baseline corrections -> fitted coherent ranking distribution','metric':'mean negative log likelihood; lower is better','promotionRule':'candidate must improve baseline (>0%) for the ticket family; no ROI claim without pre-race ticket odds'},'races':races,'skipped':skipped,'metrics':{}}
    for k,v in sums.items():
        b=v['base']/v['n']; n=v['new']/v['n']; imp=(b-n)/b*100; out['metrics'][k]={'n':v['n'],'baselineNLL':b,'candidateNLL':n,'improvementPct':imp,'pass':bool(imp>0)}
    out['shadowApproved']=[k for k in ['wide','quinella','exacta','trio','trifecta'] if out['metrics'][k]['pass']]
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TICKET_OOS_BEGIN'); print(json.dumps(out,ensure_ascii=False,indent=2)); print('TICKET_OOS_END')
if __name__=='__main__': main()
