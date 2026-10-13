"""Pre-race field pace research. Select on 2024; open 2025 only after freeze."""
from __future__ import annotations
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from formation_runtime import relative_position, is_straight_race
from pace_baseline_runtime import eligible_record
from research_formation import bootstrap_interval

ROOT = Path(__file__).resolve().parent.parent
VERSION = 'prerace-field-pace-research-v1'
FEATURES = ['fieldSize','distanceM','earlyDistanceM','styleCoverage','meanPosition','minPosition','positionSpread','escapeCount','frontCount','escapeShare','frontShare','pastLapCoverage','pastLap600Mean','pastLap600Min','pastDistanceMean']
COLUMNS = ['レースID','日付','馬番','馬キー','初角','頭数','障害','芝ダ','距離']

def replay(history, laps):
    """Metadata whitelist; result updates happen after ALL races on a date."""
    lapmap = {r['raceID']:r for r in laps if eligible_record(r)}
    if len(lapmap) != sum(eligible_record(r) for r in laps):
        raise ValueError('duplicate lap race IDs')
    d = history[COLUMNS].copy()
    d['日付'] = pd.to_datetime(d['日付']).dt.normalize()
    d = d[(d['日付'].dt.year <= 2025) & ~d['障害'].fillna(False).astype(bool)]
    if d.duplicated(['レースID','馬番']).any(): raise ValueError('duplicate entrants')
    d = d.sort_values(['日付','レースID','馬番'])
    styles = defaultdict(lambda:deque(maxlen=6))
    clocks = defaultdict(lambda:deque(maxlen=6))
    output = []
    for day, races in d.groupby('日付', sort=False):
        updates = []
        for rid, g in races.groupby('レースID', sort=False):
            surface, distance = g['芝ダ'].iloc[0], int(g['距離'].iloc[0])
            count = int(g['頭数'].iloc[0])
            straight = is_straight_race(rid, surface, distance)
            known, clockvalues, source_dates = [], [], []
            for key in g['馬キー']:
                if pd.isna(key): continue
                w = styles[key]
                if len(w) >= 3:
                    positions = [v[1] for v in w]
                    known.append((float(np.mean(positions)), np.mean(np.array(positions)==0), np.mean(np.array(positions)<=.25)))
                    source_dates.extend(v[0] for v in w)
                c = clocks[key]
                if c:
                    clockvalues.append((np.mean([v[1] for v in c]),np.mean([v[2] for v in c])))
                    source_dates.extend(v[0] for v in c)
            if day.year in (2024,2025) and rid in lapmap:
                lap = lapmap[rid]
                if lap['raceDate'] != day.strftime('%Y-%m-%d') or lap['surface'] != surface or lap['distanceM'] != distance:
                    raise ValueError('lap/history metadata mismatch')
                early = 600 + (distance % 200)
                if lap['earlyRecordedDistanceM'] != early: raise ValueError('measurement point mismatch')
                pos = [v[0] for v in known]
                escape = sum(v[1]>=.5 for v in known)
                front = sum(v[2]>=.5 for v in known)
                coverage = len(known)/count
                row = dict(raceID=rid,day=day.strftime('%Y-%m-%d'),year=day.year,venue=rid[4:6],surface=surface,
                           fieldSize=count,distanceM=distance,earlyDistanceM=early,styleCoverage=coverage,
                           meanPosition=float(np.mean(pos)) if pos else .5,minPosition=min(pos) if pos else .5,
                           positionSpread=float(np.std(pos)) if pos else 0.,escapeCount=escape,frontCount=front,
                           escapeShare=escape/max(len(known),1),frontShare=front/max(len(known),1),
                           pastLapCoverage=len(clockvalues)/count,pastLap600Mean=float(np.mean([v[0] for v in clockvalues])) if clockvalues else 36.,
                           pastLap600Min=min([v[0] for v in clockvalues],default=36.),pastDistanceMean=float(np.mean([v[1] for v in clockvalues])) if clockvalues else distance,
                           latestSourceDay=max(source_dates).strftime('%Y-%m-%d') if source_dates else None,
                           eligible=not straight and count>=3 and len(known)>=3 and coverage>=.8,
                           actualSeconds=lap['earlyRecordedTimeSeconds'])
                if row['latestSourceDay'] and row['latestSourceDay'] >= row['day']: raise ValueError('future source')
                output.append(row)
            for key, corner, n in g[['馬キー','初角','頭数']].itertuples(index=False,name=None):
                if pd.isna(key): continue
                position = None if straight else relative_position(corner,n)
                lap = lapmap.get(rid)
                updates.append((key,position,lap,distance))
        for key, position, lap, distance in updates:
            if position is not None: styles[key].append((day,position))
            if lap: clocks[key].append((day,lap['earlyRecordedTimeSeconds']/lap['earlyRecordedDistanceM']*600,distance))
    return pd.DataFrame(output)

def key(row): return (row['venue'],row['surface'],int(row['distanceM']),int(row['earlyDistanceM']))

def references(rows):
    """All going combined: no post-result going or layout is a prediction input."""
    groups = defaultdict(list)
    for row in rows.to_dict('records'): groups[key(row)].append(row['actualSeconds'])
    return {k:np.quantile(v,[.25,.5,.75]) for k,v in groups.items() if len(v)>=30}

def prepare(rows, refs):
    r = rows[rows.eligible].copy()
    r = r[[key(v) in refs for v in r.to_dict('records')]].copy()
    for col, index in [('q25',0),('median',1),('q75',2)]: r[col] = [refs[key(v)][index] for v in r.to_dict('records')]
    r['target'] = r.actualSeconds-r['median']
    r['label'] = np.where(r.actualSeconds<r.q25,0,np.where(r.actualSeconds>r.q75,2,1))
    return r

def model(name):
    if name.startswith('ridge'): return make_pipeline(StandardScaler(),Ridge(alpha=float(name.split(':')[1])))
    return RandomForestRegressor(n_estimators=160,min_samples_leaf=int(name.split(':')[1]),max_features=.8,random_state=20261013,n_jobs=2)

def predict(train,test,name):
    if name=='courseMedian': return np.zeros(len(test))
    m=model(name); m.fit(train[FEATURES],train.target)
    return m.predict(test[FEATURES])

def metrics(test,pred,train):
    predicted = test['median'].to_numpy()+pred
    labels=np.where(predicted<test.q25,0,np.where(predicted>test.q75,2,1))
    y=test.label.to_numpy()
    # Probability baseline and residual empirical distribution are fitted on training only.
    errors=train.target.to_numpy()
    probabilities=[]
    for i,p in enumerate(predicted):
        q25,q75=test.q25.iloc[i],test.q75.iloc[i]
        p0=(np.sum(p+errors<q25)+1)/(len(errors)+3)
        p2=(np.sum(p+errors>q75)+1)/(len(errors)+3)
        probabilities.append([p0,1-p0-p2,p2])
    probs=np.array(probabilities)
    brier=np.sum((probs-np.eye(3)[y])**2,axis=1)
    abs_error=np.abs(predicted-test.actualSeconds.to_numpy())
    return dict(races=len(test),accuracyPct=round(float(np.mean(labels==y))*100,2),balancedAccuracyPct=round(float(np.mean([np.mean(labels[y==c]==c) for c in range(3) if np.any(y==c)]))*100,2),meanAbsoluteErrorSeconds=round(float(abs_error.mean()),4),brierScore=round(float(brier.mean()),6),observedCounts={str(c):int(sum(y==c)) for c in range(3)},predictedCounts={str(c):int(sum(labels==c)) for c in range(3)},recallPct={str(c):round(float(np.mean(labels[y==c]==c))*100,2) if np.any(y==c) else None for c in range(3)}), abs_error,brier,labels

def evaluate(test,train,pred):
    result,error,brier,labels=metrics(test,pred,train)
    base,baseerror,basebrier,_=metrics(test,np.zeros(len(test)),train)
    daily=pd.DataFrame({'day':test.day,'error':baseerror-error,'brier':basebrier-brier,'accuracy':(labels==test.label.to_numpy()).astype(float)-(test.label.to_numpy()==1).astype(float),'n':1}).groupby('day').sum()
    result['courseMedianBaseline']=base
    result['alwaysMiddleAccuracyPct']=round(float(np.mean(test.label==1))*100,2)
    result['timeImprovementSeconds95CI']=bootstrap_interval(daily,'error','n')
    result['brierImprovement95CI']=bootstrap_interval(daily,'brier','n')
    result['accuracyImprovementVsMiddle95CI']=bootstrap_interval(daily,'accuracy','n')
    result['confusionMatrixActualRowsPredictedColumns']=[[int(np.sum((test.label.to_numpy()==a)&(labels==b))) for b in range(3)] for a in range(3)]
    result['byCondition']={}
    z=test.assign(correct=labels==test.label,error=error,baseError=baseerror)
    for field in ['surface','venue','distanceM']:
        result['byCondition'][field]=[{field:str(k),'races':len(g),'accuracyPct':round(float(g.correct.mean())*100,2),'errorSeconds':round(float(g.error.mean()),4),'baselineErrorSeconds':round(float(g.baseError.mean()),4)} for k,g in z.groupby(field)]
    return result

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--development-only',action='store_true'); args=parser.parse_args()
    laps=[]
    for p in sorted((ROOT/'research/laps').glob('*/RaceLapData.json')): laps.extend(json.loads(p.read_text())['races'])
    hpath=ROOT/'production/production_history.parquet'
    rows=replay(pd.read_parquet(hpath,columns=COLUMNS),laps)
    r24=rows[rows.year==2024]
    early=r24[r24.day<'2024-09-01']; later=r24[r24.day>='2024-09-01']
    refs=references(early); train=prepare(early,refs); val=prepare(later,refs)
    names=['courseMedian','ridge:10','ridge:100','forest:20','forest:40']
    development={name:evaluate(val,train,predict(train,val,name)) for name in names}
    selected=min(names,key=lambda name:development[name]['meanAbsoluteErrorSeconds'])
    freeze={'methodVersion':VERSION,'selectedModel':selected,'features':FEATURES,'selectionMetric':'2024 Sep-Dec MAE, Jan-Aug training only','development2024':development,'developmentTrainingRaces':len(train),'developmentValidationRaces':len(val),'minimumConditionRaces':30,'adoptionRule':'non-baseline model; held-out time and Brier improvement 95% CI both above zero; fast and slow recall each at least 20%', 'goingUsedAsInput':False,'sameDayResultsUsed':False,'minimumStyleHistoryRaces':3,'minimumStyleCoverage':.8,'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}
    out=ROOT/'reports/PreracePaceMethodFreeze.json'; out.write_text(json.dumps(freeze,ensure_ascii=False,indent=2)+'\n')
    if args.development_only:
        print(json.dumps({k:freeze[k] for k in ['selectedModel','developmentTrainingRaces','developmentValidationRaces','development2024']},ensure_ascii=False)); return
    # Model/feature/threshold choices above never inspect the held-out year.
    refs=references(r24); train=prepare(r24,refs); test=prepare(rows[rows.year==2025],refs)
    prediction=predict(train,test,selected)
    report={'methodVersion':VERSION,'status':'research_only','methodFreezeSHA256':hashlib.sha256(out.read_bytes()).hexdigest(),'selectedModel':selected,'trainingRaces':len(train),'evaluation2025':evaluate(test,train,prediction),'collectionRaces2025':int(sum(rows.year==2025)),'styleEligibleRaces2025':int(sum((rows.year==2025)&rows.eligible)),'conditionAndStyleCoveragePct2025':round(len(test)/sum(rows.year==2025)*100,2),'latestSourceDateViolations':int(sum(rows.latestSourceDay.fillna('')>=rows.day)),'historySHA256':hashlib.sha256(hpath.read_bytes()).hexdigest(),'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False,'limitations':['Historical result entrants and field size are used; scratches and pre-race snapshot timing are not reconstructed.','Going and inner/outer layout are excluded because reliable pre-race snapshots are absent. Labels use a separate all-going course reference, not the earlier going-specific reference.','Past laps describe race-leader pace, not the individual horse speed.','2023 and earlier lap histories are unavailable; early 2024 has less past-lap coverage.','The probability estimate uses empirical training target residuals, not out-of-fold calibrated errors.','No winner, odds, payouts, current-race position, lap, finish time, or same-day results enter model inputs.','2025 was previously used for course-reference research, but never for selecting this pre-race method.','No 2026 data are used.']}
    ev=report['evaluation2025']; accept=selected!='courseMedian' and ev['timeImprovementSeconds95CI'][0]>0 and ev['brierImprovement95CI'][0]>0 and min(ev['recallPct']['0'],ev['recallPct']['2'])>=20
    report['excluded2025']={'insufficientStyleHistoryOrStraight':int(sum(rows.year==2025))-int(sum((rows.year==2025)&rows.eligible)), 'insufficientCourseReferenceAmongStyleEligible':int(sum((rows.year==2025)&rows.eligible))-len(test)}
    report['adoptionDecision']='requires_prospective_validation' if accept else 'do_not_enable_pace_forecast'
    test.assign(predictedSeconds=test['median'].to_numpy()+prediction)[['raceID','day','latestSourceDay','styleCoverage','pastLapCoverage','actualSeconds','predictedSeconds','label']].to_json(ROOT/'reports/PreracePaceEvaluationRows.json',orient='records',force_ascii=False,indent=2)
    (ROOT/'reports/PreracePaceValidation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
