"""Five-leg research with one common deadline. No stakes, training or app writes."""
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re

from bs4 import BeautifulSoup
import all_bet_baseline as model
from all_bet_forward import START, JST, digest, stamp, write_first, validate_entry, parse_table
from triple_forward import request_bytes
from live_bundle_publisher import atomic
from research_triple_baseline import index_unique, load_rows

VERSION = 'win5-factorized-win-v1-20261015'
OFFICIAL = 'https://www.jra.go.jp/kouza/win5/info/racelist.html'
VENUES = {'01':'札幌','02':'函館','03':'福島','04':'新潟','05':'東京','06':'中山','07':'中京','08':'京都','09':'阪神','10':'小倉'}


def official_targets(raw,day):
    """Require the current official table's year, row, five legs and deadline."""
    soup = BeautifulSoup(raw,'html.parser')
    headings = ' '.join(h.get_text(' ',strip=True) for h in soup.select('h1,h2'))
    limit = re.search(r'(\d{4})年\s*(\d+)月\s*(\d+)日.*まで',headings)
    if not limit:
        raise ValueError('official WIN5 table coverage unavailable')
    end = datetime(int(limit[1]),int(limit[2]),int(limit[3])).date()
    date = datetime.strptime(day,'%Y-%m-%d').date()
    if date.year != end.year or date > end:
        raise ValueError('official WIN5 table does not cover requested date')
    dates, matched = [],[]
    for row in soup.select('tr'):
        cells = row.find_all('td',recursive=False)
        if len(cells) != 7:
            continue
        match = re.match(r'(\d+)月\s*(\d+)日',cells[0].get_text(strip=True))
        if not match:
            continue
        rowday = datetime(end.year,int(match[1]),int(match[2])).date()
        dates.append(rowday)
        if rowday == date:
            matched.append(cells)
    if not dates or not min(dates) <= date <= max(dates) or len(matched) > 1:
        raise ValueError('official WIN5 date range/row ambiguous')
    if not matched:
        return None
    cells = matched[0]
    legs = []
    for cell in cells[2:]:
        text = cell.get_text('',strip=True)
        m = re.fullmatch(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)(\d{1,2})R',text)
        if not m or not 1 <= int(m[2]) <= 12:
            raise ValueError('invalid official WIN5 leg')
        legs.append({'venue':m[1],'raceNumber':int(m[2])})
    if len({(r['venue'],r['raceNumber']) for r in legs}) != 5:
        raise ValueError('duplicate official WIN5 leg')
    deadline = re.search(r'(\d{1,2})時\s*(\d{1,2})分',cells[1].get_text(' ',strip=True))
    if not deadline:
        raise ValueError('published common WIN5 deadline unavailable')
    return {'raceDate':day,'legs':legs,'deadlineJST':stamp(day+f'T{int(deadline[1]):02}:{int(deadline[2]):02}').isoformat(),
            'officialSourceURL':OFFICIAL,'officialSourceFingerprint':hashlib.sha256(raw).hexdigest()}


def resolve_plan(targets,schedule):
    plan = json.loads(json.dumps(targets)); legs = []
    for leg in plan['legs']:
        found = [r for r in schedule if r['venue'] == leg['venue'] and r['race_no'] == leg['raceNumber']]
        if len(found) != 1:
            raise ValueError('official WIN5 leg not uniquely present in daily schedule')
        race = found[0]
        if (race['race_id'][4:6] not in VENUES or VENUES[race['race_id'][4:6]] != leg['venue']
                or int(race['race_id'][-2:]) != leg['raceNumber']):
            raise ValueError('WIN5 schedule race identity mismatch')
        legs.append(dict(race))
    posts = [stamp(plan['raceDate']+'T'+r['start_time']) for r in legs]
    if len({r['race_id'] for r in legs}) != 5 or posts != sorted(posts) or posts[0]-timedelta(minutes=5) != stamp(plan['deadlineJST']):
        raise ValueError('official deadline and scheduled first post disagree')
    plan['schedule'] = legs
    return plan


def validate(record):
    value = dict(record); fingerprint = value.pop('recordFingerprint')
    if digest(value) != fingerprint or record['version'] != VERSION or record['schemaVersion'] != 1:
        raise ValueError('WIN5 record fingerprint/version mismatch')
    plan = record['plan']; day = plan['raceDate']; deadline = stamp(plan['deadlineJST'])
    resolved = resolve_plan(plan,plan['schedule'])
    if resolved['schedule'] != plan['schedule'] or plan['officialSourceURL'] != OFFICIAL:
        raise ValueError('WIN5 plan mismatch')
    if not re.fullmatch('[a-f0-9]{64}',plan['officialSourceFingerprint']):
        raise ValueError('WIN5 official source evidence missing')
    began,recorded = stamp(record['startedAtJST']),stamp(record['recordedAtJST'])
    if not START <= began <= recorded < deadline or began.date().isoformat() != day or recorded.date().isoformat() != day:
        raise ValueError('WIN5 common deadline/day mismatch')
    if record['status'] == 'held':
        if not record.get('reason'):
            raise ValueError('WIN5 held reason missing')
        return record
    if record['status'] != 'ready' or len(record['predictions']) != 5:
        raise ValueError('five eligible predictions required')
    required = {'lightgbm_fixed_2025.txt','lightgbm_top2_fixed_2025.txt','lightgbm_top3_fixed_2025.txt',
                'production_history.parquet','feature_engine.py','live_monitor_topk.py','all_bet_baseline.py','win5_research.py'}
    if (set(record.get('implementationHashes',{})) != required
            or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in record['implementationHashes'].values())):
        raise ValueError('fixed implementation evidence unavailable')
    for file in (Path(model.__file__),Path(__file__)):
        if record['implementationHashes'][file.name] != hashlib.sha256(file.read_bytes()).hexdigest():
            raise ValueError('frozen WIN5 comparison implementation unavailable')
    for schedule,leg in zip(plan['schedule'],record['predictions']):
        race = leg['prediction']; model.race_key(race)
        if (race['raceID'] != schedule['race_id'] or race['raceDate'] != day
                or race['startTime'] != schedule['start_time'] or race['venue'] != schedule['venue']):
            raise ValueError('WIN5 frozen leg identity mismatch')
        available = stamp(leg['predictionAvailableAtJST'])
        source = leg['winOddsSource']; raw = source['payload']; request = stamp(source['requestStartedAtJST']); received = stamp(source['receivedAtJST'])
        if not began <= request <= received <= available <= recorded < deadline:
            raise ValueError('WIN5 input arrived after common deadline')
        if raw.get('status') != 'middle':
            raise ValueError('WIN5 pre-race win odds required')
        official_time = stamp(raw['data']['official_datetime'])
        if official_time.date().isoformat() != day or not official_time <= received < deadline:
            raise ValueError('WIN5 win odds source time mismatch')
        validate_entry(leg['entryEvidence'],race,available)
        if stamp(leg['entryEvidence']['capturedAtJST']) < began:
            raise ValueError('WIN5 reused old entry input')
        for key in ('aiProbability','marketProbability'):
            expected = model.probabilities(race['horses'],key,'win')
            odds = parse_table(raw['data']['odds']['1'],expected,'win')
            if key == 'marketProbability':
                implied = {n:1/row['low'] for n,row in odds.items()}; total = math.fsum(implied.values())
                if any(abs(expected[n]-implied[n]/total) > 1e-8 for n in expected):
                    raise ValueError('WIN5 prediction/market odds input mismatch')
    return record


def capture(root,plan,predict,*,clock=lambda:datetime.now(JST),hashes=None):
    path = Path(root)/'reports'/plan['raceDate'].replace('-','')/'win5_v1/session.json'
    if path.exists():
        validate(json.loads(path.read_text())); return path
    began,deadline = clock(),stamp(plan['deadlineJST'])
    if not START <= began < deadline or began.date().isoformat() != plan['raceDate']:
        return None
    record = {'schemaVersion':1,'version':VERSION,'plan':plan,'startedAtJST':began.isoformat(),
              'status':'held','reason':'prediction_or_source_unavailable','predictions':[],
              'implementationHashes':hashes or {}}
    try:
        for race in plan['schedule']:
            if clock() >= deadline:
                return None
            record['predictions'].append(predict(race))
        record.update(status='ready'); record.pop('reason')
    except Exception as error:
        record['errorType'] = type(error).__name__
    record['recordedAtJST'] = clock().isoformat()
    if stamp(record['recordedAtJST']) >= deadline:
        return None
    record['recordFingerprint'] = digest(record)
    try:
        validate(record)
    except (ValueError,KeyError,TypeError):
        record.update(status='held',reason='prediction_input_or_deadline_invalid')
        record.pop('recordFingerprint'); record['recordFingerprint'] = digest(record); validate(record)
    return write_first(path,record,deadline,clock)


def probability_score(record,results):
    validate(record)
    if record['status'] != 'ready':
        return {'status':'held','reason':record['reason']}
    truth,ai,market,ai_top,market_top,combinations = [],1.,1.,[],[],1
    for leg in record['predictions']:
        race = leg['prediction']; result = results.get((race['raceDate'],race['raceID']))
        state,finish = model.result_state(race,result)
        if state != 'matched':
            return {'status':state}
        winner = model.outcome_key(finish[:1]); truth.append(winner)
        probabilities = [model.probabilities(race['horses'],key,'win') for key in ('aiProbability','marketProbability')]
        ai *= probabilities[0][winner]; market *= probabilities[1][winner]
        ai_top.append(min(probabilities[0],key=lambda k:(-probabilities[0][k],k)) == winner)
        market_top.append(min(probabilities[1],key=lambda k:(-probabilities[1][k],k)) == winner)
        combinations *= len(race['horses'])
    return {'status':'probability_matched','winningOutcome':truth,'combinationCount':combinations,
            'aiActualOutcomeProbability':ai,'marketActualOutcomeProbability':market,
            'aiNegativeLogProbability':-math.log(ai),'marketNegativeLogProbability':-math.log(market),
            'aiTopOneAllFiveHit':all(ai_top),'marketTopOneAllFiveHit':all(market_top),
            'aiTopOneLegHits':sum(ai_top),'marketTopOneLegHits':sum(market_top),
            'assumption':'レース間の条件付き独立を仮定した勝率の積。全組は積の形で保持し、点数分を購入する意味ではない。'}


def build_validation(root,clock=lambda:datetime.now(JST)):
    root = Path(root)
    counts = {k:0 for k in ('records','ready','held','awaiting_results','entrant_changed','dead_heat_excluded','invalid','probability_matched','missingRecords','failedSessions')}
    rows,warnings,sessions = [],[],[]
    for path in sorted((root/'reports').glob('*/win5_v1/session.json')):
        counts['records'] += 1
        try:
            record = validate(json.loads(path.read_text())); day = record['plan']['raceDate']
            if path.parent.parent.name != day.replace('-','') or stamp(record['recordedAtJST']) > clock():
                raise ValueError('WIN5 record path/time mismatch')
            counts['ready'] += int(record['status'] == 'ready')
            result_path = root/'reports'/day.replace('-','')/'ResultData.json'
            results = index_unique(load_rows(result_path)) if result_path.exists() else {}
            if any(stamp(r['resultUpdatedAtJST']) > clock() for r in results.values()):
                raise ValueError('future WIN5 result')
            score = probability_score(record,results); counts[score['status']] += 1
            rows.append({'raceDate':day,**score})
        except (OSError,ValueError,KeyError,TypeError,OverflowError) as error:
            counts['invalid'] += 1; warnings.append(str(path.relative_to(root))+': '+type(error).__name__)
    for path in sorted((root/'reports').glob('*/win5_status.json')):
        try:
            journal = json.loads(path.read_text())
            if journal['raceDate'].replace('-','') != path.parent.name:
                raise ValueError('WIN5 journal date mismatch')
            sessions.append(journal)
            if journal['status'] in {'failed','checking','awaiting_capture','common_deadline_missed'}:
                counts['failedSessions'] += 1
            if journal.get('hasTarget') and not (path.parent/'win5_v1/session.json').exists():
                counts['missingRecords'] += 1
                rows.append({'raceDate':journal['raceDate'],'status':'missing_record','reason':journal['status']})
            elif journal.get('hasTarget') is None:
                warnings.append(str(path.relative_to(root))+': official target check incomplete')
        except (OSError,ValueError,KeyError,TypeError) as error:
            counts['failedSessions'] += 1
            warnings.append(str(path.relative_to(root))+': '+type(error).__name__)
    return {'version':VERSION,'counts':counts,'races':rows,'sessions':sessions,'warnings':warnings,
            'status':'attention_required' if warnings or counts['invalid'] or counts['missingRecords'] or counts['failedSessions'] else 'collecting',
            'payoutResearchStatus':'not_connected','returnIndex':None,'selectedBets':0,
            'autoBetEnabled':False,'appDisplayEnabled':False,
            'meaning':'5レース共通締切前の確率研究。WIN5の確定払戻・回収率の検証は未接続。単勝オッズ5本の積をWIN5オッズにしない。'}


def main():
    """Only the regular future scheduled workflow invokes this production entry."""
    root = Path(__file__).resolve().parent.parent
    now = datetime.now(JST); day = now.date().isoformat(); date8 = now.strftime('%Y%m%d')
    journal = {'version':VERSION,'raceDate':day,'startedAtJST':now.isoformat(),
               'hasTarget':None,'status':'checking','officialSourceURL':OFFICIAL}
    journal_path = root/'reports'/date8/'win5_status.json'
    try:
        raw = request_bytes(OFFICIAL)
        journal['officialSourceFingerprint'] = hashlib.sha256(raw).hexdigest()
        targets = official_targets(raw,day)
        journal['hasTarget'] = targets is not None
        if '--check' in __import__('sys').argv:
            output = os.environ.get('GITHUB_OUTPUT')
            if output:
                with open(output,'a') as stream:
                    stream.write(f'has_target={str(targets is not None).lower()}\n')
                    stream.write(f'target_date={date8}\n')
            journal['status'] = 'awaiting_capture' if targets else 'no_official_win5_target'
            return
        if targets is None:
            journal['status'] = 'no_official_win5_target'; return
        journal['deadlineJST'] = targets['deadlineJST']
        if now >= stamp(targets['deadlineJST']):
            journal['status'] = 'common_deadline_missed'; return
        # Separate noon inputs; never import the app-publishing wrapper, and
        # explicitly disable the unrelated Pace observation side effects.
        os.environ['KEIBAAI_CAPTURE_PRERACE'] = 'false'
        os.environ['KEIBAAI_LIVE_DATE'] = date8
        import pandas as pd
        import live_monitor_dry_run as base
        import live_monitor_topk as topk
        plan = resolve_plan(targets,base.fetch_schedule(date8))
        target_date = pd.Timestamp(now.date()); history = base.load_history(target_date)
        def predict(race):
            out = topk.predict_live_race_topk(race,history,target_date)
            forecast = {'raceID':race['race_id'],'raceDate':day,'startTime':race['start_time'],
                        'venue':race['venue'],'raceNumber':race['race_no'],
                        'horses':[{'number':int(r['馬番']),'name':str(r['馬名']),
                                   'aiProbability':float(r['AI勝率(%)']),'marketProbability':float(r['市場勝率(%)'])}
                                  for _,r in out.iterrows()]}
            return {'prediction':forecast,'predictionAvailableAtJST':datetime.now(JST).isoformat(),
                    'entryEvidence':out.attrs.get('allBetEntryEvidence'),'winOddsSource':out.attrs.get('winOddsSource')}
        files = [base.MODEL_PATH,topk.TOP2_MODEL_PATH,topk.TOP3_MODEL_PATH,base.HISTORY_PATH,
                 root/'production/feature_engine.py',root/'production/live_monitor_topk.py',
                 Path(model.__file__),Path(__file__)]
        hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        path = capture(root,plan,predict,hashes=hashes)
        journal['status'] = json.loads(path.read_text())['status'] if path else 'common_deadline_missed'
    except BaseException as error:
        journal.update(status='failed',errorType=type(error).__name__)
        raise
    finally:
        journal['finishedAtJST'] = datetime.now(JST).isoformat()
        atomic(journal_path,(json.dumps(journal,ensure_ascii=False,indent=2)+'\n').encode())
        print('WIN5_RESEARCH_SESSION',json.dumps(journal,ensure_ascii=False))


if __name__ == '__main__':
    main()
