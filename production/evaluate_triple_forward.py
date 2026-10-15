"""Join only real frozen Triple research records with independent final results."""
from datetime import datetime
import json
import hashlib
from pathlib import Path

import research_triple_baseline as baseline
from triple_forward import (JST, START, VERSION, TYPES, LABELS, digest, evaluate, payout_map,
                            request_bytes, validate, stamp)
from live_bundle_publisher import atomic


def saved_payouts(root, record, fetch=request_bytes, clock=lambda:datetime.now(JST)):
    race, kind = record['prediction'], record['betType']
    day = race['raceDate'].replace('-', '')
    path = root/'reports'/day/'triple_payouts_v1'/kind/(race['raceID']+'.json')
    post = stamp(race['raceDate']+'T'+race['startTime'])
    now = clock()
    if now < post:
        raise ValueError('cannot retrieve future results')
    if path.exists():
        value = json.loads(path.read_text())
        fingerprint = value.pop('fingerprint')
        if digest(value) != fingerprint or value['recordFingerprint'] != record['recordFingerprint']:
            raise ValueError('saved payout identity mismatch')
        if not post <= stamp(value['retrievedAtJST']) <= now:
            raise ValueError('payout retrieval timestamp mismatch')
        return value['payouts']
    url = 'https://race.netkeiba.com/race/result.html?race_id='+race['raceID']
    raw = fetch(url)
    payouts = payout_map(raw.decode('utf-8'), race['raceID'], kind)
    value = {'recordFingerprint': record['recordFingerprint'], 'sourceURL': url,
             'sourceFingerprint': hashlib.sha256(raw).hexdigest(),
             'retrievedAtJST': now.isoformat(), 'payouts': payouts}
    value['fingerprint'] = digest(value)
    # Only the normal result-sync job calls this; validation and tests use temp roots.
    atomic(path, (json.dumps(value, ensure_ascii=False)+'\n').encode())
    return payouts


def build(root, fetch=request_bytes, clock=lambda:datetime.now(JST)):
    counts = {kind: {k: 0 for k in ('records', 'ready', 'held', 'awaiting_results', 'payouts_pending',
                                    'entrant_changed', 'dead_heat_excluded', 'invalid', 'matched', 'missingRecords')}
              for kind in TYPES}
    rows, warnings = [], []
    for path in sorted((root/'reports').glob('*/triple_v1/*/*.json')):
        kind = path.parent.name
        if kind not in TYPES:
            warnings.append('unknown Triple research bet type: '+str(path.relative_to(root)))
            continue
        counts[kind]['records'] += 1
        try:
            record = validate(json.loads(path.read_text()))
            race = record['prediction']; day = race['raceDate'].replace('-', '')
            if stamp(record['recordedAtJST']) > clock():
                raise ValueError('future record')
            if (kind != record['betType'] or path.stem != race['raceID'] or path.parent.parent.parent.name != day):
                raise ValueError('record path identity mismatch')
            if record['status'] != 'ready':
                counts[kind]['held'] += 1
                rows.append({'raceDate':race['raceDate'],'raceID':race['raceID'],'betType':kind,'status':'held'})
                continue
            counts[kind]['ready'] += 1
            result_path = root/'reports'/day/'ResultData.json'
            results = baseline.index_unique(baseline.load_rows(result_path)) if result_path.exists() else {}
            result = results.get((race['raceDate'], race['raceID']))
            state, _ = baseline.result_state(race, result)
            if result and stamp(result['resultUpdatedAtJST']) > clock():
                raise ValueError('future result')
            if state != 'matched_retrospective':
                joined = {'status': state}
            else:
                try:
                    payouts = saved_payouts(root, record, fetch, clock)
                except OSError:
                    payouts = None
                joined = evaluate(record, result, payouts)
            state = joined['status']
            counts[kind][state] += 1
            row = {'raceDate':race['raceDate'],'raceID':race['raceID'],'betType':kind,'status':state}
            if state == 'matched':
                values = joined['rows']
                row['evaluatedOutcomes'] = len(values)
                for model in ('ai','market'):
                    leader = min(values, key=lambda x:(-x[model+'Probability'], x['outcome']))
                    row[model+'TopOneHit'] = leader['hit']
                    row[model+'ActualOutcomeProbability'] = next(x[model+'Probability'] for x in values if x['hit'])
            rows.append(row)
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            counts[kind]['invalid'] += 1
            warnings.append(str(path.relative_to(root))+': '+type(error).__name__)
    if (root/'RaceData.json').exists():
        for race in baseline.load_rows(root/'RaceData.json'):
            if race['raceDate'] < START.date().isoformat():
                continue
            for kind in TYPES:
                target = root/'reports'/race['raceDate'].replace('-','')/'triple_v1'/kind/(race['raceID']+'.json')
                if not target.exists():
                    counts[kind]['missingRecords'] += 1
    return {'schemaVersion': 1, 'version': VERSION, 'evaluationStartJST': START.isoformat(),
            'counts': counts, 'races': rows, 'warnings': warnings,
            'status': 'attention_required' if warnings or any(c['invalid'] or c['missingRecords'] for c in counts.values())
                      else 'collecting',
            'selectedBets': 0, 'returnIndex': None, 'autoBetEnabled': False, 'appDisplayEnabled': False,
            'meaning': '発走前の初回記録だけを別研究として照合。全組の検証であり、選択した買い目の回収率ではない。'}


def main():
    root = Path(__file__).resolve().parent.parent
    value = build(root)
    atomic(root/'reports/TripleForwardValidation.json', (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())
    print('TRIPLE_FORWARD_RESEARCH', json.dumps(value['counts']), 'warnings', len(value['warnings']))


if __name__ == '__main__':
    main()
