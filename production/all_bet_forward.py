"""Immutable first attempts for added pools. Never writes app signals or stakes."""
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import all_bet_baseline as model
from triple_forward import JST, digest, request_bytes, stamp

VERSION = 'all-bet-forward-v1-20261015'
START = datetime.fromisoformat('2026-10-15T17:47:37+09:00')
MODEL_HASH = hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest()


def source_url(rid, kind):
    return (f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}'
            f'&type={model.TYPES[kind]}&action=init&compress=0&output=json')


def fetch_odds(rid, kind):
    return json.loads(request_bytes(source_url(rid,kind)))


def parse_table(raw, expected, kind):
    if not isinstance(raw,dict) or set(raw) != set(expected):
        raise ValueError('exact complete odds coverage required')
    out = {}
    for key,item in raw.items():
        if not isinstance(item,list) or len(item) < (2 if kind == 'place' else 1):
            raise ValueError('invalid odds row')
        low = item[0]; high = item[1] if kind == 'place' else low
        if isinstance(low,bool) or isinstance(high,bool):
            raise ValueError('numeric odds required')
        low, high = (float(str(x).replace(',','')) for x in (low,high))
        if not math.isfinite(low) or not math.isfinite(high) or not 1 <= low <= high:
            raise ValueError('finite ordered odds >= 1 required')
        out[key] = {'low':low, 'high':high}
    return out


def path_for(root, race, kind):
    day,rid,_ = model.race_key(race)
    if kind not in model.TYPES:
        raise ValueError('unknown added pool')
    return Path(root)/'reports'/day.replace('-','')/'all_bet_v1'/kind/(rid+'.json')


def write_first(path, value, deadline, clock):
    """Only complete bytes become visible; recheck the deadline after fsync."""
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.research-',suffix='.tmp',delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write((json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if clock().astimezone(JST) >= deadline:
            return None
        try:
            os.link(temporary,path)
        except FileExistsError:
            if path.read_bytes() != temporary.read_bytes():
                raise ValueError('first research record conflict')
    finally:
        temporary.unlink(missing_ok=True)
    return path


def validate_entry(evidence, race, received):
    if not isinstance(evidence,dict):
        raise ValueError('official entry evidence unavailable')
    if (evidence['raceID'] != race['raceID'] or evidence['raceDate'] != race['raceDate']
            or stamp(evidence['scheduledPostJST']) != stamp(race['raceDate']+'T'+race['startTime'])
            or not re.fullmatch('[a-f0-9]{64}',evidence.get('sourceSHA256',''))):
        raise ValueError('official entry identity mismatch')
    # This metadata is produced only after parse_snapshot verifies the routed
    # official URL, heading, exact date, venue and post time in the source body.
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(evidence['sourceURL'])
    rid, day = race['raceID'], race['raceDate'].replace('-','')
    expected = rid[4:6]+rid[:4]+rid[6:]+day
    token = unquote(parse_qs(parsed.query).get('CNAME',[''])[0])
    if (parsed.scheme != 'https' or parsed.hostname not in {'www.jra.go.jp','jra.go.jp','www.jra.jp','jra.jp','own.jra.jp'}
            or parsed.path != '/JRADB/accessD.html'
            or not re.fullmatch(r'pw01dde(?:01|10)'+expected+r'/[0-9A-Fa-f]{2}',token)):
        raise ValueError('official source URL mismatch')
    captured = stamp(evidence['capturedAtJST'])
    if captured.date().isoformat() != race['raceDate'] or not START <= captured <= received:
        raise ValueError('entry source timestamp mismatch')


def validate(record):
    value = dict(record); fingerprint = value.pop('recordFingerprint')
    if digest(value) != fingerprint or record['schemaVersion'] != 1 or record['version'] != VERSION:
        raise ValueError('record fingerprint/version mismatch')
    race,kind = record['prediction'], record['betType']
    day,_,time = model.race_key(race); model.roster(race['horses'])
    if kind not in model.TYPES or record['baselineVersion'] != model.METHOD or record['baselineHash'] != MODEL_HASH:
        raise ValueError('frozen comparison method unavailable')
    for key in ('aiProbability','marketProbability'):
        model.win_weights(race['horses'],key)
    predicted,began,received,recorded = (stamp(record[k]) for k in
        ('predictionAvailableAtJST','requestStartedAtJST','receivedAtJST','recordedAtJST'))
    post = stamp(day+'T'+time)
    if (not START <= predicted <= began <= received <= recorded < post
            or predicted.date().isoformat() != day or recorded.date().isoformat() != day):
        raise ValueError('same-day pre-post record required')
    if record['status'] == 'ready':
        if kind in {'place','frame_quinella'}:
            validate_entry(record['entryEvidence'],race,predicted)
        expected = model.probabilities(race['horses'],'aiProbability',kind,record.get('entryEvidence'))
        if (record.get('apiStatus') != 'middle' or record.get('apiType') != model.TYPES[kind]
                or record.get('sourceURL') != source_url(race['raceID'],kind)
                or not re.fullmatch('[a-f0-9]{64}',record.get('sourceFingerprint',''))
                or set(record['odds']) != set(expected)):
            raise ValueError('pre-race source/coverage mismatch')
        for odds in record['odds'].values():
            low,high = (model.finite(odds[k]) for k in ('low','high'))
            if not 1 <= low <= high or (kind != 'place' and low != high):
                raise ValueError('invalid saved odds')
        source_time = stamp(record['officialDatetime'])
        if source_time.date().isoformat() != day or not source_time <= received < post:
            raise ValueError('source odds timestamp mismatch')
    elif record['status'] != 'held' or record.get('odds') or not record.get('reason'):
        raise ValueError('invalid held record')
    return record


def record_first(root,race,kind,predicted,entry_evidence=None,*,fetch=fetch_odds,clock=lambda:datetime.now(JST)):
    path = path_for(root,race,kind)
    if path.exists():
        old = validate(json.loads(path.read_text()))
        if old['betType'] != kind or model.race_key(old['prediction']) != model.race_key(race):
            raise ValueError('record path identity mismatch')
        return path
    day,_,time = model.race_key(race)
    now,predicted,post = clock().astimezone(JST),stamp(predicted),stamp(day+'T'+time)
    if not START <= predicted <= now < post or now.date().isoformat() != day:
        return None
    for key in ('aiProbability','marketProbability'):
        model.win_weights(race['horses'],key)
    record = {'schemaVersion':1,'version':VERSION,'baselineVersion':model.METHOD,'baselineHash':MODEL_HASH,
              'betType':kind,'prediction':race,'entryEvidence':entry_evidence,
              'predictionAvailableAtJST':predicted.isoformat(),'requestStartedAtJST':now.isoformat(),
              'status':'held','odds':{},'reason':'entry_terms_unconfirmed'}
    try:
        if kind in {'place','frame_quinella'}:
            validate_entry(entry_evidence,race,predicted)
        expected = model.probabilities(race['horses'],'aiProbability',kind,entry_evidence)
        record['reason'] = 'odds_unavailable'
        raw = fetch(race['raceID'],kind)
        if not isinstance(raw,dict) or raw.get('status') != 'middle':
            raise ValueError('pre-race odds status required')
        record.update(status='ready',odds=parse_table(raw['data']['odds'][str(model.TYPES[kind])],expected,kind),
                      apiStatus=raw['status'],apiType=model.TYPES[kind],sourceURL=source_url(race['raceID'],kind),
                      sourceFingerprint=digest(raw),officialDatetime=raw['data']['official_datetime'])
        record.pop('reason')
    except (ValueError,OSError,KeyError,TypeError) as error:
        record.update(status='held',odds={},reason=record.get('reason','odds_unavailable'),errorType=type(error).__name__)
    record['receivedAtJST'] = clock().astimezone(JST).isoformat()
    record['recordedAtJST'] = clock().astimezone(JST).isoformat()
    if stamp(record['recordedAtJST']) >= post:
        return None
    record['recordFingerprint'] = digest(record)
    try:
        validate(record)
    except ValueError:
        record.update(status='held',odds={},reason='source_time_or_schema_invalid')
        record.pop('recordFingerprint'); record['recordFingerprint'] = digest(record); validate(record)
    return write_first(path,record,post,clock)


def payout_map(html,rid,kind):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html,'html.parser')
    identities = [t.get('href') or t.get('content') or '' for t in soup.select('link[rel="canonical"],meta[property="og:url"]')]
    if not any(re.search(r'race_id='+rid+r'(?:[&#]|$)',u) for u in identities):
        raise ValueError('payout race identity missing')
    rows = [r for r in soup.select('tr') if r.find('th') and r.find('th').get_text(strip=True) == model.LABELS[kind]]
    if len(rows) != 1:
        raise ValueError('unique pool payout row required')
    cells = rows[0].find_all('td',recursive=False)
    if len(cells) < 2:
        raise ValueError('missing payout cells')
    numbers = [int(x) for x in re.findall(r'\d+',cells[0].get_text(' ',strip=True))]
    amounts = [int(x.replace(',','')) for x in re.findall(r'([0-9][0-9,]*)\s*円',cells[1].get_text(' ',strip=True))]
    width = 1 if kind in {'win','place'} else 2
    if not amounts or len(numbers) != len(amounts)*width:
        raise ValueError('payout group mismatch')
    out = {}
    for i,amount in enumerate(amounts):
        group = numbers[i*width:(i+1)*width]
        if any(not 1 <= n <= (8 if kind == 'frame_quinella' else 18) for n in group) or amount <= 0:
            raise ValueError('invalid payout outcome')
        if width == 2 and kind != 'frame_quinella' and group[0] == group[1]:
            raise ValueError('repeated horse in payout')
        key = model.outcome_key(sorted(group) if kind in {'frame_quinella','quinella'} else group)
        if key in out:
            raise ValueError('duplicate payout outcome')
        out[key] = amount
    return out


def evaluate(record,result,payouts):
    validate(record)
    if record['status'] != 'ready':
        return {'status':'held','reason':record['reason'],'rows':[]}
    race,kind,evidence = record['prediction'],record['betType'],record.get('entryEvidence')
    state,finish = model.result_state(race,result)
    if state != 'matched':
        return {'status':state,'rows':[]}
    if payouts is None:
        return {'status':'payouts_pending','rows':[]}
    truth = model.truth_keys(race,kind,evidence,finish)
    if set(payouts) != truth or any(type(v) is not int or v <= 0 for v in payouts.values()):
        raise ValueError('independent finish/payout disagreement')
    ai,market = (model.probabilities(race['horses'],key,kind,evidence) for key in ('aiProbability','marketProbability'))
    rows = [{'outcome':key,'aiProbability':ai[key],'marketProbability':market[key],
             'savedOddsLow':odds['low'],'savedOddsHigh':odds['high'],
             'expectedMultiplierLow':ai[key]*odds['low'],'expectedMultiplierHigh':ai[key]*odds['high'],
             'hit':key in truth,'payoutPer100Yen':payouts.get(key,0)} for key,odds in sorted(record['odds'].items())]
    return {'status':'matched','rows':rows,'selectedBets':0,'returnIndex':None,'autoBetEnabled':False}
