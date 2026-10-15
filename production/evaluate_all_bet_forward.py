"""Add-pool and equal-stake Win+Place package research; no ticket selection."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

import all_bet_baseline as model
from all_bet_forward import (START, VERSION, JST, digest, stamp, validate, evaluate, payout_map,
                             request_bytes, path_for)
from live_bundle_publisher import atomic
from research_triple_baseline import index_unique, load_rows

STATES = ('records','ready','held','awaiting_results','payouts_pending','entrant_changed',
          'dead_heat_excluded','invalid','matched','missingRecords')


def saved_payouts(root,record,fetch,clock):
    race,kind = record['prediction'],record['betType']
    path = root/'reports'/race['raceDate'].replace('-','')/'all_bet_payouts_v1'/kind/(race['raceID']+'.json')
    url = 'https://race.netkeiba.com/race/result.html?race_id='+race['raceID']
    post = stamp(race['raceDate']+'T'+race['startTime']); now = clock()
    if now < post:
        raise ValueError('future payout retrieval')
    if path.exists():
        value = json.loads(path.read_text()); fingerprint = value.pop('fingerprint')
        if (digest(value) != fingerprint or value['recordFingerprint'] != record['recordFingerprint']
                or value['sourceURL'] != url or not post <= stamp(value['retrievedAtJST']) <= now):
            raise ValueError('payout cache identity/timestamp mismatch')
        return value['payouts']
    raw = fetch(url)
    payouts = payout_map(raw,race['raceID'],kind)
    value = {'recordFingerprint':record['recordFingerprint'],'sourceURL':url,
             'sourceFingerprint':hashlib.sha256(raw).hexdigest(),
             'retrievedAtJST':clock().isoformat(),'payouts':payouts}
    value['fingerprint'] = digest(value)
    atomic(path,(json.dumps(value,ensure_ascii=False)+'\n').encode())
    return payouts


def score_rows(values):
    out = {'evaluatedOutcomes':len(values)}
    for key in ('ai','market'):
        leader = min(values,key=lambda r:(-r[key+'Probability'],r['outcome']))
        out[key+'TopOneHit'] = leader['hit']
        out[key+'MeanBrier'] = math.fsum((r[key+'Probability']-int(r['hit']))**2 for r in values)/len(values)
    return out


def package_rows(win,place):
    """An 応援 ticket costs 200 yen: 100 win + 100 place on the same horse.

    Partial payout and profit are different. Never add hit probabilities or
    claim the two correlated legs are independent.
    """
    if set(win) != set(place):
        raise ValueError('package horse coverage mismatch')
    rows = []
    for key,w in win.items():
        p = place[key]
        row = {'outcome':key,'costYen':200,'winProbability':w['aiProbability'],
               'placeProbability':p['aiProbability'],'hit':p['hit'],
               'payoutYen':w['payoutPer100Yen']+p['payoutPer100Yen']}
        for side in ('ai','market'):
            row[side+'Probability'] = p[side+'Probability']
            row[side+'ExpectedMultiplierLow'] = (w[side+'Probability']*w['savedOddsLow']+p[side+'Probability']*p['savedOddsLow'])/2
            row[side+'ExpectedMultiplierHigh'] = (w[side+'Probability']*w['savedOddsHigh']+p[side+'Probability']*p['savedOddsHigh'])/2
        row['profitYen'] = row['payoutYen']-200
        rows.append(row)
    return rows


def build(root,fetch=request_bytes,clock=lambda:datetime.now(JST)):
    root = Path(root)
    counts = {kind:{s:0 for s in STATES} for kind in (*model.TYPES,'support')}
    rows,warnings,joined = [],[],{}
    for path in sorted((root/'reports').glob('*/all_bet_v1/*/*.json')):
        kind = path.parent.name
        if kind not in model.TYPES:
            warnings.append('unknown added pool: '+str(path.relative_to(root))); continue
        counts[kind]['records'] += 1
        try:
            record = validate(json.loads(path.read_text()))
            race = record['prediction']; day = race['raceDate'].replace('-','')
            if path != path_for(root,race,kind) or stamp(record['recordedAtJST']) > clock():
                raise ValueError('record path/time mismatch')
            if record['status'] == 'ready':
                counts[kind]['ready'] += 1
            result_path = root/'reports'/day/'ResultData.json'
            results = index_unique(load_rows(result_path)) if result_path.exists() else {}
            result = results.get((race['raceDate'],race['raceID']))
            if result and stamp(result['resultUpdatedAtJST']) > clock():
                raise ValueError('future result')
            state,_ = model.result_state(race,result)
            payouts = None
            if record['status'] == 'ready' and state == 'matched':
                try:
                    payouts = saved_payouts(root,record,fetch,clock)
                except OSError:
                    pass
            value = evaluate(record,result,payouts)
            counts[kind][value['status']] += 1
            row = {'raceDate':race['raceDate'],'raceID':race['raceID'],'betType':kind,'status':value['status']}
            if value.get('reason'):
                row['reason'] = value['reason']
            if value['status'] == 'matched':
                row.update(score_rows(value['rows']))
            rows.append(row)
            joined.setdefault((race['raceDate'],race['raceID']),{})[kind] = (record,value)
        except (OSError,ValueError,KeyError,TypeError,OverflowError) as error:
            counts[kind]['invalid'] += 1
            warnings.append(str(path.relative_to(root))+': '+type(error).__name__)
    # Support is derived only from the new frozen win/place records, not an
    # extra pool or an extra forecast observation.
    for (day,rid),pools in sorted(joined.items()):
        if not {'win','place'} <= set(pools):
            continue
        counts['support']['records'] += 1
        w,p = pools['win'],pools['place']
        status = 'held'
        if digest(w[0]['prediction']) != digest(p[0]['prediction']):
            status = 'invalid'
        elif all(x[1]['status'] == 'matched' for x in (w,p)):
            status = 'matched'
        elif any(x[1]['status'] == 'invalid' for x in (w,p)):
            status = 'invalid'
        elif not any(x[1]['status'] == 'held' for x in (w,p)):
            status = next(x[1]['status'] for x in (w,p) if x[1]['status'] != 'matched')
        counts['support'][status] += 1
        counts['support']['ready'] += int(all(x[0]['status'] == 'ready' for x in (w,p)))
        row = {'raceDate':day,'raceID':rid,'betType':'support','status':status,
               'sourceRecordFingerprints':[w[0]['recordFingerprint'],p[0]['recordFingerprint']]}
        if status == 'matched':
            values = package_rows({r['outcome']:r for r in w[1]['rows']},{r['outcome']:r for r in p[1]['rows']})
            row.update(score_rows(values)); row['costPerPackageYen'] = 200
            # All candidates are retained for comparison, not chosen wagers.
            row['candidates'] = values
        rows.append(row)
    if (root/'RaceData.json').exists():
        for race in load_rows(root/'RaceData.json'):
            if race['raceDate'] < START.date().isoformat():
                continue
            for kind in model.TYPES:
                if not path_for(root,race,kind).exists():
                    counts[kind]['missingRecords'] += 1
            if any(not path_for(root,race,k).exists() for k in ('win','place')):
                counts['support']['missingRecords'] += 1
    from win5_research import build_validation
    win5 = build_validation(root,clock=clock,fetch=fetch)
    existing = {}
    for key,path in {'wide':'reports/WideAxisShadow.json','trio':'reports/TripleForwardValidation.json',
                     'trifecta':'reports/TripleForwardValidation.json'}.items():
        file = root/path
        item = {'report':path,'available':file.exists(),'collectionMethod':'existing_unchanged'}
        if file.exists():
            try:
                data = json.loads(file.read_text()); item['status'] = data.get('status')
                item['counts'] = data.get('counts',{}).get(key) if key != 'wide' else data.get('counts')
                if data.get('status')=='attention_required':
                    warnings.append(path+': existing research requires attention')
            except (OSError,ValueError,AttributeError,TypeError):
                item['status'] = 'unreadable'; warnings.append(path+': unreadable existing research report')
        else:
            warnings.append(path+': existing research report unavailable')
        existing[key] = item
    attention = bool(warnings) or any(c['invalid'] or c['missingRecords'] for c in counts.values()) or win5['status'] == 'attention_required'
    return {'schemaVersion':1,'version':VERSION,'scope':'JRA domestic flat races',
            'generatedAtJST':clock().isoformat(),'evaluationStartJST':START.isoformat(),
            'betTypes':{**{k:model.LABELS[k] for k in model.TYPES},'wide':'ワイド','trio':'3連複',
                        'trifecta':'3連単','support':'応援馬券（単勝＋複勝・同額）','win5':'WIN5'},
            'counts':counts,'existingResearch':existing,'win5':win5,'races':rows,'warnings':warnings,
            'status':'attention_required' if attention else 'collecting',
            'selectedBets':0,'returnIndex':None,'autoBetEnabled':False,'appDisplayEnabled':False,
            'meaning':'各券種の比較研究。0件は実運用成功や開催なしの証拠ではない。使用済み過去例と新しい発走前記録を合算しない。'}


def main():
    root = Path(__file__).resolve().parent.parent
    value = build(root)
    atomic(root/'reports/AllBetResearchValidation.json',(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())
    print('ALL_BET_FORWARD_RESEARCH',json.dumps(value['counts']),'WIN5',value['win5']['counts'],'warnings',len(value['warnings']))


if __name__ == '__main__':
    main()
