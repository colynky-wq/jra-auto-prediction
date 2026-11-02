"""Immutable, pre-result v1 plans bound to one formal T-10 input bundle.

No fetching, fitting, stakes changes, historical backfill, or display-cache input.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

JST = timezone(timedelta(hours=9))
START_DATE = '2026-11-03'
POLICY = 'formal-portfolio-inputs-v1'
FOLDER = 'portfolio_live10'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stamp(value):
    t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    # Legacy Wide feed timestamps are explicitly JST, but lack an offset.
    return t.replace(tzinfo=JST) if t.tzinfo is None else t.astimezone(JST)


def path_for(root, day, rid):
    if (len(day) != 10 or len(rid) != 12 or not rid.isdigit()
            or datetime.fromisoformat(day).date().isoformat() != day):
        raise ValueError('invalid portfolio capture identity')
    return Path(root)/'reports'/day.replace('-', '')/FOLDER/(rid+'.json')


def window(day, start, value):
    post = stamp(day+'T'+start+':00')
    minutes = (post-stamp(value)).total_seconds()/60
    if not 8 <= minutes <= 12:
        raise ValueError('portfolio input outside formal T-10 window')
    return stamp(value)


def load(root, day, rid):
    path = path_for(root, day, rid)
    record = json.loads(path.read_text(encoding='utf-8'))
    unsigned = {k:v for k,v in record.items() if k != 'fingerprint'}
    if (record.get('schemaVersion') != 1 or record.get('inputPolicy') != POLICY
            or record.get('fingerprint') != digest(unsigned)):
        raise ValueError('invalid formal portfolio fingerprint/policy')
    prediction = record['prediction']
    if prediction['raceDate'] != day or prediction['raceID'] != rid:
        raise ValueError('formal portfolio identity mismatch')
    window(day, prediction['startTime'], record['recordedAtJST'])
    expected = {f'reports/{day.replace("-", "")}/all_bet_v1/win/{rid}.json',
                f'reports/{day.replace("-", "")}/triple_v1/trio/{rid}.json',
                f'reports/{day.replace("-", "")}/wide_odds_live10/{rid}.json'}
    if set(record['sources']) != expected:
        raise ValueError('unexpected formal portfolio source paths')
    for name, sha in record['sources'].items():
        if hashlib.sha256((Path(root)/name).read_bytes()).hexdigest() != sha:
            raise ValueError('formal portfolio source changed')
    if record['plan']['ruleVersion'] != 'portfolio-shadow-v1-20261019':
        raise ValueError('formal portfolio rule version mismatch')
    return record


def record_first(root, race, clock=lambda: datetime.now(JST)):
    """Freeze plan before T-8, reusing only already frozen T-10 pool records."""
    day, rid = race['raceDate'], race['raceID']
    if day < START_DATE:
        return None
    target = path_for(root, day, rid)
    if target.exists():
        saved = load(root, day, rid)
        if digest(saved['prediction']) != digest(race):
            raise ValueError('first formal portfolio prediction conflict')
        return target
    import all_bet_forward as added
    import triple_forward as triple
    import build_bet_research_data as reference
    import build_signal_data_v2 as signals
    import portfolio_shadow as portfolio
    from formal_10min_evidence import inspect_formal_10min_evidence

    evidence = inspect_formal_10min_evidence(root, race, require_portfolio=False)
    if not evidence['eligible']:
        raise ValueError('formal win prediction evidence missing')
    folder = Path(root)/'reports'/day.replace('-', '')
    paths = [folder/'all_bet_v1'/'win'/(rid+'.json'),
             folder/'triple_v1'/'trio'/(rid+'.json'),
             folder/'wide_odds_live10'/(rid+'.json')]
    raw = [p.read_bytes() for p in paths]
    win, trio, odds = [json.loads(b) for b in raw]
    added.validate(win); triple.validate(trio)
    if race.get('predictionTiming') != 'live_10min':
        raise ValueError('formal prediction required')
    times = [window(day, race['startTime'], race['capturedAtJST'])]
    rows = {}
    for kind, record in [('win', win), ('trio', trio)]:
        if record.get('betType') != kind or digest(record['prediction']) != digest(race):
            raise ValueError('pool prediction does not match formal snapshot')
        for key in ['predictionAvailableAtJST', 'receivedAtJST', 'recordedAtJST']:
            times.append(window(day, race['startTime'], record[key]))
        candidates = reference.freeze_candidates(record, kind) if record['status']=='ready' else []
        rows[(rid, kind)] = {'betType':kind, 'status':record['status'], 'candidates':candidates}
    for key in ['raceDate', 'raceID', 'startTime']:
        if odds.get(key) != race.get(key):
            raise ValueError('formal Wide odds identity/schedule mismatch')
    if odds.get('frozenAtFirstSuccessfulCapture') is not True:
        raise ValueError('formal Wide odds must be frozen')
    times.append(window(day, race['startTime'], odds['capturedAtJST']))
    numbers = {int(h['number']) for h in race['horses']}
    pairs = {tuple(sorted((int(p['number1']),int(p['number2'])))) for p in odds['pairs']}
    expected = {(a,b) for a in numbers for b in numbers if a<b}
    if pairs != expected or len(pairs) != len(odds['pairs']):
        raise ValueError('formal Wide odds entrant coverage mismatch')
    import math
    for pair in odds['pairs']:
        low, high = pair.get('oddsLow'), pair.get('oddsHigh')
        if (type(low) not in (int,float) or type(high) not in (int,float)
                or not math.isfinite(low) or not math.isfinite(high) or not 1 <= low <= high):
            raise ValueError('invalid formal Wide odds values')
    signal = signals.build_live_race_signal(race, odds)
    if signal is None:
        raise ValueError('formal signal unavailable')
    plan = portfolio.build_race_plan(rid, day, rows, signal, {})
    now = clock().astimezone(JST)
    window(day, race['startTime'], now.isoformat())
    if max(times) > now:
        raise ValueError('future formal portfolio input')
    payload = {'schemaVersion':1, 'inputPolicy':POLICY, 'recordedAtJST':now.isoformat(),
               'prediction':deepcopy(race), 'signal':signal, 'plan':plan,
               'sources':{str(p.relative_to(root)):hashlib.sha256(b).hexdigest() for p,b in zip(paths,raw)}}
    payload['fingerprint'] = digest(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=target.parent, encoding='utf-8', delete=False) as f:
            temporary = Path(f.name)
            json.dump(payload,f,ensure_ascii=False,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
        # Calculating probabilities or flushing must not permit a late first write.
        window(day,race['startTime'],clock().isoformat())
        try:
            os.link(temporary,target)
        except FileExistsError:
            saved = load(root,day,rid)
            if digest(saved['prediction']) != digest(race):
                raise ValueError('concurrent formal portfolio prediction conflict')
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    load(root,day,rid)
    return target


def project(root, day, rid, rows, payouts):
    """Settle frozen ticket IDs without selecting tickets from later projections."""
    import portfolio_shadow as portfolio
    record = load(root,day,rid)
    plan = deepcopy(record['plan'])
    for leg in plan['legs']:
        hit = payout = None
        if leg['betType']=='wide':
            cid=leg['candidateID'];a,b=int(cid[:2]),int(cid[2:])
            result=payouts.get((rid,min(a,b),max(a,b)),{})
            hit,payout=result.get('hit'),result.get('payoutPer100Yen')
        else:
            row=rows.get((rid,leg['betType']),{})
            result=next((c for c in row.get('candidates',[]) if c.get('id')==leg['candidateID']),{})
            hit,payout=result.get('hit'),result.get('payoutYen')
        leg['hit']=hit if isinstance(hit,bool) else None
        leg['payoutYen']=payout if type(payout) is int else None
    plan.update(portfolio.evaluate_plan(plan['legs']))
    plan['inputPolicy']=POLICY;plan['inputFingerprint']=record['fingerprint']
    plan['inputRecordedAtJST']=record['recordedAtJST']
    return plan
