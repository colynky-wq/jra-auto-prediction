"""Offline-only Trio/Trifecta baseline audit; never records or publishes predictions.

No training, network, odds substitution, stakes, or production runtime imports.
Plackett-Luce is a comparison assumption using existing win probabilities;
its implied Top3 probabilities are NOT the trained Top3 model's outputs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
from itertools import permutations
import json
import math
from pathlib import Path

METHOD = 'triple-pl-win-baseline-v1-20261015'
TERMINAL = {'中止', '失格'}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('finite numeric value required')
    return float(value)


def roster(horses):
    if not isinstance(horses, list) or not 3 <= len(horses) <= 18:
        raise ValueError('3 to 18 entrants required')
    out = {}
    for h in horses:
        n, name = h['number'], h['name']
        if type(n) is not int or not 1 <= n <= 18 or n in out:
            raise ValueError('unique integer horse numbers required')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('nonempty horse name required')
        out[n] = name
    return out


def distributions(horses, key):
    """All nP3 ordered outcomes and nC3 unordered outcomes, each summing to 1."""
    names = roster(horses)
    weights = {h['number']: finite(h[key]) for h in horses}
    if any(not 0 < w <= 100 for w in weights.values()):
        raise ValueError('strictly positive percent probabilities required')
    total = math.fsum(weights.values())
    if abs(total - 100) > .05:
        raise ValueError('win probabilities must sum to 100 percent within rounding tolerance')
    ordered, unordered = {}, defaultdict(float)
    for a, b, c in permutations(sorted(names), 3):
        after_a = math.fsum(w for n, w in weights.items() if n != a)
        after_ab = math.fsum(w for n, w in weights.items() if n not in (a, b))
        p = weights[a] / total * weights[b] / after_a * weights[c] / after_ab
        if not math.isfinite(p) or p <= 0:
            raise ValueError('ordered probability underflow or invalid value')
        ordered[a, b, c] = p
        unordered[tuple(sorted((a, b, c)))] += p
    if not math.isclose(math.fsum(ordered.values()), 1., abs_tol=1e-10):
        raise ValueError('invalid ordered probability mass')
    if not math.isclose(math.fsum(unordered.values()), 1., abs_tol=1e-10):
        raise ValueError('invalid unordered probability mass')
    return ordered, dict(unordered)


def race_key(race):
    day, rid, time = race['raceDate'], race['raceID'], race['startTime']
    datetime.strptime(day, '%Y-%m-%d')
    datetime.strptime(time, '%H:%M')
    if not isinstance(rid, str) or len(rid) != 12 or not rid.isdigit():
        raise ValueError('12-digit race ID required')
    return day, rid, time


def result_state(race, result):
    """Independent result identity; ties/changes never become ordinary losses."""
    if result is None:
        return 'awaiting_results', None
    if race_key(race) != race_key(result):
        return 'invalid', None
    names = roster(race['horses'])
    actual = roster(result['horses'])
    if type(result.get('fieldSize')) is not int or result['fieldSize'] != len(actual):
        return 'invalid', None
    updated = datetime.strptime(result['resultUpdatedAtJST'], '%Y-%m-%d %H:%M:%S')
    post = datetime.strptime(race['raceDate']+' '+race['startTime'], '%Y-%m-%d %H:%M')
    if updated < post:
        return 'invalid', None
    if names != actual or any(h.get('resultStatus') in {'取消', '除外'} for h in result['horses']):
        return 'entrant_changed', None
    positions = defaultdict(list)
    for h in result['horses']:
        p = h.get('finishPosition')
        if p is None and h.get('resultStatus') in TERMINAL:
            continue
        if p is None:
            return 'awaiting_results', None
        if h.get('resultStatus') != '確定':
            return 'invalid', None
        if type(p) is not int or not 1 <= p <= len(names):
            return 'invalid', None
        positions[p].append(h['number'])
    # This first comparison baseline supports only an unambiguous top-three.
    # Never infer a single winning ticket or a payout for a dead heat.
    if any(len(positions[p]) > 1 for p in (1, 2, 3)):
        return 'dead_heat_excluded', None
    if any(len(positions[p]) != 1 for p in (1, 2, 3)):
        return 'invalid', None
    return 'matched_retrospective', tuple(positions[p][0] for p in (1, 2, 3))


def load_rows(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, list):
        raise ValueError('race data root must be an array')
    return value


def index_unique(rows):
    index = {}
    for race in rows:
        key = race_key(race)
        if key[:2] in index:
            raise ValueError('duplicate race date/ID')
        index[key[:2]] = race
    return index


def audit(root):
    paths = [root/'RaceData.json', root/'ResultData.json']
    races, results = (load_rows(p) for p in paths)
    index_unique(races)
    result_index = index_unique(results)
    counts = {k: 0 for k in ('inputRaces', 'invalid', 'awaiting_results', 'entrant_changed',
                              'dead_heat_excluded', 'matched_retrospective')}
    metrics = {kind: {model: {'races': 0, 'topOneHits': 0, 'negativeLogProbabilitySum': 0.}
                      for model in ('ai', 'market')} for kind in ('trio', 'trifecta')}
    rows = []
    for race in races:
        counts['inputRaces'] += 1
        row = {'raceDate': race.get('raceDate'), 'raceID': race.get('raceID')}
        try:
            key = race_key(race)
            models = {model: distributions(race['horses'], probability)
                      for model, probability in [('ai', 'aiProbability'), ('market', 'marketProbability')]}
            state, finish = result_state(race, result_index.get(key[:2]))
            if finish:
                for model, (ordered, unordered) in models.items():
                    for kind, distribution, target in [('trio', unordered, tuple(sorted(finish))),
                                                       ('trifecta', ordered, finish)]:
                        metric = metrics[kind][model]
                        metric['races'] += 1
                        # Deterministic tie breaking, shared by both baselines.
                        best = min(distribution, key=lambda t: (-distribution[t], t))
                        metric['topOneHits'] += best == target
                        metric['negativeLogProbabilitySum'] -= math.log(distribution[target])
            row['status'] = state
            n = len(race['horses'])
            row.update(trioOutcomes=math.comb(n, 3), trifectaOutcomes=n*(n-1)*(n-2))
        except (ValueError, KeyError, TypeError, OverflowError) as e:
            state = 'invalid'
            row.update(status=state, reason=str(e))
        counts[state] += 1
        rows.append(row)
    for models in metrics.values():
        for metric in models.values():
            n = metric['races']
            metric['topOneHitRate'] = metric['topOneHits']/n if n else None
            metric['meanNegativeLogProbability'] = metric.pop('negativeLogProbabilitySum')/n if n else None
    return {'schemaVersion': 1, 'methodVersion': METHOD,
            'scope': 'retrospective_baseline_only_not_forward_validation',
            'implementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'counts': counts, 'descriptiveMetrics': metrics, 'races': rows,
            'sources': [{'path': str(p.relative_to(root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                        for p in paths],
            'forwardValidatedRaces': 0, 'returnIndex': None,
            'realOddsConnected': False, 'ticketSelectionEnabled': False,
            'appDisplayEnabled': False, 'autoBetEnabled': False,
            'limitations': ['勝率から着順を組み立てる比較用の仮定。専用学習モデルではない。',
                            '既存Top2・Top3モデルの入着確率とは一致しない。',
                            '過去の保存データの記述比較。発走前の時刻証拠を検証しておらず独立評価ではない。',
                            '3連複・3連単の発走前実オッズと確定配当は未接続。期待値・回収率を計算しない。',
                            '上位3着の同着と出走馬変更は通常の不的中と分ける。']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='read-only source repository')
    args = parser.parse_args()
    print(json.dumps(audit(args.root), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
