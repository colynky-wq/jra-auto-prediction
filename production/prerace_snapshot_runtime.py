"""Immutable observations from the SAME official entry response used by live input."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlparse
from bs4 import BeautifulSoup
from horse_profile_runtime import JRA_VENUE_MAP

JST=timezone(timedelta(hours=9))
ROOT=Path(__file__).resolve().parent.parent
VERSION='official-entry-observation-v1'

def parse_snapshot(text,rid,date8,url,captured_at,entries):
    u=urlparse(url);expected=rid[4:6]+rid[:4]+rid[6:]+date8
    token=unquote(parse_qs(u.query).get('CNAME',[''])[0])
    if u.scheme!='https' or u.hostname not in {'www.jra.go.jp','jra.go.jp','www.jra.jp','jra.jp','own.jra.jp'} or u.path!='/JRADB/accessD.html' or not re.fullmatch(r'pw01dde(?:01|10)'+re.escape(expected)+r'/[0-9A-Fa-f]{2}',token):
        raise ValueError('official entry URL identity mismatch')
    if not re.fullmatch(r'\d{12}',rid) or rid[4:6] not in JRA_VENUE_MAP:raise ValueError('invalid race identity')
    if captured_at.tzinfo is None:raise ValueError('capture time must be timezone aware')
    captured_at=captured_at.astimezone(JST)
    soup=BeautifulSoup(text,'html.parser')
    heading=next((h.get_text(' ',strip=True) for h in soup.find_all('h1') if '出馬表' in h.get_text()),'')
    d=re.search(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日',heading)
    identity=re.search(r'(\d+)回\s*(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)\s*(\d+)日\s*(\d+)レース',heading)
    if not d or not identity:raise ValueError('entry heading identity missing')
    if f'{int(d[1]):04d}{int(d[2]):02d}{int(d[3]):02d}'!=date8 or (int(identity[1]),identity[2],int(identity[3]),int(identity[4]))!=(int(rid[6:8]),JRA_VENUE_MAP[rid[4:6]],int(rid[8:10]),int(rid[10:12])):raise ValueError('entry heading differs from request')
    header=soup.select_one('.race_header')
    if header is None:raise ValueError('current race header missing')
    headertext=header.get_text(' ',strip=True)
    tm=re.search(r'発走(?:時刻)?\s*[:：]?\s*(\d{1,2})時\s*(\d{1,2})分',headertext)
    if not tm:raise ValueError('scheduled post time missing')
    post=datetime.strptime(date8,'%Y%m%d').replace(hour=int(tm[1]),minute=int(tm[2]),tzinfo=JST)
    if captured_at>=post or captured_at.date()!=post.date():raise ValueError('not a same-day pre-post observation')
    course=re.search(r'コース\s*[:：]\s*([\d,]+)\s*メートル\s*[（(]([^）)]+)[）)]',headertext)
    if not course:raise ValueError('course metadata missing')
    detail=course[2];surface='ダ' if 'ダート' in detail else '芝' if '芝' in detail else None
    if not surface or '障害' in headertext or '芝→ダート' in detail:raise ValueError('not a flat race')
    race_class=next((value for pattern,value in [('新馬|メイクデビュー','新馬'),('未勝利','未勝利'),('1勝','1勝'),('2勝','2勝'),('3勝','3勝'),('オープン|G[IⅠ]|G[1-3]|リステッド','OP')] if re.search(pattern,headertext)),None)
    # Restrict going to current header. Past-race table conditions are NOT current going.
    going=re.search(r'(?:芝|ダート)\s*[:：]?\s*(稍重|不良|良|重)',headertext)
    source_rows={}
    table=soup.find('table')
    if table is None:raise ValueError('entry table missing')
    for tr in table.find_all('tr'):
        tds=tr.find_all('td',recursive=False)
        if len(tds)<4:continue
        number=re.search(r'\d+',tds[1].get_text(' ',strip=True))
        if not number:continue
        n=int(number[0])
        if n in source_rows:raise ValueError('duplicate entrant number')
        current=' '.join(td.get_text(' ',strip=True) for td in tds[:4])
        source_rows[n]=bool(re.search(r'出走取消|競走除外|取消|除外',current))
    runners=[]
    for n,name in entries[['馬番','馬名']].itertuples(index=False,name=None):
        n=int(n)
        if n not in source_rows:raise ValueError('parsed entrant not found in source')
        runners.append({'number':n,'name':str(name),'withdrawn':source_rows[n]})
    if len({r['number'] for r in runners})!=len(runners) or set(source_rows)!={r['number'] for r in runners}:raise ValueError('entrant set incomplete')
    return {'schemaVersion':1,'methodVersion':VERSION,'raceID':rid,'raceDate':post.date().isoformat(),'capturedAtJST':captured_at.isoformat(timespec='microseconds'),'scheduledPostJST':post.isoformat(),'sourceURL':url,'sourceSHA256':hashlib.sha256(text.encode()).hexdigest(),'sourceHashEncoding':'decoded UTF-8 response text','prePostConfirmed':True,'surface':surface,'distanceM':int(course[1].replace(',','')),'courseDetail':detail,'raceClass':race_class,'going':going[1] if going else None,'goingObservedInCurrentHeader':bool(going),'activeEntrantsAtCapture':sum(not r['withdrawn'] for r in runners),'runners':sorted(runners,key=lambda r:r['number']),'paceForecastEnabled':False,'formalSignalsChanged':False,'stakeSizingEnabled':False}

def persist_snapshot(text,rid,date8,url,captured_at,entries,root=None,track_observation=None):
    observation=parse_snapshot(text,rid,date8,url,captured_at,entries)
    if track_observation is not None:
        track_capture=datetime.fromisoformat(track_observation['capturedAtJST'])
        track_asof=datetime.fromisoformat(track_observation['asOfJST'])
        post=datetime.fromisoformat(observation['scheduledPostJST'])
        if track_capture.tzinfo is None or track_asof.tzinfo is None or track_capture>=post or track_asof>track_capture or track_observation['raceDate']!=observation['raceDate'] or track_observation['venue']!=JRA_VENUE_MAP[rid[4:6]]:
            raise ValueError('track observation identity or pre-post timing mismatch')
        available=max(captured_at,track_capture)
        track_observation=dict(track_observation)
        age=(available-track_asof).total_seconds()/3600
        track_observation['eligibleForRaceInput']=track_asof.date()==post.date() and 0<=age<=6
        track_observation['ageHoursAtInputAvailable']=round(age,3)
        observation['completeInputAvailableAtJST']=available.isoformat()
        observation['trackConditionObservation']=track_observation
        if not observation['goingObservedInCurrentHeader'] and track_observation['eligibleForRaceInput']:
            observation['going']=track_observation['going']['芝' if observation['surface']=='芝' else 'ダート']
            observation['goingSource']='official timestamped track page'
    root=Path(root) if root else ROOT/'reports'/date8/'prerace'
    folder=root/rid;folder.mkdir(parents=True,exist_ok=True)
    state={k:observation[k] for k in ['scheduledPostJST','surface','distanceM','courseDetail','raceClass','going','activeEntrantsAtCapture','runners']}
    if track_observation is not None:
        state['trackState']={k:track_observation[k] for k in ['asOfJST','going','eligibleForRaceInput']}
    fingerprint=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    indexpath=folder/'index.json';index=json.loads(indexpath.read_text()) if indexpath.exists() else {'raceID':rid,'snapshots':[]}
    if index.get('latestFingerprint')==fingerprint:return None
    timestamp=captured_at.astimezone(JST).strftime('%Y%m%dT%H%M%S%f')
    filename=timestamp+'-'+fingerprint[:12]+'.json'
    path=folder/filename
    with path.open('x',encoding='utf-8') as f:json.dump(observation,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    index['latestFingerprint']=fingerprint;index['snapshots'].append({'file':filename,'capturedAtJST':observation['capturedAtJST'],'fingerprint':fingerprint})
    temp=indexpath.with_suffix('.tmp');temp.write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n');temp.replace(indexpath)
    return path
