"""Evaluate the new frozen Wide-axis rule without using the old ticket history."""
from datetime import datetime
from pathlib import Path
import json
from wide_axis_rules import RULE_VERSION, START, JST, FOLDER, fingerprint, number


def actual_result_state(day, record, post):
    """Use the independently fetched result roster, never forecast pair names."""
    path = day/'ResultData.json'
    if not path.exists():
        return 'awaitingResults'
    results = json.loads(path.read_text())
    if not isinstance(results, list):
        raise ValueError('result race list required')
    rows = [r for r in results if r['raceID'] == record['raceID']]
    if not rows:
        return 'awaitingResults'
    if len(rows) != 1:
        raise ValueError('duplicate actual result race')
    result = rows[0]
    updated = datetime.strptime(result['resultUpdatedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)
    if result['raceDate'] != record['raceDate'] or result['startTime'] != record['startTime'] or updated < post:
        raise ValueError('actual result date/post/publication mismatch')
    horses = result['horses']
    if not isinstance(horses, list):
        raise ValueError('actual result horse list required')
    if not horses:
        return 'awaitingResults'
    if type(result['fieldSize']) is not int or result['fieldSize'] != len(horses):
        raise ValueError('incomplete actual result roster')
    identities = set()
    cancelled = False
    for horse in horses:
        n, name = horse['number'], horse['name']
        if type(n) is not int or n < 1 or not isinstance(name, str) or not name or any(x[0] == n for x in identities):
            raise ValueError('invalid actual entrant identity')
        identities.add((n, name))
        finish, status = horse.get('finishPosition'), str(horse.get('resultStatus') or '').strip()
        if finish is not None:
            if type(finish) is not int or not 1 <= finish <= len(horses):
                raise ValueError('invalid final position')
        elif status not in {'取消', '除外', '中止', '失格'}:
            return 'awaitingResults'
        cancelled = cancelled or status in {'取消', '除外'}
    if cancelled or identities != {(h['number'],h['name']) for h in record['entrants']}:
        return 'entrantChanged'
    return None


def build(root):
    root = Path(root)
    counts = dict(recordedRaces=0, held=0, awaitingResults=0, matched=0, entrantChanged=0, invalid=0, selectedBets=0)
    stake = returned = 0
    warnings = []
    for path in sorted((root/'reports').glob('20??????/'+FOLDER+'/*.json')):
        counts['recordedRaces'] += 1
        try:
            r = json.loads(path.read_text())
            digest = r.pop('recordFingerprint')
            if digest != fingerprint(r) or r['ruleVersion'] != RULE_VERSION or r['raceID'] != path.stem:
                raise ValueError('record fingerprint/version/identity mismatch')
            recorded = datetime.strptime(r['recordedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)
            post = datetime.strptime(r['raceDate']+' '+r['startTime'], '%Y-%m-%d %H:%M').replace(tzinfo=JST)
            e = r['evaluation']
            captured = datetime.strptime(e['capturedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)
            if not START <= recorded < post or captured > recorded or captured.date() != post.date() or r['raceDate'].replace('-','') != path.parent.parent.name:
                raise ValueError('not a genuine same-day pre-post record')
            if e['ruleVersion'] != RULE_VERSION:
                raise ValueError('evaluation rule mismatch')
            if e['status'] == 'no_candidate':
                counts['held'] += 1
                continue
            if e['status'] != 'ready' or not 1 <= len(e['pairs']) <= 3 or e['totalYen'] != 100*len(e['pairs']):
                raise ValueError('invalid saved selection')
            result_state = actual_result_state(path.parent.parent, r, post)
            if result_state:
                counts[result_state] += 1
                continue
            validation_path = path.parent.parent/'WideValidation.json'
            if not validation_path.exists():
                counts['awaitingResults'] += 1
                continue
            validation = json.loads(validation_path.read_text())
            rows = [row for row in validation['races'] if row['raceID'] == r['raceID']]
            if not rows:
                counts['awaitingResults'] += 1
                continue
            if len(rows) != 1:
                raise ValueError('duplicate result race')
            row = rows[0]
            if row.get('validationScope') != 'all_pairs' or row['snapshotCapturedAtJST'] != e['capturedAtJST']:
                raise ValueError('different odds snapshot or incomplete validation')
            entrants = {(p['number1'],p['name1']) for p in row['pairs']} | {(p['number2'],p['name2']) for p in row['pairs']}
            if entrants != {(h['number'],h['name']) for h in r['entrants']}:
                counts['entrantChanged'] += 1
                continue
            pairs = {(min(p['number1'],p['number2']),max(p['number1'],p['number2'])):p for p in row['pairs']}
            if len(pairs) != len(row['pairs']):
                raise ValueError('duplicate validation pair')
            race_return = 0
            for pair in e['pairs']:
                p = pairs[(pair['number1'],pair['number2'])]
                if abs(number(p['oddsLow'])-pair['oddsLow']) > .0001 or abs(number(p['oddsHigh'])-pair['oddsHigh']) > .0001:
                    raise ValueError('saved odds do not match')
                payout = p['payoutPer100Yen']
                if type(payout) is not int or payout < 0 or type(p['hit']) is not bool or p['hit'] != (payout > 0):
                    raise ValueError('invalid payout')
                race_return += payout
            counts['matched'] += 1
            counts['selectedBets'] += len(e['pairs'])
            stake += e['totalYen']
            returned += race_return
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            counts['invalid'] += 1
            warnings.append('記録・結果の対応を確認できません: '+str(path.relative_to(root)))
    return {'schemaVersion':1, 'ruleVersion':RULE_VERSION, 'evaluationStartJST':START.isoformat(),
        'generatedAtJST':datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S'),
        'status':'prospective_data_collecting', 'counts':counts, 'stakeYenAt100PerBet':stake,
        'returnYenAt100PerBet':returned, 'returnIndex':round(returned/stake,4) if stake else None,
        'autoBetEnabled':False, 'moneyAllocationEnabled':False, 'warnings':warnings,
        'meaning':'新ルールの発走前保存だけを評価。確定結果の出走馬と照合し、取消・除外や変更は別集計。旧プランの成績を合算しない。端末の予算制限後の配分成績ではない。'}


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    report = build(root)
    path = root/'reports/WideAxisShadow.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,ensure_ascii=False))
