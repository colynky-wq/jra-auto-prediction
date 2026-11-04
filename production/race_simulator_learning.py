"""Prospective simulator journal, paired evaluation and gated model promotion.

Default CLI is capture-only for the live outbox. ``cycle`` evaluates already
committed forecasts, fits one challenger, and makes one fixed-cohort decision.
No result, prediction-AI probability, odds or popularity enters race motion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import tempfile
import unicodedata

from race_simulator_calibration import (BASELINE_ID, active_model, baseline, canonical,
    digest, engine_digest, load_model, load_registry, settings, validate_model)
from race_simulator_v02 import HorseInput, simulate_race
from race_simulator_v02_adapter import load_profiles_for_race, prepare_motion_inputs, simulate_current_keibaai_v02

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OBJECTIVE = 'brier-rank-sectionals-v1'


@dataclass(frozen=True)
class Policy:
    min_training_races: int = 30
    min_training_days: int = 2
    min_validation_races: int = 30
    min_validation_days: int = 2
    min_relative_improvement: float = 0.02
    min_clock_coverage: float = 0.60
    bootstrap_draws: int = 2000
    training_runs: int = 40
    training_window: int = 60
    capture_runs: int = 250


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('timezone-aware timestamp required')
    return parsed


def stamp(now):
    if now.tzinfo is None: raise ValueError('timezone-aware clock required')
    return now.astimezone(timezone.utc).isoformat()


def read_json(path, default=None):
    path = Path(path)
    if not path.exists(): return default
    def invalid(value): raise ValueError('nonfinite JSON: ' + value)
    return json.loads(path.read_text(), parse_constant=invalid)


def write_json(path, payload, immutable=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(payload) + b'\n'
    if path.exists() and path.read_bytes() == data: return False
    if immutable and path.exists(): raise ValueError('immutable record conflict: ' + str(path))
    fd, name = tempfile.mkstemp(prefix='.rs-learning-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        if immutable: os.link(tmp, path)
        else: tmp.replace(path)
    finally: tmp.unlink(missing_ok=True)
    return True


def post_time(race):
    rid = str(race.get('raceID', ''))
    if not re.fullmatch(r'\d{12}', rid): raise ValueError('invalid race ID')
    day = str(race['raceDate'])
    post = datetime.strptime(day + ' ' + race['startTime'], '%Y-%m-%d %H:%M').replace(tzinfo=JST)
    if rid[:4] != day[:4]: raise ValueError('race year differs')
    return post


def journal_path(root, race):
    post_time(race)
    return Path(root)/'reports'/race['raceDate'].replace('-', '')/'race_simulator_v02'/f"{race['raceID']}.json"


def compact(sim):
    return {key: sim[key] for key in ('distanceMeters', 'pace', 'settings', 'horses', 'flowPrediction')}


def predict(motion, model, runs, seed):
    validate_model(model)
    horses = [HorseInput(**row) for row in motion['horses']]
    if any(h.prediction_ai_probability is not None for h in horses):
        raise ValueError('prediction AI is not a motion input')
    result = simulate_race(horses, motion['distanceMeters'], motion['pace'], runs, seed,
                           motion['dtSeconds'], motion['dtSeconds'], calibration=model['settings'])
    from build_race_simulation_data_v02 import audit_race
    audit = audit_race({**result, 'independence': {'usesPredictionAIForMotion': False,
        'usesMarketProbabilityForMotion': False, 'usesOddsForMotion': False}})
    if audit['status'] != 'pass': raise ValueError('simulator audit failed: ' + str(audit))
    return compact(result)


def clean_sources(race, research, profiles):
    """Discard aggregates without a provable strictly earlier history day."""
    day = race['raceDate']; known = {h['number']: h for h in race['horses']}
    rr = None
    for row in (research or {}).get('races', []):
        if row.get('raceID') != race['raceID']: continue
        if row.get('raceDate') != day: raise ValueError('research date mismatch')
        rr = json.loads(json.dumps(row)); rr.pop('formation', None)
        if {h['number']: h['name'] for h in row.get('horses', [])} != {n: h['name'] for n,h in known.items()}:
            raise ValueError('research entrant identity mismatch')
        for h in rr['horses']:
            for key in ('styleTendency', 'initialPositionHistory'):
                source = h.get(key) or {}; latest = str(source.get('latestHistoryDate') or '')[:10]
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', latest) or latest >= day:
                    h.pop(key, None)
        break
    safe = {'profiles': {}}
    for key, p in profiles.get('profiles', {}).items():
        latest = str(p.get('latestDate') or '')[:10]
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', latest) and latest < day:
            safe['profiles'][key] = p
    return {'races': [rr]} if rr else None, safe


def capture_one(root, race, research, profiles, now=None, policy=Policy(), clock=None):
    now = now or datetime.now(timezone.utc); post = post_time(race)
    path = journal_path(root, race)
    if path.exists(): return None
    if not now < post - timedelta(seconds=30): raise ValueError('not before capture deadline')
    if race.get('predictionTiming') != 'live_10min' or race.get('captureQuality') != 'formal_10min':
        raise ValueError('formal pre-race input required')
    captured = timestamp(race['capturedAtJST'])
    if not captured <= now < post or captured.astimezone(JST).date() != post.date():
        raise ValueError('input not available before post')
    safe_research, safe_profiles = clean_sources(race, research, profiles)
    _, horses, distance, pace, _, _, _ = prepare_motion_inputs([race], safe_research, safe_profiles, race['raceID'])
    motion_horses = []
    for horse in horses:
        row = asdict(horse); row['prediction_ai_probability'] = None; motion_horses.append(row)
    motion = {'horses': motion_horses, 'distanceMeters': distance, 'pace': pace, 'dtSeconds': 0.5}
    state = load_registry(root); active = load_model(root, state['activeModelID'])
    models = [active]
    if state.get('candidateModelID'):
        candidate = load_model(root, state['candidateModelID'])
        if candidate['baselineModelID'] != active['modelID']: raise ValueError('candidate baseline changed')
        if timestamp(candidate['createdAt']) > now: raise ValueError('candidate created after capture')
        models.append(candidate)
    seed = 20261104 + int(digest(race['raceID'])[:8], 16) % 1000000
    forecasts = {m['modelID']: {'model': m, 'prediction': predict(motion, m, policy.capture_runs, seed)} for m in models}
    display = simulate_current_keibaai_v02([race], safe_research, safe_profiles, race['raceID'],
        policy.capture_runs, seed, 0.5, calibration=active['settings'])
    end = clock() if clock else datetime.now(timezone.utc)
    # Tests inject the wall clock as well as the capture clock; production cannot backdate it.
    if not captured <= now <= end < post: raise ValueError('post time reached while simulating')
    display.update(raceDate=race['raceDate'], raceNumber=race.get('raceNumber'), venue=race.get('venue'),
                   startTime=race['startTime'], prospectiveConfirmed=True, modelID=active['modelID'], predictionRecordedAt=stamp(end))
    from build_race_simulation_data_v02 import audit_race
    display['audit'] = audit_race(display)
    if display['audit']['status'] != 'pass': raise ValueError('display simulation failed audit')
    record = {'schemaVersion': 1, 'raceID': race['raceID'], 'raceDate': race['raceDate'],
        'startTime': race['startTime'], 'scheduledPost': stamp(post), 'recordedAt': stamp(end),
        'inputCapturedAt': stamp(captured), 'engineSHA256': engine_digest(), 'seed': seed,
        'activeModelID': active['modelID'], 'candidateModelID': state.get('candidateModelID'),
        'inputSHA256': digest({'race': race, 'research': safe_research, 'profiles': safe_profiles}),
        'motion': motion, 'forecasts': forecasts, 'displaySimulation': display}
    record['recordSHA256'] = digest(record)
    write_json(path, record, immutable=True)
    if not (clock() if clock else datetime.now(timezone.utc)) < post:
        path.unlink(); raise ValueError('post time reached during record write')
    return record


def capture_upcoming(root=ROOT, now=None, policy=Policy(), clock=None):
    root = Path(root); now = now or datetime.now(timezone.utc)
    races = read_json(root/'RaceData.json', [])
    research = read_json(root/'AppResearchData.json', {})
    batch = read_json(root/'RaceSimulationData.json', {})
    outputs = {r['raceID']: r for r in batch.get('races', [])}
    saved = []; held = []; historical = 0; changed = False
    for race in races:
        try:
            post = post_time(race)
            if now >= post:
                historical += 1; continue
            if post.date() != now.astimezone(JST).date(): continue
            profiles = load_profiles_for_race(race, root/'HorseProfiles')
            record = capture_one(root, race, research, profiles, now, policy, clock)
            if record:
                saved.append(record['raceID']); outputs[record['raceID']] = record['displaySimulation']; changed = True
            elif record is None:
                frozen = read_json(journal_path(root, race))
                if frozen:
                    display = frozen_display(frozen, race)
                    if outputs.get(race['raceID']) != display: changed = True
                    outputs[race['raceID']] = display
        except (ValueError, KeyError, TypeError) as exc:
            held.append({'raceID': race.get('raceID'), 'reason': str(exc)})
    if changed:
        day = now.astimezone(JST).date().isoformat()
        rows = sorted((r for r in outputs.values() if r.get('raceDate') == day), key=lambda r:(r.get('startTime',''),r['raceID']))
        batch = {'schemaVersion': 'race-simulator-v0.2-batch', 'generatedAt': stamp(now), 'status': 'ok',
            'targetRaceDate': day, 'settings': {'runsPerRace': policy.capture_runs, 'baseSeed': 20261104, 'dtSeconds': 0.5},
            'summary': {'inputRaces': len(rows), 'simulatedRaces': len(rows), 'skippedRaces': 0, 'errorRaces': 0},
            'races': rows, 'learning': {'activeModelID': active_model(root)['modelID'], 'prospectiveCapture': True}}
        write_json(root/'RaceSimulationData.json', batch)
    return {'saved': saved, 'held': held, 'historicalRacesExcluded': historical}


def frozen_display(record, race):
    if record.get('recordSHA256') != digest({k:v for k,v in record.items() if k != 'recordSHA256'}):
        raise ValueError('forecast record hash mismatch')
    if record['raceID'] != race['raceID'] or record['raceDate'] != race['raceDate']:
        raise ValueError('frozen display identity mismatch')
    if not timestamp(record['inputCapturedAt']) <= timestamp(record['recordedAt']) < timestamp(record['scheduledPost']):
        raise ValueError('frozen display was recorded after post')
    if timestamp(record['scheduledPost']) != post_time(race): raise ValueError('scheduled post changed')
    if {h['number']:name_key(h['name']) for h in record['motion']['horses']} != {h['number']:name_key(h['name']) for h in race['horses']}:
        raise ValueError('frozen display entrants changed')
    return record['displaySimulation']


def first_committed_at(root, path):
    relative = str(Path(path).relative_to(root))
    r = subprocess.run(['git', 'log', '--no-renames', '--diff-filter=A', '--format=%H%x00%cI', '--', relative],
                       cwd=root, capture_output=True, text=True, timeout=20, check=True)
    commits = [(timestamp(line.split('\0')[1]), line.split('\0')[0]) for line in r.stdout.splitlines() if line.strip()]
    if not commits: return None
    made, sha = min(commits)
    original = subprocess.run(['git','show',f'{sha}:{relative}'],cwd=root,capture_output=True,timeout=20,check=True).stdout
    if original != Path(path).read_bytes(): raise ValueError('journal modified since first commit')
    return made


def validate_record(record, committed_at):
    if record.get('schemaVersion') != 1: raise ValueError('unsupported forecast record')
    if record.get('recordSHA256') != digest({k:v for k,v in record.items() if k != 'recordSHA256'}):
        raise ValueError('forecast record hash mismatch')
    post = timestamp(record['scheduledPost']); made = timestamp(record['recordedAt'])
    if not timestamp(record['inputCapturedAt']) <= made < post: raise ValueError('retrospective forecast')
    if post != post_time(record): raise ValueError('forecast schedule identity mismatch')
    if committed_at is None or not made-timedelta(seconds=2) <= committed_at < post:
        raise ValueError('no durable pre-post commit evidence')
    if record['engineSHA256'] != engine_digest(): raise ValueError('different simulator engine version')
    if any(h.get('prediction_ai_probability') is not None for h in record['motion']['horses']):
        raise ValueError('prediction AI in motion inputs')
    expected = {record['activeModelID']}
    if record.get('candidateModelID'): expected.add(record['candidateModelID'])
    if set(record['forecasts']) != expected: raise ValueError('forecast model set mismatch')
    for key, forecast in record['forecasts'].items():
        model = validate_model(forecast['model'])
        if key != model['modelID']: raise ValueError('forecast model identity mismatch')
        if model.get('createdAt') and timestamp(model['createdAt']) > made: raise ValueError('model not available at capture')
        if model.get('trainingCutoff') and timestamp(model['trainingCutoff']) >= made: raise ValueError('model trained on future outcomes')
        if record['raceID'] in model.get('trainingRaceIDs', []): raise ValueError('forecast is in its own training data')
    if record.get('candidateModelID'):
        candidate = record['forecasts'][record['candidateModelID']]['model']
        if candidate.get('baselineModelID') != record['activeModelID']: raise ValueError('candidate baseline mismatch')
    return record


def name_key(value):
    return ''.join(unicodedata.normalize('NFKC', str(value)).split())


def match_result(record, result, now):
    if result.get('raceID') != record['raceID'] or result.get('raceDate') != record['raceDate']:
        raise ValueError('result race identity mismatch')
    if now <= timestamp(record['scheduledPost']): raise ValueError('race has not started')
    expected = {h['number']: name_key(h['name']) for h in record['motion']['horses']}
    actual = result.get('horses', [])
    if len(actual) != len(expected) or {h['number']: name_key(h['name']) for h in actual} != expected:
        raise ValueError('result entrant set/name changed')
    for horse in actual:
        rank = horse.get('finishPosition')
        if isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= len(expected):
            raise ValueError('result incomplete or entrant withdrawn')
    if not any(h['finishPosition'] == 1 for h in actual): raise ValueError('winner unavailable')
    return result


def metrics(prediction, result):
    rows = {h['horseNumber']:h for h in prediction['horses']}
    actual = {h['number']:h for h in result['horses']}
    if len(rows) != len(prediction['horses']) or set(rows) != set(actual): raise ValueError('forecast entrant mismatch')
    n = len(rows); winners = [k for k,h in actual.items() if h['finishPosition'] == 1]
    probabilities = [float(rows[k]['simulationWinProbability']) for k in rows]
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities) or abs(sum(probabilities)-1) > 0.001:
        raise ValueError('invalid win probabilities')
    if any(not 1 <= rows[k]['expectedFinish'] <= n for k in rows): raise ValueError('invalid expected rank')
    brier = sum((rows[k]['simulationWinProbability'] - (1/len(winners) if k in winners else 0))**2 for k in rows)
    rank_mae = statistics.mean(abs(rows[k]['expectedFinish']-actual[k]['finishPosition'])/(n-1) for k in rows)
    lap = result.get('raceLap') or {}; flags = lap.get('validation') or {}
    clock_valid = (lap.get('raceID') == result['raceID'] and lap.get('raceDate') == result['raceDate']
        and lap.get('distanceM') == prediction['distanceMeters']
        and all(flags.get(k) is True for k in ('headingMatches','distanceMatches','winnerTimeMatches','raceLast600mMatches')))
    out = {'winnerBrier': brier, 'normalizedRankMAE': rank_mae,
           'winnerTimeMAPE': None, 'first600MAPE': None, 'last600MAPE': None}
    if clock_valid:
        winner_time = lap.get('winnerTimeSeconds')
        if isinstance(winner_time, (int,float)) and math.isfinite(winner_time) and winner_time > 0:
            out['winnerTimeMAPE'] = abs(prediction['flowPrediction']['meanWinnerTimeSeconds']-winner_time)/winner_time
        early = lap.get('first600mSeconds'); estimate = prediction.get('flowPrediction',{}).get('meanFirst600Seconds')
        if isinstance(early,(int,float)) and isinstance(estimate,(int,float)) and 20 <= early <= 80:
            out['first600MAPE'] = abs(estimate-early)/early
    tail_errors = []
    for k,h in actual.items():
        tail, estimate = h.get('last3F'), rows[k].get('averageLast600Seconds')
        if isinstance(tail,(int,float)) and isinstance(estimate,(int,float)) and 20 <= tail <= 60:
            tail_errors.append(abs(estimate-tail)/tail)
    if tail_errors: out['last600MAPE'] = statistics.mean(tail_errors)
    weights = {'winnerBrier': .50, 'normalizedRankMAE': .25, 'winnerTimeMAPE': .10, 'first600MAPE': .10, 'last600MAPE': .05}
    used = [k for k in weights if out[k] is not None]
    if any(not math.isfinite(out[k]) for k in used): raise ValueError('nonfinite metric')
    out['loss'] = sum(weights[k]*out[k] for k in used)/sum(weights[k] for k in used)
    return out


def evaluate_record(record, result, now):
    match_result(record, result, now)
    scores = {model_id: metrics(f['prediction'], result) for model_id,f in record['forecasts'].items()}
    return {'schemaVersion': 1, 'objectiveVersion': OBJECTIVE, 'raceID': record['raceID'],
        'raceDate': record['raceDate'], 'recordSHA256': record['recordSHA256'], 'resultSHA256': digest(result),
        'scores': scores, 'actualPassingOrders': {str(h['number']):h.get('passingOrder') for h in result['horses']},
        'passingOrderComparison': 'observations only; no corner-to-distance mapping is assumed'}


def load_settled(root, now, commit_resolver=None):
    root = Path(root); records = []; held = []
    paths = sorted((root/'reports').glob('*/race_simulator_v02/*.json'))
    results = {}
    def collect(rows):
        for row in rows:
            key = (row.get('raceID'), row.get('raceDate'))
            # Both the rotating cache and daily archive can be present. A stale
            # daily file must not replace a newer official correction.
            def priority(r):
                value = str(r.get('resultUpdatedAtJST') or '')
                try:
                    t = datetime.fromisoformat(value)
                    if t.tzinfo is None: t = t.replace(tzinfo=JST)
                    updated = t.timestamp()
                except ValueError: updated = float('-inf')
                complete = sum(isinstance(h.get('finishPosition'), int) for h in r.get('horses', []))
                detail = sum(h.get('last3F') is not None for h in r.get('horses', [])) + bool(r.get('raceLap'))
                return updated, complete, detail, digest(r)
            if key not in results or priority(row) > priority(results[key]): results[key] = row
    collect(read_json(root/'ResultData.json', []))
    for day in {p.parts[-3] for p in paths}:
        collect(read_json(root/'reports'/day/'ResultData.json', []))
    for path in paths:
        try:
            record = read_json(path)
            committed = commit_resolver(path) if commit_resolver else first_committed_at(root, path)
            validate_record(record, committed)
            if path != journal_path(root, record): raise ValueError('journal path identity mismatch')
            result = results.get((record['raceID'],record['raceDate']))
            if result is None: raise ValueError('awaiting result')
            evaluation = evaluate_record(record, result, now)
            evaluation_key = digest({'objectiveVersion':OBJECTIVE,'resultSHA256':evaluation['resultSHA256']})
            dest = root/'reports'/record['raceDate'].replace('-','')/'race_simulator_evaluation'/record['raceID']/(evaluation_key+'.json')
            write_json(dest, evaluation, immutable=True)
            record.pop('displaySimulation', None)
            records.append({'record':record,'result':result,'evaluation':evaluation})
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
            held.append({'file':str(path.relative_to(root)), 'reason':str(exc)})
    return sorted(records,key=lambda x:(x['record']['scheduledPost'],x['record']['raceID'])), held


def fit_candidate(records, current, now, policy=Policy()):
    records = records[-policy.training_window:]
    if len(records) < policy.min_training_races or len({x['record']['raceDate'] for x in records}) < policy.min_training_days:
        return None
    cutoff = max(timestamp(x['record']['scheduledPost']) for x in records)
    if cutoff >= now: raise ValueError('unsettled training chronology')
    ratios=[]
    for x in records:
        prediction=x['record']['forecasts'][x['record']['activeModelID']]['prediction']
        lap=x['result'].get('raceLap') or {}; actual=lap.get('winnerTimeSeconds')
        if x['evaluation']['scores'][x['record']['activeModelID']]['winnerTimeMAPE'] is not None and actual:
            scale=x['record']['forecasts'][x['record']['activeModelID']]['model']['settings']['speedScale']
            ratios.append(scale*prediction['flowPrediction']['meanWinnerTimeSeconds']/actual)
    speed=max(.90,min(1.10,statistics.median(ratios))) if ratios else current['settings']['speedScale']
    configurations=[dict(current['settings'])]
    configurations.extend({'speedScale':speed,'strengthScale':strength,'styleScale':style}
                          for strength in (.75,1.,1.25) for style in (.8,1.,1.2))
    trials=[]
    for config in configurations:
        # A temporary model with a valid immutable identity, using training races only.
        model={'schemaVersion':1,'settings':settings(config),'engineSHA256':engine_digest(),
               'createdAt':stamp(now),'trainingCutoff':stamp(cutoff),'trainingRaceIDs':[x['record']['raceID'] for x in records],
               'baselineModelID':current['modelID'],'objectiveVersion':OBJECTIVE}
        model['modelID']='rs-'+digest(model)[:24]
        losses=[]
        for x in records:
            p=predict(x['record']['motion'],model,policy.training_runs,x['record']['seed'])
            losses.append(metrics(p,x['result'])['loss'])
        trials.append((statistics.mean(losses), model))
    score, model=min(trials,key=lambda x:(x[0],digest(x[1]['settings'])))
    if model['settings'] == current['settings'] or score >= trials[0][0]-1e-12:
        return None  # Do not consume a new holdout cohort for an unchanged setting.
    model.pop('modelID'); model['trainingLoss']=score
    model['trainingDataSHA256']=digest([(x['record']['recordSHA256'],x['evaluation']['resultSHA256']) for x in records])
    model['trainingRuns']=policy.training_runs
    model['modelID']='rs-'+digest(model)[:24]
    return validate_model(model)


def promotion_decision(records, candidate, current, policy=Policy()):
    eligible=[]; training=set(candidate['trainingRaceIDs'])
    if candidate['baselineModelID'] != current['modelID']: raise ValueError('challenger baseline is stale')
    for x in records:
        r=x['record']; scores=x['evaluation']['scores']
        if r['raceID'] in training: continue
        if timestamp(r['recordedAt']) <= timestamp(candidate['createdAt']): continue
        if r['activeModelID'] != current['modelID'] or r.get('candidateModelID') != candidate['modelID']: continue
        if current['modelID'] not in scores or candidate['modelID'] not in scores: continue
        if r['forecasts'][candidate['modelID']]['model'] != candidate: raise ValueError('challenger content changed')
        eligible.append(x)
        if len(eligible) >= policy.min_validation_races and len({z['record']['raceDate'] for z in eligible}) >= policy.min_validation_days:
            break  # One fixed first eligible cohort. Never repeatedly peek at later windows.
    info={'status':'awaiting_validation','validationRaces':len(eligible),'validationDays':len({x['record']['raceDate'] for x in eligible}),
          'candidateModelID':candidate['modelID'],'baselineModelID':current['modelID']}
    if len(eligible) < policy.min_validation_races or info['validationDays'] < policy.min_validation_days: return info
    old=[x['evaluation']['scores'][current['modelID']] for x in eligible]
    new=[x['evaluation']['scores'][candidate['modelID']] for x in eligible]
    differences=[a['loss']-b['loss'] for a,b in zip(old,new)]
    rng=random.Random(digest([x['record']['recordSHA256'] for x in eligible]))
    bootstrap=sorted(statistics.mean(rng.choices(differences,k=len(differences))) for _ in range(policy.bootstrap_draws))
    lower=bootstrap[int(.05*(len(bootstrap)-1))]
    before=statistics.mean(x['loss'] for x in old); after=statistics.mean(x['loss'] for x in new)
    improvement=(before-after)/max(before,1e-12)
    clock_coverage=statistics.mean(x['winnerTimeMAPE'] is not None and x['first600MAPE'] is not None for x in old)
    reasons=[]
    if improvement < policy.min_relative_improvement: reasons.append('insufficient_relative_improvement')
    if lower <= 0: reasons.append('paired_bootstrap_not_positive')
    if clock_coverage < policy.min_clock_coverage: reasons.append('insufficient_sectional_coverage')
    for key in ('winnerBrier','normalizedRankMAE'):
        if statistics.mean(x[key] for x in new) > statistics.mean(x[key] for x in old)+1e-9: reasons.append(key+'_regressed')
    for metric, reason in (('winnerTimeMAPE','finish_clock_regressed'),
                           ('first600MAPE','early_section_regressed'),('last600MAPE','late_section_regressed')):
        pairs=[(a[metric],b[metric]) for a,b in zip(old,new) if a[metric] is not None]
        if any(b is None for _,b in pairs): reasons.append(metric+'_missing')
        elif pairs and statistics.mean(b-a for a,b in pairs) > .002: reasons.append(reason)
    if statistics.mean(differences[len(differences)//2:]) < 0: reasons.append('recent_half_regressed')
    info.update(status='promote' if not reasons else 'reject',reasons=reasons,
        validationRaceIDs=[x['record']['raceID'] for x in eligible],beforeLoss=before,afterLoss=after,
        relativeImprovement=improvement,paired95LowerMeanImprovement=lower,clockCoverage=clock_coverage)
    return info


def cycle(root=ROOT, now=None, policy=Policy(), commit_resolver=None):
    root=Path(root);now=now or datetime.now(timezone.utc)
    records, held=load_settled(root,now,commit_resolver)
    state=load_registry(root); current=load_model(root,state['activeModelID'])
    decision=None
    if state.get('candidateModelID'):
        candidate=load_model(root,state['candidateModelID'])
        decision=promotion_decision(records,candidate,current,policy)
        if decision['status'] != 'awaiting_validation':
            event={**decision,'decidedAt':stamp(now)}
            event['decisionSHA256']=digest(event)
            write_json(root/'research/race_simulator/decisions'/(event['decisionSHA256']+'.json'),event,immutable=True)
            state['decisions'].append(event);state['candidateModelID']=None
            if decision['status']=='promote': state['activeModelID']=candidate['modelID'];current=candidate
    # A candidate's holdout races never influence its fit. A later generation can
    # learn from them only after that generation's irrevocable decision.
    training_hash=digest([(x['record']['recordSHA256'],x['evaluation']['resultSHA256']) for x in records[-policy.training_window:]])
    if not state.get('candidateModelID') and training_hash != state.get('lastTrainingDataSHA256'):
        candidate=fit_candidate(records,current,now,policy)
        state['lastTrainingDataSHA256']=training_hash
        if candidate:
            write_json(root/'research/race_simulator/models'/(candidate['modelID']+'.json'),candidate,immutable=True)
            state.update(candidateModelID=candidate['modelID'],lastTrainingDataSHA256=training_hash,
                         generation=state.get('generation',0)+1)
    state['lastCycleAt']=stamp(now)
    write_json(root/'research/race_simulator/state.json',state)
    display=read_json(root/'RaceSimulationData.json',{})
    report={'schemaVersion':1,'generatedAt':stamp(now),'objectiveVersion':OBJECTIVE,
        'status':('candidate_in_forward_validation' if state.get('candidateModelID') else
                  'validated_model_active' if state['activeModelID'] != BASELINE_ID else 'awaiting_forward_training_data'),
        'activeModelID':state['activeModelID'],'candidateModelID':state.get('candidateModelID'),
        'settledProspectiveRaces':len(records),'heldRecords':held,'policy':asdict(policy),'latestDecision':decision,
        'retrospectiveDisplayRacesExcluded':sum(not r.get('prospectiveConfirmed',False) for r in display.get('races',[])),
        'formalPredictionAIChanged':False,'automaticBettingEnabled':False,
        'generation':state.get('generation',0),'decisionHistory':state['decisions'],
        'meaning':'Only durably committed pre-post forecasts count. Retrospective simulations are excluded. Lower held-out loss is better.'}
    write_json(root/'RaceSimulatorLearning.json',report)
    write_markdown(root,report)
    return report


def write_markdown(root,report):
    p=report['policy'];d=report.get('latestDecision') or {}
    status={'awaiting_forward_training_data':'発走前記録の蓄積待ち',
            'candidate_in_forward_validation':'別レースで候補を検証中',
            'validated_model_active':'別レースで検証した設定を採用中'}[report['status']]
    decision={'promote':'採用','reject':'見送り','awaiting_validation':'検証レースの蓄積待ち'}.get(d.get('status'),'データ待ち')
    text=f'''# RaceSimulator 継続改善の状況

- 状態: {status}
- 採用中モデル: {report['activeModelID']}
- 検証中モデル: {report['candidateModelID'] or 'なし'}
- 結果照合できた発走前記録: {report['settledProspectiveRaces']}レース
- 保留記録: {len(report['heldRecords'])}件
- 学習開始: {p['min_training_races']}レース・{p['min_training_days']}開催日以上
- 採用判定: 学習に使っていない、その後の{p['min_validation_races']}レース・{p['min_validation_days']}開催日以上
- 採用条件: 損失{p['min_relative_improvement']:.0%}以上改善、対応のあるブートストラップ下限が正、勝率・順位の悪化なし、ラップ情報の充足・大きな悪化なし
- 直近の判定: {decision}
- 実績に含めないレース後の表示用シミュレーション: {report['retrospectiveDisplayRacesExcluded']}レース

発走前に保存・コミットできた予測だけを実績として数えます。レース後の再生成は含めません。
通過順は実測値を保存しますが、コーナーと距離の対応がない箇所を25%地点などと同一視しません。
予測AIの馬券候補・勝率には変更を加えず、展開シミュレーターの設定だけを検証・更新します。
この検証は予測精度や回収率の改善を保証するものではありません。
'''
    path=Path(root)/'reports/RaceSimulatorLearning.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=['capture','cycle'],default='capture')
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    if args.command=='capture': result=capture_upcoming(args.root)
    else: result=cycle(args.root)
    print(json.dumps({k:v for k,v in result.items() if k not in {'heldRecords','policy'}},ensure_ascii=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
