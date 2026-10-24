"""Versioned Wide hole-axis preview; uses Top3 marginals and joint probabilities."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import os
import re
import tempfile

JST = timezone(timedelta(hours=9))
RULE_VERSION = 'wide-axis-v1-20261014'
START = datetime.fromisoformat('2026-10-14T23:32:47+09:00')
FOLDER = 'wide_axis_v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('finite number required')
    return float(value)


def input_fingerprint(race, signal):
    w = signal.get('wideEvaluation') or {}
    return fingerprint({'raceID':race['raceID'],'raceDate':race['raceDate'],'startTime':race['startTime'],
        'horses':[{k:h.get(k) for k in ('number','name','aiTop3Probability','marketTop3Probability')} for h in race['horses']],
        'capturedAtJST':w.get('capturedAtJST'),'pairs':w.get('validationPairs') or w.get('pairs') or []})


def evaluate(race, signal):
    result = {'schemaVersion': 1, 'ruleVersion': RULE_VERSION, 'status': 'pending',
              'message': '発走前の実ワイドオッズ待ちです。', 'axis': None, 'pairs': [],
              'totalYen': 0, 'minimumSingleHitReturnYen': None, 'capturedAtJST': None}
    try:
        w = signal.get('wideEvaluation') or {}
        if not signal.get('eligible') or race.get('raceID') != signal.get('raceID'):
            result['message'] = '予測データ待ち、または対象外です。'
            return result
        if not w.get('oddsConnected'):
            return result
        day = datetime.strptime(race['raceDate'], '%Y-%m-%d').replace(tzinfo=JST)
        if day.strftime('%Y-%m-%d') != race['raceDate']:
            raise ValueError('canonical day required')
        if race['raceDate'] != signal.get('raceDate') or race['startTime'] != signal.get('startTime'):
            raise ValueError('prediction day/post mismatch')
        post = datetime.strptime(race['raceDate']+' '+race['startTime'], '%Y-%m-%d %H:%M').replace(tzinfo=JST)
        capture = datetime.strptime(w['capturedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)
        if capture.date() != day.date() or capture >= post:
            raise ValueError('not a same-day pre-post snapshot')
        result['capturedAtJST'] = w['capturedAtJST']
        horses = race['horses']
        names = {h['number']: h['name'] for h in horses}
        if len(names) != len(horses):
            raise ValueError('duplicate entrant number')
        if len(horses) < 6:
            result.update(status='no_candidate', message='市場の3着内評価6番手以下の軸候補がありません。')
            return result
        for h in horses:
            if type(h['number']) is not int or h['number'] < 1 or not h['name']:
                raise ValueError('invalid entrant')
            for key in ['aiTop3Probability', 'marketTop3Probability']:
                if not 0 <= number(h[key]) <= 100:
                    raise ValueError('invalid Top3 marginal')
        for key in ['aiTop3Probability', 'marketTop3Probability']:
            if abs(sum(h[key] for h in horses)-300) > .05:
                raise ValueError('missing or inconsistent Top3 marginals')
        by_number = {h['number']: h for h in horses}
        ordered = sorted(horses, key=lambda h: (-h['marketTop3Probability'], h['number']))
        # Shared ranks: a tie at fifth place must not create an arbitrary hole-axis.
        ranks = {h['number']: 1+sum(o['marketTop3Probability'] > h['marketTop3Probability'] for o in horses) for h in ordered}
        pairs = []
        seen = set()
        source = w.get('validationPairs') or w.get('pairs') or []
        if len(source) != len(horses)*(len(horses)-1)//2:
            raise ValueError('complete joint pair coverage required')
        for p in source:
            if type(p['number1']) is not int or type(p['number2']) is not int:
                raise ValueError('integer pair numbers required')
            a, b = sorted((p['number1'], p['number2']))
            if a == b or a not in names or b not in names or (a,b) in seen:
                raise ValueError('invalid/duplicate pair')
            if names[p['number1']] != p['name1'] or names[p['number2']] != p['name2']:
                raise ValueError('entrant name mismatch')
            seen.add((a,b))
            if p.get('wideOddsLow') is None or p.get('wideOddsHigh') is None:
                continue
            probability = number(p['aiWideProbability'])
            low, high = number(p['wideOddsLow']), number(p['wideOddsHigh'])
            if not 0 <= probability <= min(by_number[a]['aiTop3Probability'], by_number[b]['aiTop3Probability'])+.01 or not 1 <= low <= high:
                raise ValueError('joint probability/odds mismatch')
            ev = round(probability/100*low, 4)
            if ev >= 1.10:
                pairs.append({'number1': a, 'name1': names[a], 'number2': b, 'name2': names[b],
                    'probabilityPct': probability, 'oddsLow': low, 'oddsHigh': high, 'expectedValue': ev})
        axes = sorted((h for h in horses if ranks[h['number']] >= 6 and h['aiTop3Probability'] > h['marketTop3Probability']),
            key=lambda h: (-h['aiTop3Probability'], -(h['aiTop3Probability']-h['marketTop3Probability']), h['number']))
        for axis in axes:
            candidates = sorted((p for p in pairs if axis['number'] in (p['number1'], p['number2'])),
                key=lambda p: (-p['probabilityPct'], -p['expectedValue'], p['number1'], p['number2']))
            for count in [3, 2, 1]:
                # At 100 yen each, one hit at saved lower odds covers the whole set.
                chosen = [p for p in candidates if p['oddsLow'] >= count][:count]
                if len(chosen) != count:
                    continue
                result.update(status='ready', message='ワイド向けの穴軸・相手を評価しました。',
                    axis={'number': axis['number'], 'name': axis['name'], 'aiTop3Pct': axis['aiTop3Probability'],
                          'marketTop3Pct': axis['marketTop3Probability'], 'marketTop3Rank': ranks[axis['number']]},
                    pairs=chosen, totalYen=100*count, minimumSingleHitReturnYen=round(min(p['oddsLow'] for p in chosen)*100, 2))
                return result
        result.update(status='no_candidate', message='3着内の評価差・組み合わせの期待倍率に合う穴軸がないため見送りです。')
    except (ValueError, TypeError, KeyError, OverflowError):
        result.update(status='invalid', message='3着内確率・出走馬・実オッズの対応を確認できません。', axis=None, pairs=[], totalYen=0)
    return result


def display_evaluation(root, race, signal):
    """Keep the first saved evaluation; changed inputs must be explicit."""
    path = Path(root)/'reports'/str(race.get('raceDate','')).replace('-','')/FOLDER/(str(race.get('raceID',''))+'.json')
    if not path.exists():
        return evaluate(race, signal)
    result = {'schemaVersion':1,'ruleVersion':RULE_VERSION,'status':'invalid',
              'message':'保存済みの軸評価と現在の入力の対応を確認できません。',
              'axis':None,'pairs':[],'totalYen':0,'minimumSingleHitReturnYen':None,'capturedAtJST':None}
    try:
        record = json.loads(path.read_text())
        digest = record.pop('recordFingerprint')
        if digest != fingerprint(record) or record['ruleVersion'] != RULE_VERSION or record['raceID'] != race['raceID']:
            return result
        if record['sourceFingerprint'] != input_fingerprint(race, signal):
            result['message'] = '保存後に出走馬・3着内確率・オッズが変わったため、新しい仮配分を保留します。'
            return result
        return record['evaluation']
    except (OSError, KeyError, ValueError, TypeError):
        return result


def record_first(root, race, signal, now):
    """Freeze only genuine live attempts; never backfill after post or change a file."""
    if now.tzinfo is None:
        raise ValueError('aware clock required')
    day, rid = race['raceDate'], race['raceID']
    if not re.fullmatch(r'\d{12}', rid):
        raise ValueError('invalid race ID')
    post = datetime.strptime(day+' '+race['startTime'], '%Y-%m-%d %H:%M').replace(tzinfo=JST)
    now = now.astimezone(JST)
    if now < START or now.date() != post.date() or now >= post:
        return None
    result = evaluate(race, signal)
    if result['status'] not in {'ready', 'no_candidate'}:
        return None
    capture = datetime.strptime(result['capturedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)
    if capture > now:
        return None
    path = Path(root)/'reports'/day.replace('-','')/FOLDER/(rid+'.json')
    record = {'schemaVersion':1, 'ruleVersion':RULE_VERSION, 'raceID':rid, 'raceDate':day,
        'venue':race.get('venue',''), 'raceNumber':race.get('raceNumber'), 'raceName':race.get('raceName',''),
        'startTime':race['startTime'], 'recordedAtJST':now.strftime('%Y-%m-%d %H:%M:%S'),
        'entrants': sorted([{'number':h['number'],'name':h['name']} for h in race['horses']], key=lambda h:h['number']),
        'sourceFingerprint':input_fingerprint(race,signal), 'evaluation':result}
    record['recordFingerprint'] = fingerprint(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, suffix='.tmp', delete=False) as f:
            temporary = f.name
            f.write(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return path
