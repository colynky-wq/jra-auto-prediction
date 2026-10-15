"""Strict research-only first capture and payout join. No stakes or app signals."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from itertools import combinations, permutations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

import research_triple_baseline as baseline

JST = timezone(timedelta(hours=9))
START = datetime.fromisoformat('2026-10-15T16:57:59+09:00')
VERSION = 'triple-forward-v1-20261015'
TYPES = {'trio': 7, 'trifecta': 8}
LABELS = {'trio': '3連複', 'trifecta': '3連単'}
BASELINE_HASH = hashlib.sha256(Path(baseline.__file__).read_bytes()).hexdigest()


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp required')
    out = datetime.fromisoformat(value)
    return out.replace(tzinfo=JST) if out.tzinfo is None else out.astimezone(JST)


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def outcome_key(numbers):
    return ''.join(f'{n:02}' for n in numbers)


def expected_keys(kind, numbers):
    function = combinations if kind == 'trio' else permutations
    if kind not in TYPES:
        raise ValueError('unsupported bet type')
    return {outcome_key(t) for t in function(sorted(numbers), 3)}


def odds_map(payload, kind, numbers):
    """Require every combination, with no silent drop of unavailable entries."""
    if not isinstance(payload, dict) or payload.get('status') != 'middle':
        raise ValueError('pre-race odds status required')
    raw = payload['data']['odds'][str(TYPES[kind])]
    return parse_table(raw, kind, numbers)


def parse_table(raw, kind, numbers):
    if not isinstance(raw, dict) or set(raw) != expected_keys(kind, numbers):
        raise ValueError('complete exact entrant coverage required')
    out = {}
    for key, item in raw.items():
        if not isinstance(item, list) or not item or isinstance(item[0], bool):
            raise ValueError('invalid odds row')
        value = float(str(item[0]).replace(',', ''))
        if not math.isfinite(value) or value < 1:
            raise ValueError('finite decimal odds >= 1 required')
        out[key] = value
    return out


def request_bytes(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache',
                  'Referer': 'https://race.netkeiba.com/'})
    with urlopen(req, timeout=6) as stream:
        data = stream.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('source response too large')
    return data


def source_url(rid, kind):
    return (f'https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}'
            f'&type={TYPES[kind]}&action=init&compress=0&output=json')


def fetch_odds(rid, kind):
    return json.loads(request_bytes(source_url(rid,kind)))


def record_path(root, race, kind):
    day, rid, _ = baseline.race_key(race)
    if kind not in TYPES:
        raise ValueError('unsupported bet type')
    return root/'reports'/day.replace('-', '')/'triple_v1'/kind/(rid+'.json')


def validate(record):
    value = dict(record)
    fingerprint = value.pop('recordFingerprint')
    if digest(value) != fingerprint or record['schemaVersion'] != 1 or record['version'] != VERSION:
        raise ValueError('record fingerprint/version mismatch')
    race, kind = record['prediction'], record['betType']
    if kind not in TYPES:
        raise ValueError('unknown frozen bet type')
    day, rid, time = baseline.race_key(race)
    numbers = baseline.roster(race['horses'])
    post = stamp(day+'T'+time)
    predicted, began, received, recorded = (stamp(record[k]) for k in
        ('predictionAvailableAtJST', 'requestStartedAtJST', 'receivedAtJST', 'recordedAtJST'))
    if not START <= predicted <= began <= received <= recorded < post:
        raise ValueError('record time sequence is invalid')
    if predicted.date().isoformat() != day or recorded.date().isoformat() != day:
        raise ValueError('record day mismatch')
    if record['baselineVersion'] != baseline.METHOD or record['baselineHash'] != BASELINE_HASH:
        raise ValueError('frozen baseline implementation unavailable')
    if record['status'] not in {'ready', 'odds_unavailable'}:
        raise ValueError('unknown capture status')
    if record['status'] == 'ready':
        if (record.get('apiStatus') != 'middle' or record.get('apiType') != TYPES[kind]
                or record.get('sourceURL') != source_url(rid,kind)
                or not re.fullmatch('[a-f0-9]{64}', record.get('sourceFingerprint',''))):
            raise ValueError('pre-race source evidence mismatch')
        odds = record['odds']
        if set(odds) != expected_keys(kind, numbers):
            raise ValueError('frozen odds coverage mismatch')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 1
               for x in odds.values()):
            raise ValueError('invalid frozen odds')
        official = stamp(record['officialDatetime'])
        if official.date().isoformat() != day or not predicted.replace(hour=0, minute=0, second=0, microsecond=0) <= official <= received < post:
            raise ValueError('official source time mismatch')
    elif record.get('odds'):
        raise ValueError('held record must not contain usable odds')
    return record


def record_first(root, race, kind, prediction_available, *, fetch=fetch_odds,
                 clock=lambda: datetime.now(JST)):
    """First attempt (including a hold) is immutable; never fetch after post."""
    root = Path(root)
    path = record_path(root, race, kind)
    if path.exists():
        old = validate(json.loads(path.read_text()))
        if old['prediction']['raceID'] != race['raceID'] or old['betType'] != kind:
            raise ValueError('existing record identity mismatch')
        return path
    day, rid, time = baseline.race_key(race)
    numbers = baseline.roster(race['horses'])
    predicted = stamp(prediction_available)
    now, post = clock().astimezone(JST), stamp(day+'T'+time)
    if not START <= predicted <= now < post or now.date().isoformat() != day:
        return None
    # Validate both inputs before making a network request.
    for key in ('aiProbability', 'marketProbability'):
        baseline.distributions(race['horses'], key)
    record = {'schemaVersion': 1, 'version': VERSION, 'baselineVersion': baseline.METHOD,
              'baselineHash': BASELINE_HASH, 'betType': kind,
              'prediction': json.loads(json.dumps(race, allow_nan=False)),
              'predictionAvailableAtJST': predicted.isoformat(),
              'requestStartedAtJST': now.isoformat(), 'status': 'odds_unavailable', 'odds': {}}
    try:
        raw = fetch(rid, kind)
        record.update(odds=odds_map(raw, kind, numbers),
                      officialDatetime=raw['data']['official_datetime'],
                      apiStatus=raw.get('status'), apiType=TYPES[kind], sourceURL=source_url(rid,kind),
                      sourceFingerprint=digest(raw), status='ready')
    except (OSError, ValueError, KeyError, TypeError) as error:
        record.update(status='odds_unavailable', odds={}, reason=type(error).__name__)
    record['receivedAtJST'] = clock().astimezone(JST).isoformat()
    record['recordedAtJST'] = clock().astimezone(JST).isoformat()
    if stamp(record['recordedAtJST']) >= post:
        return None
    # Bad source timestamps are a saved hold, never a successful observation.
    record['recordFingerprint'] = digest(record)
    try:
        validate(record)
    except ValueError:
        record.update(status='odds_unavailable', odds={}, reason='source_time_or_schema_invalid')
        record.pop('recordFingerprint')
        record['recordFingerprint'] = digest(record)
        validate(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write((json.dumps(record, ensure_ascii=False, allow_nan=False)+'\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if clock().astimezone(JST) >= post:
            return None
        try:
            os.link(temporary, path)
        except FileExistsError:
            validate(json.loads(path.read_text()))
    finally:
        temporary.unlink(missing_ok=True)
    return path


def payout_map(html, rid, kind):
    """Read official payout amounts per 100 yen; preserve all winning tickets."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    identities = [t.get('href') or t.get('content') or '' for t in
                  soup.select('link[rel="canonical"],meta[property="og:url"]')]
    if not any(re.search(r'race_id='+rid+r'(?:[&#]|$)', u) for u in identities):
        raise ValueError('payout document race identity unavailable')
    found = []
    for row in soup.select('tr'):
        title = row.find('th')
        if title and title.get_text(strip=True) == LABELS[kind]:
            found.append(row)
    if len(found) != 1:
        raise ValueError('exactly one matching payout row required')
    cells = found[0].find_all('td', recursive=False)
    if len(cells) < 2:
        raise ValueError('payout cells missing')
    numbers = [int(x) for x in re.findall(r'\d+', cells[0].get_text(' ', strip=True))]
    amounts = [int(x.replace(',', '')) for x in re.findall(r'([0-9][0-9,]*)\s*円', cells[1].get_text(' ', strip=True))]
    if len(numbers) != 3*len(amounts) or not amounts:
        raise ValueError('payout group count mismatch')
    out = {}
    for offset, yen in enumerate(amounts):
        triple = numbers[3*offset:3*offset+3]
        if len(set(triple)) != 3 or any(not 1 <= n <= 18 for n in triple) or yen <= 0:
            raise ValueError('invalid winning outcome')
        key = outcome_key(sorted(triple) if kind == 'trio' else triple)
        if key in out:
            raise ValueError('duplicate payout')
        out[key] = yen
    return out


def evaluate(record, result, payouts):
    """Return full-outcome research rows, never selected stakes or a portfolio ROI."""
    validate(record)
    if record['status'] != 'ready':
        return {'status': 'held', 'rows': []}
    state, finish = baseline.result_state(record['prediction'], result)
    if state != 'matched_retrospective':
        return {'status': state, 'rows': []}
    if payouts is None:
        return {'status': 'payouts_pending', 'rows': []}
    kind = record['betType']
    truth = outcome_key(sorted(finish) if kind == 'trio' else finish)
    if set(payouts) != {truth} or type(payouts[truth]) is not int or payouts[truth] <= 0:
        raise ValueError('independent result and payout outcomes disagree')
    ai, market = (baseline.distributions(record['prediction']['horses'], key)
                  for key in ('aiProbability', 'marketProbability'))
    index = 1 if kind == 'trio' else 0
    ai, market = ({outcome_key(k): v for k, v in model[index].items()} for model in (ai, market))
    rows = [{'outcome': k, 'aiProbability': ai[k], 'marketProbability': market[k],
             'savedOdds': odds, 'expectedMultiplier': ai[k]*odds,
             'hit': k == truth, 'payoutPer100Yen': payouts.get(k, 0)}
            for k, odds in sorted(record['odds'].items())]
    return {'status': 'matched', 'betType': kind, 'recordFingerprint': record['recordFingerprint'],
            'rows': rows, 'selectedBets': 0, 'returnIndex': None, 'autoBetEnabled': False}
