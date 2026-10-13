"""Research-only pre-post inference from JSON weights; no training-library dependency."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import math
import numpy as np
import pandas as pd
from formation_runtime import relative_position,is_straight_race
from horse_profile_runtime import normalize_horse_name
from pace_baseline_runtime import eligible_record
from prerace_snapshot_runtime import JST,write_once_json

ROOT=Path(__file__).resolve().parent.parent
MODEL_PATH=ROOT/'research/pace/ProspectivePaceModel.json'
LABELS=['fast','middle','slow']

def softmax_predict(model,features):
    x=[float(features[k]) for k in model['numericFeatures']]
    x=(np.asarray(x)-np.asarray(model['numericMean']))/np.asarray(model['numericScale'])
    encoded=x.tolist()
    for field,categories in zip(model['categoricalFeatures'],model['categories']):encoded.extend(float(features[field]==v) for v in categories)
    z=np.asarray(model['coefficients'])@np.asarray(encoded)+np.asarray(model['intercepts'])
    if not np.isfinite(z).all():raise ValueError('nonfinite research score')
    p=np.exp(z-z.max());p/=p.sum()
    aligned=np.zeros(3)
    for i,c in enumerate(model['classes']):aligned[int(c)]=p[i]
    return aligned

def field_features(observation,history,laps):
    """Resolve latest horse key before excluding straight history, then use past days only."""
    rid=observation['raceID'];day=pd.Timestamp(observation['raceDate']);distance=int(observation['distanceM'])
    active=[r for r in observation['runners'] if not r['withdrawn']];count=len(active)
    if count<3 or count!=observation['activeEntrantsAtCapture']:raise ValueError('invalid active entrant count')
    names=[normalize_horse_name(r['name']) for r in active]
    if len(set(names))!=len(names):raise ValueError('ambiguous entrant names')
    d=history.copy();d['日付']=pd.to_datetime(d['日付']).dt.normalize()
    d=d[(d['日付']<day)&~d['障害'].fillna(False).astype(bool)&d['馬キー'].notna()&d['馬名'].notna()]
    if 'normalizedName' not in d:
        names_map={name:normalize_horse_name(name) for name in d['馬名'].unique()}
        d['normalizedName']=d['馬名'].map(names_map)
    d=d[d.normalizedName.isin(names)].sort_values(['日付','レースID','馬番'])
    latest=d.drop_duplicates('normalizedName',keep='last');keys=dict(zip(latest.normalizedName,latest['馬キー']))
    bykey={k:g for k,g in d.groupby('馬キー',sort=False)}
    lapmap={r['raceID']:r for r in laps if eligible_record(r) and r['raceDate']<observation['raceDate']}
    known=[];clockvalues=[];source_dates=[]
    for name in names:
        g=bykey.get(keys.get(name))
        if g is None:continue
        positions=[];clocks=[]
        for row in g.to_dict('records'):
            if not is_straight_race(row['レースID'],row['芝ダ'],row['距離']):
                pos=relative_position(row['初角'],row['頭数'])
                if pos is not None:positions.append((row['日付'],pos))
            lap=lapmap.get(row['レースID'])
            if lap:
                if lap['raceDate']!=row['日付'].strftime('%Y-%m-%d'):raise ValueError('past lap date mismatch')
                clocks.append((row['日付'],lap['earlyRecordedTimeSeconds']/lap['earlyRecordedDistanceM']*600,int(row['距離'])))
        positions=positions[-6:];clocks=clocks[-6:]
        if len(positions)>=3:
            values=np.asarray([v[1] for v in positions]);known.append((float(values.mean()),float(np.mean(values==0)),float(np.mean(values<=.25))))
            source_dates.extend(v[0] for v in positions)
        if clocks:
            clockvalues.append((float(np.mean([v[1] for v in clocks])),float(np.mean([v[2] for v in clocks]))));source_dates.extend(v[0] for v in clocks)
    pos=[v[0] for v in known];escape=sum(v[1]>=.5 for v in known);front=sum(v[2]>=.5 for v in known)
    month=day.month
    return dict(venue=rid[4:6],surface=observation['surface'],raceClass=observation.get('raceClass') or 'unknown',fieldSize=count,distanceM=distance,earlyDistanceM=600+distance%200,styleCoverage=len(known)/count,meanPosition=float(np.mean(pos)) if pos else .5,minPosition=min(pos) if pos else .5,positionSpread=float(np.std(pos)) if pos else 0.,escapeCount=escape,frontCount=front,escapeShare=escape/max(len(known),1),frontShare=front/max(len(known),1),pastLapCoverage=len(clockvalues)/count,pastLap600Mean=float(np.mean([v[0] for v in clockvalues])) if clockvalues else 36.,pastLap600Min=min([v[0] for v in clockvalues],default=36.),pastDistanceMean=float(np.mean([v[1] for v in clockvalues])) if clockvalues else distance,seasonSin=float(np.sin(2*np.pi*month/12)),seasonCos=float(np.cos(2*np.pi*month/12)),raceNumber=int(rid[-2:]),latestSourceDay=max(source_dates).strftime('%Y-%m-%d') if source_dates else None,knownStyleRunners=len(known))

def forecast(observation,history,laps,model,now=None):
    now=now or datetime.now(JST)
    capture=datetime.fromisoformat(observation.get('completeInputAvailableAtJST',observation['capturedAtJST']))
    post=datetime.fromisoformat(observation['scheduledPostJST']);fitted=datetime.fromisoformat(model['fittedAtJST'])
    if any(v.tzinfo is None for v in [now,capture,post,fitted]) or not fitted<=now or not capture<=now<post or now.astimezone(JST).date().isoformat()!=observation['raceDate'] or model['trainingCutoffDate']>=observation['raceDate'] or not observation.get('prePostConfirmed'):
        raise ValueError('not a prospective pre-post prediction')
    features=field_features(observation,history,laps)
    condition=[features[k] for k in ['venue','surface','distanceM','earlyDistanceM']]
    ref=next((r for r in model['references'] if r['condition']==condition),None)
    reason=None
    if is_straight_race(observation['raceID'],observation['surface'],observation['distanceM']):reason='straight_race'
    elif features['styleCoverage']<.8 or features['knownStyleRunners']<3:reason='insufficient_style_history'
    elif ref is None:reason='insufficient_course_reference'
    elif features['raceClass'] not in model['categories'][model['categoricalFeatures'].index('raceClass')]:reason='unknown_race_class'
    result={'schemaVersion':1,'methodVersion':model['methodVersion'],'modelID':model['modelID'],'trainingCutoffDate':model['trainingCutoffDate'],'modelFittedAtJST':model['fittedAtJST'],'raceID':observation['raceID'],'raceDate':observation['raceDate'],'snapshotCapturedAtJST':observation['capturedAtJST'],'inputAvailableAtJST':capture.isoformat(),'predictedAtJST':now.isoformat(timespec='microseconds'),'scheduledPostJST':post.isoformat(),'status':'held' if reason else 'research_forecast','holdReason':reason,'latestSourceDay':features['latestSourceDay'],'courseCondition':condition,'activeEntrantsAtPrediction':features['fieldSize'],'styleCoverage':features['styleCoverage'],'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False,'inputFeatures':{k:features[k] for k in model['numericFeatures']+model['categoricalFeatures']}}
    if not reason:
        probabilities=softmax_predict(model,features)
        result.update(probabilities=dict(zip(LABELS,probabilities.tolist())),predictedClass=LABELS[int(probabilities.argmax())],baselineProbabilities=dict(zip(LABELS,ref['prior'])),referenceQuantilesSeconds=ref['quantiles'],slowFlagThreshold=model['slowFlagThreshold'],slowFlag=bool(probabilities[2]>=model['slowFlagThreshold']) if model['slowFlagThreshold'] is not None else None)
    return result

_CACHE={}

def record_first_prediction(snapshot_path,model_path=MODEL_PATH):
    """One immutable first observation per race; never reconstruct after post."""
    snapshot_path=Path(snapshot_path);output=snapshot_path.parent/'PacePrediction.json'
    if output.exists():return None
    if not Path(model_path).exists():raise ValueError('research model artifact unavailable')
    observation=json.loads(snapshot_path.read_text());model=json.loads(Path(model_path).read_text())
    hpath=ROOT/'production/production_history.parquet';stamp=hpath.stat().st_mtime_ns
    if _CACHE.get('historyStamp')!=stamp:
        columns=['レースID','日付','馬番','馬名','馬キー','初角','頭数','障害','芝ダ','距離']
        _CACHE['history']=pd.read_parquet(hpath,columns=columns)
        names={name:normalize_horse_name(name) for name in _CACHE['history']['馬名'].dropna().unique()}
        _CACHE['history']['normalizedName']=_CACHE['history']['馬名'].map(names)
        _CACHE['historyStamp']=stamp
    # Reload sidecars once per calendar day; new same-day results cannot be features.
    day=datetime.now(JST).date().isoformat()
    if _CACHE.get('lapDay')!=day:
        laps=[]
        for path in (ROOT/'research/laps').glob('*/RaceLapData.json'):laps.extend(json.loads(path.read_text())['races'])
        _CACHE.update(lapDay=day,laps=laps)
    result=forecast(observation,_CACHE['history'],_CACHE['laps'],model)
    # Feature extraction may take time; refuse a write after scheduled post.
    now=datetime.now(JST)
    if now>=datetime.fromisoformat(result['scheduledPostJST']):raise ValueError('post time reached during research inference')
    result['predictedAtJST']=now.isoformat(timespec='microseconds')
    result['snapshotFile']=snapshot_path.name
    result['snapshotSHA256']=hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    result['modelFileSHA256']=hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    write_once_json(output,result)
    if datetime.now(JST)>=datetime.fromisoformat(result['scheduledPostJST']):
        output.unlink()
        raise ValueError('post time reached during immutable record write')
    return output

def record_latest_snapshot_prediction(rid,date8):
    folder=ROOT/'reports'/date8/'prerace'/rid
    index=folder/'index.json'
    if not index.exists():return None
    entries=json.loads(index.read_text()).get('snapshots',[])
    if not entries:return None
    filename=entries[-1]['file']
    if Path(filename).name!=filename:raise ValueError('invalid snapshot index filename')
    return record_first_prediction(folder/filename)
