"""Score only immutable predictions actually saved before scheduled post."""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research_formation import bootstrap_interval
from pace_prospective_runtime import LABELS
from pace_baseline_runtime import eligible_record
ROOT=Path(__file__).resolve().parent.parent
START='2026-10-13'

def check_prediction(pred,snapshot,lap,final_numbers):
    times=[datetime.fromisoformat(pred[k]) for k in ['snapshotCapturedAtJST','inputAvailableAtJST','predictedAtJST','scheduledPostJST','modelFittedAtJST']]
    capture,available,generated,post,fitted=times
    if any(t.tzinfo is None for t in times) or not capture<=available<=generated<post or not fitted<=generated or generated.date().isoformat()!=pred['raceDate'] or pred['trainingCutoffDate']>=pred['raceDate']:raise ValueError('prediction timing is not prospective')
    if datetime.fromisoformat(snapshot.get('completeInputAvailableAtJST',snapshot['capturedAtJST']))!=available or not snapshot.get('prePostConfirmed'):raise ValueError('snapshot availability mismatch')
    if snapshot['raceID']!=pred['raceID'] or snapshot['raceDate']!=pred['raceDate'] or snapshot['capturedAtJST']!=pred['snapshotCapturedAtJST'] or snapshot['scheduledPostJST']!=pred['scheduledPostJST']:raise ValueError('snapshot identity mismatch')
    if pred['latestSourceDay'] and pred['latestSourceDay']>=pred['raceDate']:raise ValueError('same-day or future feature source')
    if lap['raceID']!=pred['raceID'] or lap['raceDate']!=pred['raceDate'] or not eligible_record(lap):raise ValueError('lap identity or validation mismatch')
    if [lap['raceID'][4:6],lap['surface'],int(lap['distanceM']),lap['earlyRecordedDistanceM']]!=pred['courseCondition']:raise ValueError('course or measurement point changed')
    active={r['number'] for r in snapshot['runners'] if not r['withdrawn']}
    if active!=set(final_numbers):return None,'entrant_set_changed'
    p=np.array([pred['probabilities'][k] for k in LABELS]);base=np.array([pred['baselineProbabilities'][k] for k in LABELS])
    for values in [p,base]:
        if not np.isfinite(values).all() or (values<0).any() or (values>1).any() or abs(values.sum()-1)>1e-9:raise ValueError('invalid probability vector')
    q25,median,q75=pred['referenceQuantilesSeconds']
    if not 0<q25<=median<=q75:raise ValueError('invalid frozen reference')
    seconds=lap['earlyRecordedTimeSeconds'];label=0 if seconds<q25 else 2 if seconds>q75 else 1
    target=np.eye(3)[label];prediction=int(p.argmax());baseline=int(base.argmax())
    return {'day':pred['raceDate'],'modelID':pred['modelID'],'actual':label,'predicted':prediction,'baseline':baseline,'correct':prediction==label,'brier':float(np.sum((p-target)**2)),'baseBrier':float(np.sum((base-target)**2))},None

def summarize(rows):
    d=pd.DataFrame(rows);y=d.actual.to_numpy();pred=d.predicted.to_numpy()
    daily=d.assign(brierDelta=d.baseBrier-d.brier,accuracyDelta=d.correct.astype(float)-(d.baseline==d.actual).astype(float),n=1).groupby('day').sum(numeric_only=True)
    recalls={str(c):round(float(np.mean(pred[y==c]==c))*100,2) if sum(y==c) else None for c in range(3)}
    precision={str(c):round(float(np.mean(y[pred==c]==c))*100,2) if sum(pred==c) else None for c in range(3)}
    intervals={'brierImprovement95CI':bootstrap_interval(daily,'brierDelta','n'),'accuracyImprovement95CI':bootstrap_interval(daily,'accuracyDelta','n')}
    qualified=len(d)>=1000 and intervals['brierImprovement95CI'][0]>0 and intervals['accuracyImprovement95CI'][0]>0 and all(recalls[str(c)] is not None and recalls[str(c)]>=20 for c in [0,2])
    return {'evaluationRaces':len(d),'evaluationDays':len(daily),'accuracyPct':round(float(d.correct.mean())*100,2),'coursePriorAccuracyPct':round(float(np.mean(d.baseline==d.actual))*100,2),'brierScore':round(float(d.brier.mean()),6),'coursePriorBrierScore':round(float(d.baseBrier.mean()),6),'actualCounts':dict(Counter(str(v) for v in y)),'predictedCounts':dict(Counter(str(v) for v in pred)),'recallPct':recalls,'precisionPct':precision,**intervals,'adoptionDecision':'requires_review_and_more_live_checks' if qualified else 'do_not_enable_pace_forecast'}

def run(reports_root,lap_root,history,models_root=None):
    models_root=Path(models_root) if models_root else ROOT/'research/pace/models'
    models={}
    for path in models_root.glob('*.json'):
        raw=path.read_bytes();m=json.loads(raw);models[m['modelID']]=(m,hashlib.sha256(raw).hexdigest())
    laps={}
    for p in Path(lap_root).glob('*/RaceLapData.json'):
        for lap in json.loads(p.read_text())['races']:laps[lap['raceID']]=lap
    groups={rid:g for rid,g in history.groupby('レースID',sort=False)}
    evaluated=defaultdict(list);counts=Counter();examples=[]
    for index in sorted(Path(reports_root).glob('*/prerace/*/index.json')):
        date8=index.parents[2].name
        if len(date8)!=8 or not date8.isdigit() or f'{date8[:4]}-{date8[4:6]}-{date8[6:]}'<START:continue
        counts['racesWithObservedEntry']+=1
        path=index.parent/'PacePrediction.json'
        if not path.exists():counts['missingPrediction']+=1;continue
        pred=json.loads(path.read_text());counts['recordedFirstAttempts']+=1
        if pred.get('status')=='held':counts['heldBeforeRace']+=1;continue
        if pred['raceID'] not in laps or pred['raceID'] not in groups:counts['awaitingCompletedResults']+=1;continue
        try:
            if pred['modelID'] not in models:raise ValueError('unregistered research model')
            model,modelsha=models[pred['modelID']]
            if pred['modelFileSHA256']!=modelsha or pred['modelFittedAtJST']!=model['fittedAtJST'] or pred['trainingCutoffDate']!=model['trainingCutoffDate']:raise ValueError('frozen model identity mismatch')
            filename=pred['snapshotFile']
            if Path(filename).name!=filename:raise ValueError('invalid snapshot filename')
            snapshotpath=path.parent/filename;raw=snapshotpath.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=pred['snapshotSHA256']:raise ValueError('snapshot bytes changed')
            snapshot=json.loads(raw);g=groups[pred['raceID']]
            row,reason=check_prediction(pred,snapshot,laps[pred['raceID']],g['馬番'].tolist())
            if reason:counts[reason]+=1;continue
            evaluated[pred['modelID']].append(row);counts['evaluatedRaces']+=1
        except (ValueError,KeyError,OSError,TypeError) as exc:
            counts['invalidRecords']+=1
            if len(examples)<20:examples.append({'raceID':pred.get('raceID'),'reason':str(exc)})
    return {'schemaVersion':1,'methodVersion':'prospective-pace-evaluation-v1','status':'prospective_observations_pending' if not counts['evaluatedRaces'] else 'research_only','evaluationStartDate':START,'selectionRule':'First recorded attempt per race, including holds; changed entrant set is reported and excluded; never backfill a prediction after post.','counts':{k:int(counts[k]) for k in ['racesWithObservedEntry','recordedFirstAttempts','missingPrediction','heldBeforeRace','awaitingCompletedResults','entrant_set_changed','invalidRecords','evaluatedRaces']},'byModel':{key:summarize(rows) for key,rows in evaluated.items()},'invalidExamples':examples,'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}

def main():
    history=pd.read_parquet(ROOT/'production/production_history.parquet',columns=['レースID','馬番'])
    report=run(ROOT/'reports',ROOT/'research/laps',history)
    out=ROOT/'reports/PaceProspectiveValidation.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
