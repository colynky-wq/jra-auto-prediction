"""Human-readable per-race recording status, including absent and invalid records."""
from __future__ import annotations
from collections import Counter
from datetime import datetime
import hashlib,json
from pathlib import Path
import pandas as pd
from horse_profile_runtime import JRA_VENUE_MAP
from prerace_snapshot_runtime import JST
from evaluate_prospective_pace import START,check_prediction
ROOT=Path(__file__).resolve().parent.parent
LABELS={'waiting':'記録待ち','saved':'予測保存済み','held':'発走前に保留','missing':'記録なし','failed':'保存失敗','invalid':'記録不整合'}
HOLD={'insufficient_style_history':'過去3走以上の脚質記録が足りません。','insufficient_course_reference':'同じコース条件の基準件数が足りません。','straight_race':'直線レースは対象外です。','unknown_race_class':'レースのクラスを確認できません。'}

def read_json(path):
    if not path.exists():return None,None
    try:return json.loads(path.read_text()),None
    except (OSError,ValueError) as exc:return None,'保存ファイルを読み取れません。'

def build(reports_root,lap_root,history,models_root,now):
    reports_root=Path(reports_root);lap_root=Path(lap_root);models_root=Path(models_root)
    expected={};groups={};models={};laps={}
    h=history.copy();h['日付']=pd.to_datetime(h['日付']);h=h[(h['日付'].dt.strftime('%Y-%m-%d')>=START)&~h['障害'].fillna(False).astype(bool)]
    for rid,g in h.groupby('レースID',sort=False):
        day=g['日付'].iloc[0].strftime('%Y-%m-%d');groups[rid]=g
        expected[rid]={'raceID':rid,'raceDate':day,'venue':JRA_VENUE_MAP[rid[4:6]],'raceNumber':int(rid[-2:]),'startTime':None,'scheduledPostJST':None,'fromCompletedHistory':True}
    for p in reports_root.glob('*/PaceRecordingSchedule.json'):
        schedule,error=read_json(p)
        if schedule and schedule['raceDate']>=START:
            for r in schedule['races']:expected[r['raceID']]={**r,'fromCompletedHistory':r['raceID'] in groups}
    for p in reports_root.glob('*/prerace/*/index.json'):
        rid=p.parent.name;date8=p.parents[2].name
        if len(date8)==8 and date8.isdigit():
            day=f'{date8[:4]}-{date8[4:6]}-{date8[6:]}'
            if day>=START and rid not in expected:expected[rid]={'raceID':rid,'raceDate':day,'venue':JRA_VENUE_MAP.get(rid[4:6],''),'raceNumber':int(rid[-2:]),'startTime':None,'scheduledPostJST':None,'fromCompletedHistory':False}
    # Failed capture can leave an event without an index; preserve that race too.
    for p in reports_root.glob('*/prerace/*/events/*.json'):
        e,error=read_json(p)
        if e and e['raceDate']>=START and e['raceID'] not in expected:
            rid=e['raceID'];expected[rid]={'raceID':rid,'raceDate':e['raceDate'],'venue':JRA_VENUE_MAP.get(rid[4:6],''),'raceNumber':int(rid[-2:]),'startTime':None,'scheduledPostJST':None,'fromCompletedHistory':False}
    for p in models_root.glob('*.json'):
        m,error=read_json(p)
        if m:models[m['modelID']]=(m,hashlib.sha256(p.read_bytes()).hexdigest())
    for p in lap_root.glob('*/RaceLapData.json'):
        data,error=read_json(p)
        if data:
            for r in data['races']:laps[r['raceID']]=r
    rows=[]
    for rid,race in sorted(expected.items(),key=lambda item:(item[1]['raceDate'],item[1].get('startTime') or '',item[0]),reverse=True):
        folder=reports_root/race['raceDate'].replace('-','')/'prerace'/rid
        index,indexerror=read_json(folder/'index.json');pred,prederror=read_json(folder/'PacePrediction.json')
        events=[]
        for path in sorted((folder/'events').glob('*.json')):
            event,error=read_json(path)
            if event:events.append(event)
        latest=events[-1] if events else None
        issues=[];state='waiting';reason='発走前の記録を待っています。';result_state='no_prediction';result_label='予測未保存';captured=None;predicted=None
        post=datetime.fromisoformat(race['scheduledPostJST']) if race.get('scheduledPostJST') else None
        complete=bool(race['fromCompletedHistory'])
        if prederror or indexerror:state='invalid';reason=prederror or indexerror
        elif pred:
            captured=pred.get('snapshotCapturedAtJST');predicted=pred.get('predictedAtJST')
            try:
                snapshotcapture=datetime.fromisoformat(pred['snapshotCapturedAtJST'])
                postpred=datetime.fromisoformat(pred['scheduledPostJST']);predictiontime=datetime.fromisoformat(pred['predictedAtJST']);capturetime=datetime.fromisoformat(pred['inputAvailableAtJST']);fit=datetime.fromisoformat(pred['modelFittedAtJST'])
                if any(t.tzinfo is None for t in [snapshotcapture,postpred,predictiontime,capturetime,fit]) or not snapshotcapture<=capturetime<=predictiontime<postpred or not fit<=predictiontime or pred['trainingCutoffDate']>=race['raceDate']:raise ValueError('time')
                if pred['raceID']!=rid or pred['raceDate']!=race['raceDate']:raise ValueError('identity')
                filename=pred['snapshotFile']
                if Path(filename).name!=filename:raise ValueError('filename')
                snapshotpath=folder/filename;snapshot=json.loads(snapshotpath.read_text())
                if hashlib.sha256(snapshotpath.read_bytes()).hexdigest()!=pred['snapshotSHA256']:raise ValueError('snapshot')
                if pred['modelID'] not in models or models[pred['modelID']][1]!=pred['modelFileSHA256']:raise ValueError('model')
                model=models[pred['modelID']][0]
                if model['fittedAtJST']!=pred['modelFittedAtJST'] or model['trainingCutoffDate']!=pred['trainingCutoffDate']:raise ValueError('model time')
                if snapshot['raceID']!=rid or snapshot['raceDate']!=race['raceDate'] or snapshot['capturedAtJST']!=pred['snapshotCapturedAtJST'] or snapshot['scheduledPostJST']!=pred['scheduledPostJST'] or datetime.fromisoformat(snapshot.get('completeInputAvailableAtJST',snapshot['capturedAtJST']))!=capturetime or not snapshot.get('prePostConfirmed'):raise ValueError('snapshot identity')
                if pred['status']=='held':state='held';reason=HOLD.get(pred.get('holdReason'),'発走前の条件確認で保留しました。');result_state='not_evaluated';result_label='照合対象外'
                elif pred['status']=='research_forecast':
                    state='saved';reason='発走前の研究予測を保存しました。';result_state='awaiting_result';result_label='結果待ち'
                    if rid in laps and rid in groups:
                        row,changed=check_prediction(pred,snapshot,laps[rid],groups[rid]['馬番'].tolist())
                        if changed:result_state='entrant_changed';result_label='出走馬変更';reason='予測後に出走馬が変わったため、主集計から外します。'
                        else:result_state='matched';result_label='照合済み'
                else:raise ValueError('status')
            except (KeyError,ValueError,TypeError,OSError):state='invalid';reason='保存内容・参照ファイル・時刻の整合性を確認できません。';result_state='invalid';result_label='照合保留'
        elif latest and latest['stage'] in {'capture_failed','prediction_failed','monitor_failed'}:
            state='failed';reason={'capture_failed':'発走前の出馬表を保存できませんでした。','prediction_failed':'研究予測の作成または保存に失敗しました。','monitor_failed':'自動監視の処理が失敗し、研究予測が残っていません。'}[latest['stage']]
        elif complete or (post and now>=post):
            state='missing';reason='レースは発走済みですが、発走前の研究予測が見つかりません。'
        if latest and latest['stage']=='monitor_missed' and not pred:state='missing';reason='発走前の記録時間に間に合いませんでした。'
        if any(e['stage']=='track_unavailable' for e in events):issues.append('馬場情報を取得できない試行がありました。')
        if state=='saved' and any(e['stage'] in {'capture_failed','prediction_failed','monitor_failed'} for e in events):issues.append('保存前後に失敗した試行があります。予測は保存済みです。')
        row={k:race[k] for k in ['raceID','raceDate','venue','raceNumber','startTime']}
        row.update(recordingState=state,recordingLabel=LABELS[state],resultState=result_state,resultLabel=result_label,reason=reason,issues=issues,snapshotCapturedAtJST=captured,predictedAtJST=predicted,lastAttemptAtJST=latest['atJST'] if latest else predicted,attemptEvents=len(events),needsAttention=state in {'failed','missing','invalid'} or bool(issues))
        rows.append(row)
    counts=Counter(r['recordingState'] for r in rows)
    return {'schemaVersion':1,'status':'recording_active' if rows else 'awaiting_first_schedule','generatedAtJST':now.strftime('%Y-%m-%d %H:%M:%S'),'recordingStartDate':START,'counts':{'totalRaces':len(rows),**{k:int(counts[k]) for k in LABELS},'matched':sum(r['resultState']=='matched' for r in rows),'awaitingResult':sum(r['resultState']=='awaiting_result' for r in rows),'needsAttention':sum(r['needsAttention'] for r in rows)},'races':rows,'emptyMessage':'研究記録の対象となる開催予定はまだ保存されていません。次の自動監視で一覧を作成します。','paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}

def markdown(data):
    lines=['# 研究記録状況','',f"更新：{data['generatedAtJST']} JST",'',f"対象 {data['counts']['totalRaces']}件／予測保存済み {data['counts']['saved']}件／要確認 {data['counts']['needsAttention']}件",'', 'ペース予想の表示は保留中です。この一覧は研究用データの保存と照合の状況です。','']
    if not data['races']:return '\n'.join(lines+[data['emptyMessage'],''])
    lines+=['| 日付 | レース | 発走 | 記録 | 結果照合 | 原因・確認事項 |','|---|---|---|---|---|---|']
    for r in data['races']:
        reason=' '.join([r['reason']]+r['issues']).replace('|','／').replace('\n',' ')
        lines.append(f"| {r['raceDate']} | {r['venue']}{r['raceNumber']}R | {r['startTime'] or '未収録'} | {r['recordingLabel']} | {r['resultLabel']} | {reason} |")
    return '\n'.join(lines)+'\n'

def main():
    h=pd.read_parquet(ROOT/'production/production_history.parquet',columns=['レースID','日付','馬番','障害'])
    data=build(ROOT/'reports',ROOT/'research/laps',h,ROOT/'research/pace/models',datetime.now(JST))
    path=ROOT/'PaceRecordingStatus.json'
    previous,error=read_json(path)
    if previous:
        old={k:v for k,v in previous.items() if k!='generatedAtJST'};new={k:v for k,v in data.items() if k!='generatedAtJST'}
        try:age=(datetime.now(JST)-datetime.strptime(previous['generatedAtJST'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)).total_seconds()
        except (KeyError,ValueError):age=301
        if old==new and age<300:
            print('RECORDING_STATUS_UNCHANGED');return
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n');temporary.replace(path)
    (ROOT/'reports/PaceRecordingStatus.md').write_text(markdown(data));print(json.dumps(data['counts']))
if __name__=='__main__':main()
