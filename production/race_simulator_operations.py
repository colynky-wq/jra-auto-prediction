"""Persistent capture attempts and honest per-race operational status."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import subprocess


LABELS={'pending':'発走前の保存待ち','local_saved':'保存済・公開確認待ち',
    'result_waiting':'予測保存済・結果待ち','matched':'結果照合済み','held':'照合保留',
    'missed':'保存漏れ','failed':'保存失敗','excluded':'導入前・対象外'}


def note_attempt(root,race,status,reason,now):
    from race_simulator_learning import read_json,write_json,stamp
    path=Path(root)/'reports'/race['raceDate'].replace('-','')/'race_simulator_attempts.json'
    data=read_json(path,{'schemaVersion':1,'raceDate':race['raceDate'],'races':{}})
    previous=data['races'].get(race['raceID'])
    if previous and (previous['status'],previous['reason'])==(status,reason):return
    data['races'][race['raceID']]={'raceID':race['raceID'],'raceDate':race['raceDate'],
        'startTime':race.get('startTime'),'venue':race.get('venue'),'raceNumber':race.get('raceNumber'),
        'raceName':race.get('raceName'),'status':status,'reason':reason,'updatedAt':stamp(now),
        'firstAttemptAt':previous['firstAttemptAt'] if previous else stamp(now)}
    write_json(path,data)


def build_status(root,now=None,records=(),held=(),verify_commits=False,commit_resolver=None):
    from race_simulator_learning import (JST,read_json,write_json,stamp,timestamp,post_time,
        journal_path,validate_record,first_committed_at)
    from race_simulator_calibration import digest,load_registry
    root=Path(root);now=now or datetime.now(timezone.utc);today=now.astimezone(JST).date().isoformat()
    state=load_registry(root);started=timestamp(state.get('operationsStartedAt') or stamp(now))
    inventory={};schedules={};attempts={};issues=[]
    def add(race):
        post_time(race)
        key=(race['raceDate'],race['raceID'])
        inventory[key]={**inventory.get(key,{}),**race}
    for race in read_json(root/'RaceData.json',[]):
        try:add(race)
        except (ValueError,KeyError,TypeError):issues.append('取得済みレースの開催情報に不整合があります。')
    for path in (root/'reports').glob('*/PaceRecordingSchedule.json'):
        try:
            doc=read_json(path)
            if doc['raceDate'].replace('-','')!=path.parent.name:raise ValueError('schedule date mismatch')
            for race in doc['races']:
                if race['raceDate']!=doc['raceDate']:raise ValueError('schedule row date mismatch')
                add(race)
            schedules[doc['raceDate']]=True
        except (ValueError,KeyError,TypeError,OSError):issues.append('予定一覧の一部を確認できません。')
    for path in (root/'reports').glob('*/race_simulator_attempts.json'):
        try:
            doc=read_json(path)
            for row in doc['races'].values():
                if row['raceDate'].replace('-','')!=path.parent.name:raise ValueError('attempt date mismatch')
                add(row);attempts[(row['raceDate'],row['raceID'])]=row
        except (ValueError,KeyError,TypeError,OSError):issues.append('保存試行の記録を一部確認できません。')
    for path in (root/'reports').glob('*/race_simulator_v02/*.json'):
        try:
            doc=read_json(path)
            if journal_path(root,doc)!=path:raise ValueError('journal path mismatch')
            display=doc.get('displaySimulation') or {}
            add({k:v for k,v in {**display,**doc}.items() if k in
                ('raceID','raceDate','raceNumber','raceName','venue','startTime','course','track')})
        except (ValueError,KeyError,TypeError,OSError):
            issues.append('保存記録の一部に不整合があります。')
            try:add({'raceID':path.stem,'raceDate':datetime.strptime(path.parts[-3],'%Y%m%d').date().isoformat(),'startTime':'00:00'})
            except ValueError:pass
    matched={x['record']['raceID'] for x in records}
    held_by={Path(x['file']).stem:x['reason'] for x in held}
    rows=[]
    for (day,rid),race in sorted(inventory.items(),reverse=True):
        post=post_time(race);path=journal_path(root,race);attempt=attempts.get((day,rid))
        status='pending';reason='正式10分前の入力と発走前の保存を待っています。';durable=False;recorded=None
        if path.exists():
            try:
                doc=read_json(path)
                if doc.get('recordSHA256')!=digest({k:v for k,v in doc.items() if k!='recordSHA256'}):raise ValueError('journal hash mismatch')
                if not timestamp(doc['recordedAt'])<post:raise ValueError('not pre-post')
                recorded=doc['recordedAt'];status='local_saved';reason='発走前に保存済みです。公開履歴の確認を待っています。'
                if verify_commits:
                    committed=commit_resolver(path) if commit_resolver else first_committed_at(root,path)
                    validate_record(doc,committed);durable=True
                    status='result_waiting';reason='発走前の公開履歴を確認済みです。確定結果を待っています。'
                    if rid in matched:status='matched';reason='確定結果と照合し、評価に利用できる記録です。'
                    elif rid in held_by and held_by[rid]!='awaiting result':status='held';reason='予測は保存済みですが、結果や出走馬の対応確認で保留しています。'
            except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError):
                status='held';reason='保存内容または発走前の公開履歴を確認できず、評価対象から外しています。'
        elif post<=started or '障' in str(race.get('course') or race.get('track') or ''):
            status='excluded';reason='導入前のレース、またはシミュレーターの対象外です。'
        elif attempt and attempt['status']=='failed':
            status='failed';reason='保存処理に失敗しました。発走前は再試行し、発走後の作り直しは実績に含めません。'
        elif post<=now:
            status='missed';reason='発走前の記録がありません。過去に遡って予測実績を作成しません。'
        rows.append({k:race.get(k) for k in ('raceID','raceDate','venue','raceNumber','raceName','startTime')} |
            {'state':status,'label':LABELS[status],'reason':reason,'durableConfirmed':durable,'recordedAt':recorded})
        rows[-1]['verificationCheckedAt']=stamp(now) if verify_commits and path.exists() else None
    # Prefer today's expected schedule; otherwise label the last available day.
    dates={r['raceDate'] for r in rows}
    target=today if today in dates else max(dates,default=today)
    selected=[r for r in rows if r['raceDate']==target]
    available=sorted(dates,reverse=True)[:30]
    counts=dict(Counter(r['state'] for r in selected))
    validation_complete=any(x['status']=='promote' for x in state.get('decisions',[]))
    report={'schemaVersion':1,'generatedAt':stamp(now),'targetRaceDate':target,
        'expectedScheduleAvailable':schedules.get(target,False),'races':selected,'counts':counts,'issues':list(dict.fromkeys(issues)),
        'availableDates':available,'history':[r for r in rows if r['raceDate'] in available],
        'scheduleDates':sorted(schedules),
        'liveVerification':{'status':'verified' if matched else 'awaiting_live_race',
            'matchedRaces':len(matched),'message':'実開催の保存・結果照合を確認済みです。' if matched else '実開催の保存・結果照合が一巡する確認はこれからです。'},
        'modelValidationVerified':validation_complete}
    write_json(root/'RaceSimulatorRecordingStatus.json',report)
    return report
