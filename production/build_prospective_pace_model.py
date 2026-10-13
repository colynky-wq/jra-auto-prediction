"""Develop season context on used history, then export a fixed research JSON model."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from research_prerace_pace import FEATURES
from research_pace_classifier import CATEGORIES,measures,CUTOFF
from prerace_snapshot_runtime import JST
from pace_prospective_runtime import softmax_predict
from research_slow_pace import threshold_stats
ROOT=Path(__file__).resolve().parent.parent
VERSION='prospective-pace-research-v3'

def fit(train,numeric,c):
    pre=ColumnTransformer([('numeric',StandardScaler(),numeric),('category',OneHotEncoder(handle_unknown='ignore',sparse_output=False),CATEGORIES)])
    m=make_pipeline(pre,LogisticRegression(C=c,max_iter=3000,random_state=20261013));m.fit(train[numeric+CATEGORIES],train.label);return m

def main():
    d=pd.read_pickle(ROOT/'tmp/pace-prospective-training.pkl')
    month=pd.to_datetime(d.day).dt.month
    d['seasonSin']=np.sin(2*np.pi*month/12);d['seasonCos']=np.cos(2*np.pi*month/12);d['raceNumber']=d.raceID.str[-2:].astype(int)
    train=d[d.year<=2025];development=d[d.year==2026]
    candidates={'base:0.1':(FEATURES,.1),'season:0.1':(FEATURES+['seasonSin','seasonCos','raceNumber'],.1),'season:1':(FEATURES+['seasonSin','seasonCos','raceNumber'],1.)}
    results={}
    for name,(numeric,c) in candidates.items():results[name]=measures(development,fit(train,numeric,c).predict_proba(development[numeric+CATEGORIES]))[0]
    selected=min(candidates,key=lambda name:results[name]['brierScore']);numeric,c=candidates[selected]
    development_p=fit(train,numeric,c).predict_proba(development[numeric+CATEGORIES])
    grid=[threshold_stats(development.label.to_numpy(),development_p[:,2],t) for t in [.20,.25,.30,.35,.40,.45,.50]]
    eligible=[r for r in grid if r['calledRaces']>=30 and r['precisionPct']>=50 and r['recallPct']>=20]
    slow_threshold=max(eligible,key=lambda r:r['recallPct'])['threshold'] if eligible else None
    m=fit(d,numeric,c);pre=m[0];scaler=pre.named_transformers_['numeric'];ohe=pre.named_transformers_['category'];estimator=m[-1]
    refs=json.loads((ROOT/'tmp/pace-prospective-references.json').read_text());prior=np.bincount(d.label,minlength=3)/len(d)
    for ref in refs:
        venue,surface,distance,point=ref['condition'];g=d[(d.venue==venue)&(d.surface==surface)&(d.distanceM==distance)&(d.earlyDistanceM==point)]
        counts=np.bincount(g.label,minlength=3);ref['prior']=((counts+20*prior)/(len(g)+20)).tolist()
    model={'schemaVersion':1,'methodVersion':VERSION,'trainingCutoffDate':CUTOFF,'numericFeatures':numeric,'categoricalFeatures':CATEGORIES,'numericMean':scaler.mean_.tolist(),'numericScale':scaler.scale_.tolist(),'categories':[v.tolist() for v in ohe.categories_],'coefficients':estimator.coef_.tolist(),'intercepts':estimator.intercept_.tolist(),'classes':estimator.classes_.tolist(),'references':refs,'slowFlagThreshold':slow_threshold,'selectedDevelopmentCandidate':selected,'trainingRaces':len(d),'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}
    model['modelID']=hashlib.sha256(json.dumps(model,sort_keys=True,ensure_ascii=False).encode()).hexdigest();model['fittedAtJST']=datetime.now(JST).isoformat(timespec='microseconds')
    test=d.sample(100,random_state=20261013)
    exported=np.array([softmax_predict(model,row) for row in test.to_dict('records')]);expected=m.predict_proba(test[numeric+CATEGORIES]);error=float(np.max(np.abs(exported-expected)))
    if error>1e-12:raise ValueError('exported JSON inference differs from fitted classifier')
    archive=ROOT/'research/pace/models'/(model['modelID']+'.json');archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists():
        saved=json.loads(archive.read_text())
        model['fittedAtJST']=saved['fittedAtJST']
    else:
        with archive.open('x',encoding='utf-8') as f:json.dump(model,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    out=ROOT/'research/pace/ProspectivePaceModel.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(model,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    report={'schemaVersion':1,'methodVersion':VERSION,'status':'prospective_observations_pending','independentEvaluation':False,'developmentThrough':CUTOFF,'candidateResultsOnUsed2026':results,'selectedCandidate':selected,'trainingRaces':len(d),'modelID':model['modelID'],'jsonInferenceMaximumDifference':error,'selectedCandidateSlowThresholdGrid':grid,'slowFlagThreshold':slow_threshold,'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False,'meaning':'2026 was already evaluated. These comparisons are development only; independent evaluation requires forecasts recorded before future races.'}
    (ROOT/'reports/PaceProspectiveDevelopment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
