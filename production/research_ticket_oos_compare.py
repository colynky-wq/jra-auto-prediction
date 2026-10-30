from __future__ import annotations
import json, math
from pathlib import Path
from itertools import permutations
import lightgbm as lgb
import numpy as np
import pandas as pd
from feature_engine import ALLF, add_features, add_rating
from topk_runtime import market_top2_top3, normalize_probability_total, sigmoid, logit
from ticket_probability_runtime import fit_race_weights, wide_pair_matrix
HERE=Path(__file__).resolve().parent
HIST=HERE/'production_history.parquet'; WIN=HERE/'lightgbm_fixed_2025.txt'; TOP2=HERE/'lightgbm_top2_fixed_2025.txt'; TOP3=HERE/'lightgbm_top3_fixed_2025.txt'
OUT=HERE.parent/'reports'/'model_research'/'ticket_oos_compare_2026.json'; EPS=1e-12
START=pd.Timestamp('2026-01-01'); END=pd.Timestamp('2026-10-25')
def bern(y,p):
    p=min(max(float(p),EPS),1-EPS); return -(y*math.log(p)+(1-y)*math.log(1-p))
def multiclass_nll(p): return -math.log(max(float(p),EPS))
def ordered2(w,i,j): return float(w[i]*w[j]/max(1-w[i],EPS))
def ordered3(w,i,j,k): return float(w[i]*w[j]/max(1-w[i],EPS)*w[k]/max(1-w[i]-w[j],EPS))
def pair_unordered(w,i,j): return ordered2(w,i,j)+ordered2(w,j,i)
def triple_unordered(w,i,j,k): return sum(ordered3(w,*p) for p in permutations((i,j,k),3))
def prepare_oos():
    d=pd.read_parquet(HIST); d['日付']=pd.to_datetime(d['日付'],errors='coerce'); d=d.sort_values(['日付','レースID','馬番']).reset_index(drop=True)
    miss={'日付','レースID','馬番','着順','単勝オッズ','馬キー'}-set(d.columns)
    if miss: raise RuntimeError(f'missing raw columns: {sorted(miss)}')
    d=add_rating(d); d=add_features(d)
    d=d[(d['日付']>=START)&(d['日付']<=END)].copy()
    eligible=d['対象'].fillna(False).astype(bool)&d['着順'].notna()&d['市場確率'].notna()&(~d['障害'].fillna(False).astype(bool))
    d=d.loc[eligible].copy(); numeric=d[ALLF].apply(pd.to_numeric,errors='coerce'); d=d.loc[np.isfinite(numeric.to_numpy(float)).all(axis=1)].copy()
    return d.reset_index(drop=True)
def main():
    d=prepare_oos(); wm=lgb.Booster(model_file=str(WIN)); m2=lgb.Booster(model_file=str(TOP2)); m3=lgb.Booster(model_file=str(TOP3))
    # Raw LightGBM corrections depend only on each row's fixed features. Predict
    # the full OOS frame once per model instead of paying per-race Booster call
    # overhead thousands of times; race-level normalization remains unchanged.
    Xall=d[ALLF].astype(np.float32)
    d['_win_corr']=wm.predict(Xall,raw_score=True,validate_features=True)
    d['_top2_corr']=m2.predict(Xall,raw_score=True,validate_features=True)
    d['_top3_corr']=m3.predict(Xall,raw_score=True,validate_features=True)
    sums={k:{'base':0.0,'new':0.0,'n':0} for k in ['top2','top3','wide','quinella','exacta','trio','trifecta']}; races=0; skipped=0
    for _,g in d.groupby('レースID',sort=False):
        g=g.sort_values('馬番').reset_index(drop=True)
        if len(g)<3 or g['着順'].isna().any() or g['馬番'].duplicated().any(): skipped+=1; continue
        market=g['市場確率'].to_numpy(float); market=market/market.sum()
        raw=g['_win_corr'].to_numpy(float); aiwin=normalize_probability_total(sigmoid(g['市場ロジット'].to_numpy(float)+raw),1)
        a2,a3=market_top2_top3(aiwin); market2,market3=market_top2_top3(market)
        n2=normalize_probability_total(sigmoid(logit(market2)+g['_top2_corr'].to_numpy(float)),2)
        n3=normalize_probability_total(sigmoid(logit(market3)+g['_top3_corr'].to_numpy(float)),3)
        nw=fit_race_weights(aiwin,n2,n3); bw=np.asarray(aiwin,float); bw=bw/bw.sum()
        nums=g['馬番'].astype(int).tolist(); pos={no:i for i,no in enumerate(nums)}
        finish=g.sort_values('着順')['馬番'].astype(int).tolist(); top2=set(finish[:2]); top3=set(finish[:3])
        for i,no in enumerate(nums):
            for kind,bb,nn,truth in [('top2',a2[i],n2[i],no in top2),('top3',a3[i],n3[i],no in top3)]:
                sums[kind]['base']+=bern(int(truth),bb); sums[kind]['new']+=bern(int(truth),nn); sums[kind]['n']+=1
        # Compute all Wide pair probabilities once for each ranking vector. This
        # is mathematically identical to summing every third horse/order for each
        # pair separately, but avoids repeated Python triple enumeration.
        bwide=wide_pair_matrix(bw); nwide=wide_pair_matrix(nw)
        for i in range(len(nums)):
            for j in range(i+1,len(nums)):
                y=int(nums[i] in top3 and nums[j] in top3)
                sums['wide']['base']+=bern(y,bwide[i,j]); sums['wide']['new']+=bern(y,nwide[i,j]); sums['wide']['n']+=1
        i,j,k=(pos[finish[x]] for x in range(3))
        probs={
          'quinella':(pair_unordered(bw,i,j),pair_unordered(nw,i,j)),
          'exacta':(ordered2(bw,i,j),ordered2(nw,i,j)),
          'trio':(triple_unordered(bw,i,j,k),triple_unordered(nw,i,j,k)),
          'trifecta':(ordered3(bw,i,j,k),ordered3(nw,i,j,k)),
        }
        for kind,(pb,pn) in probs.items():
            sums[kind]['base']+=multiclass_nll(pb); sums[kind]['new']+=multiclass_nll(pn); sums[kind]['n']+=1
        races+=1
    if not races: raise RuntimeError('no eligible OOS races')
    out={'protocol':{'period':f'{START.date()}..{END.date()}','baseline':'formal A win probabilities -> Plackett-Luce','candidate':'formal A win + fixed Top2/Top3 market-baseline corrections -> fitted coherent ranking distribution','metric':'mean negative log likelihood; lower is better','promotionRule':'candidate must improve baseline (>0%) for the ticket family; no ROI claim without pre-race ticket odds'},'races':races,'skipped':skipped,'metrics':{}}
    for k,v in sums.items():
        b=v['base']/v['n']; n=v['new']/v['n']; imp=(b-n)/b*100; out['metrics'][k]={'n':v['n'],'baselineNLL':b,'candidateNLL':n,'improvementPct':imp,'pass':bool(imp>0)}
    out['shadowApproved']=[k for k in ['wide','quinella','exacta','trio','trifecta'] if out['metrics'][k]['pass']]
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TICKET_OOS_BEGIN'); print(json.dumps(out,ensure_ascii=False,indent=2)); print('TICKET_OOS_END')
if __name__=='__main__': main()
