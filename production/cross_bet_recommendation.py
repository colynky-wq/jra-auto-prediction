"""Research-only cross-bet selector built from immutable saved odds records.

This layer never changes the frozen per-bet experiments, never sizes stakes and
never enables betting.  It compares the saved candidates on one conservative
scale: AI hit probability x the LOWER saved decimal odds.  WIN5 stays separate
because there is no ordinary per-combination odds snapshot in this research
pipeline.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import all_bet_baseline as single
import all_bet_forward as added
import research_triple_baseline as triple_model
import triple_forward as triple
from live_bundle_publisher import atomic

SCHEMA_VERSION = 1
BREAK_EVEN = 1.0
TOP_ALTERNATIVES = 5
BET_LABELS = {
    'win': '単勝',
    'place': '複勝',
    'frame_quinella': '枠連',
    'quinella': '馬連',
    'wide': 'ワイド',
    'exacta': '馬単',
    'trio': '3連複',
    'trifecta': '3連単',
    'support': '応援馬券',
}
BET_TYPES = tuple(BET_LABELS)
USABLE_STATES = {'ready', 'awaiting_results', 'payouts_pending', 'matched'}
EVALUATED_STATES = USABLE_STATES | {'no_candidate'}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite number required')
    return float(value)


def _decoded(key):
    if not isinstance(key, str) or not key or len(key) % 2:
        raise ValueError('invalid outcome key')
    return [int(key[i:i + 2]) for i in range(0, len(key), 2)]


def _row(kind, key, label, probability, market, low, high, **extra):
    probability = _finite(probability)
    low, high = _finite(low), _finite(high)
    if not 0 <= probability <= 1 or not 1 <= low <= high:
        raise ValueError('invalid candidate probability/odds')
    if market is not None:
        market = _finite(market)
        if not 0 <= market <= 1:
            raise ValueError('invalid market probability')
    value = {
        'betType': kind,
        'betTypeLabel': BET_LABELS[kind],
        'id': key,
        'label': label,
        'probabilityPct': round(probability * 100, 6),
        'marketProbabilityPct': round(market * 100, 6) if market is not None else None,
        'oddsLow': round(low, 6),
        'oddsHigh': round(high, 6),
        'expectedMultiplierLow': round(probability * low, 6),
        'expectedMultiplierHigh': round(probability * high, 6),
    }
    value.update(extra)
    return value


def _labels(record, kind, key):
    horses = record['prediction']['horses']
    names = {int(h['number']): str(h['name']) for h in horses}
    numbers = _decoded(key)
    if kind == 'frame_quinella':
        frames = single.frame_map(record['entryEvidence'], horses)
        parts = []
        for frame in numbers:
            members = '・'.join(names[n] for n in sorted(frames) if frames[n] == frame)
            parts.append(f'{frame}枠（{members}）')
    else:
        parts = [f'{n}番 {names[n]}' for n in numbers]
    return (' → ' if kind in {'exacta', 'trifecta'} else ' − ').join(parts)


def _ordinary_candidates(record, kind):
    record = added.validate(record)
    if record['status'] != 'ready':
        return []
    race = record['prediction']
    ai = single.probabilities(race['horses'], 'aiProbability', kind, record.get('entryEvidence'))
    market = single.probabilities(race['horses'], 'marketProbability', kind, record.get('entryEvidence'))
    out = []
    for key, probability in ai.items():
        odds = record['odds'][key]
        out.append(_row(kind, key, _labels(record, kind, key), probability, market[key],
                        odds['low'], odds['high'], sourceScope='all_saved_odds_outcomes'))
    return out


def _triple_candidates(record, kind):
    record = triple.validate(record)
    if record['status'] != 'ready':
        return []
    race = record['prediction']
    index = 1 if kind == 'trio' else 0
    ai_distribution = triple_model.distributions(race['horses'], 'aiProbability')[index]
    market_distribution = triple_model.distributions(race['horses'], 'marketProbability')[index]
    ai = {single.outcome_key(numbers): probability for numbers, probability in ai_distribution.items()}
    market = {single.outcome_key(numbers): probability for numbers, probability in market_distribution.items()}
    out = []
    for key, probability in ai.items():
        odds = record['odds'][key]
        out.append(_row(kind, key, _labels(record, kind, key), probability, market[key], odds, odds,
                        sourceScope='all_saved_odds_outcomes'))
    return out


def _support_candidates(win_record, place_record):
    win_record = added.validate(win_record)
    place_record = added.validate(place_record)
    if win_record['status'] != 'ready' or place_record['status'] != 'ready':
        return []
    race = win_record['prediction']
    if added.digest(race) != added.digest(place_record['prediction']):
        raise ValueError('support prediction mismatch')
    wa = single.probabilities(race['horses'], 'aiProbability', 'win')
    wm = single.probabilities(race['horses'], 'marketProbability', 'win')
    pa = single.probabilities(race['horses'], 'aiProbability', 'place', place_record['entryEvidence'])
    pm = single.probabilities(race['horses'], 'marketProbability', 'place', place_record['entryEvidence'])
    names = {f"{int(h['number']):02}": str(h['name']) for h in race['horses']}
    out = []
    for key in sorted(pa):
        w_odds, p_odds = win_record['odds'][key], place_record['odds'][key]
        low = (wa[key] * w_odds['low'] + pa[key] * p_odds['low']) / 2
        high = (wa[key] * w_odds['high'] + pa[key] * p_odds['high']) / 2
        # A support ticket is two equal 100-yen legs, so its expected multiplier
        # is the arithmetic mean of the Win and Place expected multipliers.  It
        # does not have one single hit probability; Place probability is shown
        # only as the familiar display probability.
        value = {
            'betType': 'support',
            'betTypeLabel': BET_LABELS['support'],
            'id': key,
            'label': f"{int(key)}番 {names[key]}（単勝100円＋複勝100円）",
            'probabilityPct': round(pa[key] * 100, 6),
            'marketProbabilityPct': round(pm[key] * 100, 6),
            'winProbabilityPct': round(wa[key] * 100, 6),
            'oddsLow': None,
            'oddsHigh': None,
            'winOddsLow': round(w_odds['low'], 6),
            'winOddsHigh': round(w_odds['high'], 6),
            'placeOddsLow': round(p_odds['low'], 6),
            'placeOddsHigh': round(p_odds['high'], 6),
            'expectedMultiplierLow': round(low, 6),
            'expectedMultiplierHigh': round(high, 6),
            'sourceScope': 'all_saved_win_place_packages',
        }
        out.append(value)
    return out


def _wide_candidates(row):
    out = []
    for candidate in row.get('candidates', []):
        low, high = candidate.get('oddsLow'), candidate.get('oddsHigh')
        probability = candidate.get('probabilityPct')
        if low is None or high is None or probability is None:
            continue
        value = _row('wide', str(candidate['id']), str(candidate['label']),
                     _finite(probability) / 100, None, low, high,
                     sourceScope='frozen_wide_rule_candidates')
        out.append(value)
    return out


def rank_candidates(candidates):
    """Rank on conservative lower-bound expected multiplier only."""
    valid = []
    seen = set()
    for candidate in candidates:
        if candidate.get('betType') not in BET_LABELS:
            continue
        key = (candidate['betType'], str(candidate.get('id', '')))
        if key in seen:
            raise ValueError('duplicate cross-bet candidate')
        seen.add(key)
        low = _finite(candidate['expectedMultiplierLow'])
        high = _finite(candidate['expectedMultiplierHigh'])
        if low < 0 or high < low:
            raise ValueError('invalid expected multiplier')
        probability = candidate.get('probabilityPct')
        probability = -1.0 if probability is None else _finite(probability)
        valid.append((low, high, probability, candidate))
    valid.sort(key=lambda item: (-item[0], -item[1], -item[2],
                                 item[3]['betType'], str(item[3]['id'])))
    return [item[3] for item in valid]


def build_recommendation(race, candidates, states):
    ranked = rank_candidates(candidates)
    evaluated = [kind for kind in BET_TYPES if states.get(kind) in EVALUATED_STATES]
    usable = [kind for kind in BET_TYPES if any(c['betType'] == kind for c in ranked)]
    unavailable = [kind for kind in BET_TYPES if kind not in usable]
    base = {
        'schemaVersion': SCHEMA_VERSION,
        'raceDate': race.get('raceDate'),
        'raceID': race.get('raceID'),
        'venue': race.get('venue', ''),
        'raceNumber': race.get('raceNumber'),
        'startTime': race.get('startTime'),
        'raceName': race.get('raceName', ''),
        'decision': 'NO_ODDS',
        'decisionLabel': '実オッズ付き候補不足',
        'breakEvenExpectedMultiplier': BREAK_EVEN,
        'recommendedBetType': None,
        'recommendedBetTypeLabel': None,
        'candidate': None,
        'bestObserved': ranked[0] if ranked else None,
        'topAlternatives': ranked[:TOP_ALTERNATIVES],
        'evaluatedBetTypes': evaluated,
        'usableOddsBetTypes': usable,
        'unavailableBetTypes': unavailable,
        'coverageComplete': len(evaluated) == len(BET_TYPES),
        'formalRecommendation': False,
        'autoBetEnabled': False,
        'stakeSizingEnabled': False,
        'win5Excluded': True,
    }
    if not ranked:
        base['reason'] = '比較できる保存済み実オッズがないため、横断候補を出しません。'
        return base
    best = ranked[0]
    if _finite(best['expectedMultiplierLow']) < BREAK_EVEN:
        base.update(decision='PASS', decisionLabel='見送り',
                    reason='実オッズ下限で計算した全候補の期待倍率が1.00未満のため見送ります。')
        return base
    base.update(
        decision='RESEARCH_CANDIDATE',
        decisionLabel='研究上の第一候補',
        recommendedBetType=best['betType'],
        recommendedBetTypeLabel=best['betTypeLabel'],
        candidate=best,
        reason='保存済み実オッズの下限を使った期待倍率が最も高い候補です。利益を保証する正式推奨ではありません。',
    )
    return base


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _race_candidate_pool(root, day, rid, rows):
    state_by_kind = {row['betType']: row.get('status') for row in rows if row.get('betType') != 'win5'}
    candidates = []
    folder = Path(root) / 'reports' / day.replace('-', '')

    for kind in single.TYPES:
        if state_by_kind.get(kind) not in USABLE_STATES:
            continue
        path = folder / 'all_bet_v1' / kind / f'{rid}.json'
        if path.exists():
            candidates.extend(_ordinary_candidates(_load(path), kind))

    for kind in triple.TYPES:
        if state_by_kind.get(kind) not in USABLE_STATES:
            continue
        path = folder / 'triple_v1' / kind / f'{rid}.json'
        if path.exists():
            candidates.extend(_triple_candidates(_load(path), kind))

    if state_by_kind.get('support') in USABLE_STATES:
        win_path = folder / 'all_bet_v1' / 'win' / f'{rid}.json'
        place_path = folder / 'all_bet_v1' / 'place' / f'{rid}.json'
        if win_path.exists() and place_path.exists():
            candidates.extend(_support_candidates(_load(win_path), _load(place_path)))

    wide_rows = [row for row in rows if row.get('betType') == 'wide']
    if len(wide_rows) > 1:
        raise ValueError('duplicate Wide projection row')
    if wide_rows and wide_rows[0].get('status') in USABLE_STATES:
        candidates.extend(_wide_candidates(wide_rows[0]))
    return candidates, state_by_kind


def enrich(payload, root):
    if not isinstance(payload, dict) or not isinstance(payload.get('races'), list):
        raise ValueError('BetResearchData payload required')
    by_race = {}
    for row in payload['races']:
        if row.get('betType') == 'win5' or str(row.get('raceID', '')).startswith('WIN5-'):
            continue
        key = (row.get('raceDate'), row.get('raceID'))
        by_race.setdefault(key, []).append(row)

    recommendations = []
    for (day, rid), rows in sorted(by_race.items(), key=lambda item: (
            item[0][0] or '', min((r.get('startTime') or '' for r in item[1]), default=''), item[0][1] or '')):
        if not day or not rid:
            continue
        race = max(rows, key=lambda row: sum(row.get(k) not in (None, '') for k in
                                             ('venue', 'raceNumber', 'startTime', 'raceName')))
        candidates, states = _race_candidate_pool(root, day, rid, rows)
        recommendations.append(build_recommendation(race, candidates, states))

    payload = dict(payload)
    payload['crossBetSchemaVersion'] = SCHEMA_VERSION
    payload['crossBetBreakEvenExpectedMultiplier'] = BREAK_EVEN
    payload['crossBetRecommendations'] = recommendations
    payload['crossBetMeaning'] = (
        '全券種を保存済み実オッズ下限×AI確率の期待倍率で横断比較する研究表示です。'
        '単勝・複勝・枠連・馬連・ワイド・馬単・3連複・3連単・応援馬券を対象とし、WIN5は別枠です。'
        '正式な購入推奨・自動投票・賭け金配分ではありません。'
    )
    return payload


def enrich_file(path, root):
    path = Path(path)
    value = enrich(_load(path), root)
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())
    return value


def main():
    root = Path(__file__).resolve().parent.parent
    paths = sorted((root / 'reports').glob('*/BetResearchData.json'))
    if (root / 'BetResearchData.json').exists():
        paths.append(root / 'BetResearchData.json')
    total = 0
    decisions = {'RESEARCH_CANDIDATE': 0, 'PASS': 0, 'NO_ODDS': 0}
    for path in paths:
        value = enrich_file(path, root)
        rows = value['crossBetRecommendations']
        total += len(rows)
        for row in rows:
            decisions[row['decision']] += 1
    print('CROSS_BET_RESEARCH', 'files=', len(paths), 'races=', total, 'decisions=', decisions)


if __name__ == '__main__':
    main()
