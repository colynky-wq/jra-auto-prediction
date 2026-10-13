"""Durable best-effort recording diagnostics; formal prediction never depends on these."""
from datetime import datetime
import hashlib,json,re
from pathlib import Path
from prerace_snapshot_runtime import JST,write_once_json
ROOT=Path(__file__).resolve().parent.parent

def record_event(rid,date8,stage,detail):
    try:
        if not re.fullmatch(r'\d{12}',str(rid)):return
        datetime.strptime(date8,'%Y%m%d')
        now=datetime.now(JST);folder=ROOT/'reports'/date8/'prerace'/str(rid)/'events';folder.mkdir(parents=True,exist_ok=True)
        text=str(detail)[:500];payload={'raceID':str(rid),'raceDate':datetime.strptime(date8,'%Y%m%d').date().isoformat(),'atJST':now.isoformat(timespec='microseconds'),'stage':stage,'detail':text}
        filename=now.strftime('%Y%m%dT%H%M%S%f')+'-'+hashlib.sha256((stage+text).encode()).hexdigest()[:10]+'.json'
        write_once_json(folder/filename,payload)
    except Exception as exc:print('RECORDING_DIAGNOSTIC_UNAVAILABLE',type(exc).__name__)

def save_schedule(date8,races):
    try:
        day=datetime.strptime(date8,'%Y%m%d').date().isoformat();now=datetime.now(JST)
        if now.date().isoformat()!=day:return
        payload={'schemaVersion':1,'raceDate':day,'observedAtJST':now.isoformat(),'races':[]}
        for r in races:
            post=datetime.strptime(date8+r['start_time'],'%Y%m%d%H:%M').replace(tzinfo=JST)
            payload['races'].append({'raceID':r['race_id'],'raceDate':day,'venue':r['venue'],'raceNumber':int(r['race_no']),'startTime':r['start_time'],'scheduledPostJST':post.isoformat()})
        folder=ROOT/'reports'/date8;folder.mkdir(parents=True,exist_ok=True);path=folder/'PaceRecordingSchedule.json'
        # Keep the first observed roster; a later session cannot erase missing races.
        if not path.exists():write_once_json(path,payload)
    except Exception as exc:print('RECORDING_SCHEDULE_UNAVAILABLE',type(exc).__name__)
