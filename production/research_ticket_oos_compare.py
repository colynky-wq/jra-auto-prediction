from __future__ import annotations
# OOS_TRIGGER: 2026-10-31T research CI rerun; no production behavior change.
import json, math
from pathlib import Path
from itertools import permutations
import lightgbm as lgb
import numpy as np
import pandas as pd
from feature_engine import ALLF
from topk_runtime import market_top2_top3, normalize_probability_total, sigmoid, logit
from ticket_probability_runtime import distributions

HERE=Path(__file__).resolve().parent
HIST=HERE/'production_history.parquet'
WIN=HERE/'lightgbm_fixed_2025.txt'
TOP2=HERE/'lightgbm_top2_fixed_2025.txt'
TOP3=HERE/'lightgbm_top3_fixed_2025.txt'
OUT=HERE.parent/'reports'/'model_research'/'ticket_oos_compare_2026.json'
EPS=1e-12

def norm_race_market(g):
    if '市場確率' in g and g['市場確率'].notna().all(): q=g['市場確率'].to_numpy(float)
    else: q=1/g['単勝オッズ'].to_numpy(float)
    q=np.clip(q,EPS,None); return q/q.sum()

def pred_model(model,X,base,total):
    corr=model.predict(X,raw_score=True,validate_features=True)
    return normalize_probability_total(sigmoid(logit(base)+corr),total)

def pl_from_win(nums,w):
    w=np.asarray(w,float); w=w/w.sum(); n=len(w); ex={}; qn={}; tf={}; tr={}
    for i,j in permutations(range(n),2):
        p=w[i]*w[j]/(1-w[i]); ex[(nums[i],nums[j])]=p; u=tuple(sorted((nums[i],nums[j]))); qn[u]=qn.get(u,0)+p
    for i,j,k in permutations(range(n),3):
        p=w[i]*w[j]/(1-w[i])*w[k]/(1-w[i]-w[j]); key=(nums[i],nums[j],nums[k]); tf[key]=p; u=tuple(sorted(key)); tr[u]=tr.get(u,0)+p
    wd={pair:sum(p for key,p in tr.items() if pair[0] in key and pair[1] in key) for pair in qn}
    return {'wide':wd,'quinella':qn,'exacta':ex,'trio':tr,'trifecta':tf}

def event_key(kind, finish):
    a,b,c=finish[:3]
    if kind=='quinella': return tuple(sorted((a,b)))
    if kind=='exacta': return (a,b)
    if kind=='trio': return tuple(sorted((a,b,c)))
    if kind=='trifecta': return (a,b,c)
    return None

def bern(y,p): return -(y*math.log(max(p,EPS))+(1-y)*math.log(max(1-p,EPS)))
def multiclass_nll(p): return -math.log(max(p,EPS))

def main():
    d=pd.read_parquet(HIST); d['日付']=pd.to_datetime(d['日付'],errors='coerce')
    d=d[(d['日付']>='2026-01-01')&(d['日付']<='2026-10-25')].copy()
    required=set(ALLF+['レースID','馬番','着順','単勝オッズ'])
    miss=required-set(d.columns)
    if miss: raise RuntimeError(f'missing columns: {sorted(miss)}')
    wm=lgb.Booster(model_file=str(WIN)); m2=lgb.Booster(model_file=str(TOP2)); m3=lgb.Booster(model_file=str(TOP3))
    sums={k:{'base':0.0,'new':0.0,'n':0} for k in ['top2','top3','wide','quinella','exacta','trio','trifecta']}
    races=0; skipped=0
    for rid,g in d.groupby('レースID',sort=False):
        g=g.sort_values('馬番').reset_index(drop=True)
        if len(g)<3 or g['着順'].isna().any() or g['馬番'].duplicated().any(): skipped+=1; continue
        X=g[ALLF].astype(np.float32)
        if not np.isfinite(X.to_numpy()).all(): skipped+=1; continue
        market=norm_race_market(g)
        win=wm.predict(X,raw_score=True,validate_features=True)
        aiwin=sigmoid(g['市場ロジット'].to_numpy(float)+win); aiwin=aiwin/aiwin.sum()
        b2,b3=market_top2_top3(aiwin)
        n2=pred_model(m2,X,b2,2); n3=pred_model(m3,X,b3,3)
        nums=g['馬番'].astype(int).tolist()
        base=pl_from_win(nums,aiwin); new=distributions(nums,aiwin,n2,n3)
        finish=g.sort_values('着順')['馬番'].astype(int).tolist(); top2=set(finish[:2]); top3=set(finish[:3])
        for i,no in enumerate(nums):
            for kind,bb,nn,truth in [('top2',b2[i],n2[i],no in top2),('top3',b3[i],n3[i],no in top3)]:
                sums[kind]['base']+=bern(int(truth),float(bb)); sums[kind]['new']+=bern(int(truth),float(nn)); sums[kind]['n']+=1
        for pair,pb in base['wide'].items():
            y=int(pair[0] in top3 and pair[1] in top3); pn=new['wide'][pair]
            sums['wide']['base']+=bern(y,pb); sums['wide']['new']+=bern(y,pn); sums['wide']['n']+=1
        for kind in ['quinella','exacta','trio','trifecta']:
            key=event_key(kind,finish); sums[kind]['base']+=multiclass_nll(base[kind][key]); sums[kind]['new']+=multiclass_nll(new[kind][key]); sums[kind]['n']+=1
        races+=1
    out={'protocol':{'period':'2026-01-01..2026-10-25','baseline':'formal A win probabilities -> Plackett-Luce','candidate':'formal A win + fixed Top2/Top3 marginals -> fitted coherent ranking distribution','metric':'mean negative log likelihood; lower is better','promotionRule':'candidate must improve baseline (>0%) for the ticket family; no ROI claim without pre-race ticket odds'},'races':races,'skipped':skipped,'metrics':{}}
    family={'wide':'wide','quinella':'quinella','exacta':'exacta','trio':'trio','trifecta':'trifecta'}
    for k,v in sums.items():
        b=v['base']/v['n']; n=v['new']/v['n']; imp=(b-n)/b*100
        out['metrics'][k]={'n':v['n'],'baselineNLL':b,'candidateNLL':n,'improvementPct':imp,'pass':bool(imp>0)}
    out['shadowApproved']=[k for k in family if out['metrics'][k]['pass']]
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TICKET_OOS_BEGIN'); print(json.dumps(out,ensure_ascii=False,indent=2)); print('TICKET_OOS_END')
if __name__=='__main__': main()
