"""Direct pace classification: select on 2024, train through 2025, test 2026."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from research_prerace_pace import FEATURES, COLUMNS, replay, references, prepare
from research_formation import bootstrap_interval

ROOT=Path(__file__).resolve().parent.parent
VERSION='prerace-pace-direct-classifier-v2'
CUTOFF='2026-10-12'
CATEGORIES=['venue','surface','raceClass']
CANDIDATES=['coursePrior','logistic:0.1','logistic:1','forest:20','forest:40']

def attach_class(rows,history):
    groups=history.groupby('レースID')['クラス']
    if (groups.nunique(dropna=False)>1).any():raise ValueError('race class differs among runners')
    r=rows.copy();r['raceClass']=r.raceID.map(groups.first()).fillna('unknown')
    return r

def probabilities(train,test,name):
    global_prior=np.bincount(train.label,minlength=3)/len(train)
    if name=='coursePrior':
        groups={k:np.bincount(g.label,minlength=3) for k,g in train.groupby(['venue','surface','distanceM','earlyDistanceM'])}
        return np.array([(groups.get((r.venue,r.surface,r.distanceM,r.earlyDistanceM),np.zeros(3))+20*global_prior)/(sum(groups.get((r.venue,r.surface,r.distanceM,r.earlyDistanceM),np.zeros(3)))+20) for r in test.itertuples()])
    numeric=FEATURES
    pre=ColumnTransformer([('numeric',StandardScaler(),numeric),('category',OneHotEncoder(handle_unknown='ignore',sparse_output=False),CATEGORIES)])
    if name.startswith('logistic:'):
        estimator=LogisticRegression(C=float(name.split(':')[1]),max_iter=3000,random_state=20261013)
    else:
        estimator=RandomForestClassifier(n_estimators=180,min_samples_leaf=int(name.split(':')[1]),class_weight='balanced',max_features=.8,random_state=20261013,n_jobs=2)
    m=make_pipeline(pre,estimator);m.fit(train[numeric+CATEGORIES],train.label)
    p=m.predict_proba(test[numeric+CATEGORIES]);aligned=np.zeros((len(test),3));aligned[:,m[-1].classes_.astype(int)]=p
    return aligned

def measures(test,p):
    y=test.label.to_numpy();pred=p.argmax(axis=1);brier=np.sum((p-np.eye(3)[y])**2,axis=1);correct=pred==y
    return {'races':len(test),'accuracyPct':round(float(correct.mean())*100,2),'balancedAccuracyPct':round(float(np.mean([np.mean(pred[y==c]==c) for c in range(3) if sum(y==c)]))*100,2),'brierScore':round(float(brier.mean()),6),'actualCounts':dict(Counter(str(v) for v in y)),'predictedCounts':dict(Counter(str(v) for v in pred)),'recallPct':{str(c):round(float(np.mean(pred[y==c]==c))*100,2) if sum(y==c) else None for c in range(3)},'confusionMatrixActualRowsPredictedColumns':[[int(sum((y==a)&(pred==b))) for b in range(3)] for a in range(3)]},brier,correct

def evaluate(train,test,name):
    p=probabilities(train,test,name);base=probabilities(train,test,'coursePrior')
    result,brier,correct=measures(test,p);b,basebrier,basecorrect=measures(test,base)
    result['coursePriorBaseline']=b
    result['alwaysMiddleAccuracyPct']=round(float(np.mean(test.label==1))*100,2)
    d=pd.DataFrame({'day':test.day,'brier':basebrier-brier,'accuracy':correct.astype(float)-basecorrect.astype(float),'middle':correct.astype(float)-(test.label==1).astype(float),'n':1}).groupby('day').sum()
    result['brierImprovementVsCoursePrior95CI']=bootstrap_interval(d,'brier','n')
    result['accuracyImprovementVsCoursePrior95CI']=bootstrap_interval(d,'accuracy','n')
    result['accuracyImprovementVsMiddle95CI']=bootstrap_interval(d,'middle','n')
    result['byCondition']={}
    r=test.assign(correct=correct,brier=brier,baseBrier=basebrier)
    for field in ['surface','venue','distanceM','raceClass']:
        result['byCondition'][field]=[{field:str(k),'races':len(g),'accuracyPct':round(float(g.correct.mean())*100,2),'brierScore':round(float(g.brier.mean()),6),'baselineBrierScore':round(float(g.baseBrier.mean()),6)} for k,g in r.groupby(field)]
    return result

def develop(rows):
    r24=rows[rows.year==2024];early=r24[r24.day<'2024-09-01'];late=r24[r24.day>='2024-09-01']
    refs=references(early);train=prepare(early,refs);val=prepare(late,refs)
    results={name:evaluate(train,val,name) for name in CANDIDATES}
    preferred=min(CANDIDATES,key=lambda name:results[name]['brierScore'])
    selected=min(CANDIDATES[1:],key=lambda name:results[name]['brierScore'])
    return {'schemaVersion':1,'methodVersion':VERSION,'selectedModel':selected,'developmentPreferredMethod':preferred,'developmentCandidateQualified':preferred!='coursePrior','numericFeatures':FEATURES,'categoricalFeatures':CATEGORIES,'candidateModels':CANDIDATES,'selectionRule':'Minimum Brier score on Sep-Dec 2024; Jan-Aug 2024 training and reference thresholds only. No 2025 or 2026 selection.','trainingYearsAfterFreeze':[2024,2025],'evaluationYear':2026,'evaluationCutoff':CUTOFF,'development2024':results,'adoptionRule':'non-baseline; held-out Brier and accuracy improvements vs course prior have positive lower 95% day-bootstrap limits; fast and slow recall each >=20%; at least 1000 evaluation races. Still requires prospective snapshots.','paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--development-only',action='store_true');args=parser.parse_args()
    laps=[]
    for p in sorted((ROOT/'research/laps').glob('*/RaceLapData.json')):laps.extend(json.loads(p.read_text())['races'])
    hpath=ROOT/'production/production_history.parquet';h=pd.read_parquet(hpath,columns=COLUMNS+['クラス'])
    rows=attach_class(replay(h,laps,last_year=2025 if args.development_only else 2026),h)
    freeze=develop(rows);fpath=ROOT/'reports/PaceClassifierMethodFreeze.json'
    fpath.write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+'\n')
    if args.development_only:
        print(json.dumps({k:freeze[k] for k in ['selectedModel','development2024']},ensure_ascii=False));return
    expected=h[(pd.to_datetime(h['日付']).dt.year==2026)&(pd.to_datetime(h['日付'])<=CUTOFF)&~h['障害'].fillna(False).astype(bool)]['レースID'].nunique()
    collected=rows[(rows.year==2026)&(rows.day<=CUTOFF)]
    if len(collected)!=expected:raise ValueError(f'2026 collection incomplete: {len(collected)}/{expected}; do not open partial held-out evaluation')
    refs=references(rows[rows.year==2024]);train=prepare(rows[rows.year<=2025],refs);test=prepare(collected,refs)
    result=evaluate(train,test,freeze['selectedModel'])
    accept=freeze['developmentCandidateQualified'] and freeze['selectedModel']!='coursePrior' and len(test)>=1000 and result['brierImprovementVsCoursePrior95CI'][0]>0 and result['accuracyImprovementVsCoursePrior95CI'][0]>0 and min(result['recallPct']['0'],result['recallPct']['2'])>=20
    report={'schemaVersion':1,'methodVersion':VERSION,'selectedModel':freeze['selectedModel'],'developmentPreferredMethod':freeze['developmentPreferredMethod'],'developmentCandidateQualified':freeze['developmentCandidateQualified'],'methodFreezeSHA256':hashlib.sha256(fpath.read_bytes()).hexdigest(),'historySHA256':hashlib.sha256(hpath.read_bytes()).hexdigest(),'lapDataSHA256':hashlib.sha256(json.dumps(sorted(laps,key=lambda x:x['raceID']),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'trainingRaces':len(train),'expected2026ThroughCutoff':expected,'collected2026ThroughCutoff':len(collected),'evaluation2026':result,'evaluationCoveragePct':round(len(test)/expected*100,2),'latestSourceDateViolations':int(sum(rows.latestSourceDay.fillna('')>=rows.day)),'adoptionDecision':'requires_prospective_validation' if accept else 'do_not_enable_pace_forecast','paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False,'limitations':['2025 is training, not an independent evaluation; 2026 is opened only after 2024 selection and complete collection.','Current race class, entrants and count are from historical result metadata; pre-race snapshot timing and scratches are not reconstructed.','Going and inner/outer course layout are not inputs. Thresholds combine all going and are fixed on 2024; distinct from older going-specific labels.','Past laps are race-leader pace, not individual horse speed; 2023 and earlier lap histories missing.','Probabilities are not independently calibrated; no live adoption from historical accuracy alone.','Bootstrap by calendar day preserves shared race-day effects; condition breakdowns are descriptive, not selection criteria.']}
    (ROOT/'reports/PaceClassifierValidation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
