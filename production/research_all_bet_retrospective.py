"""Retrospective all-bet EV/ROI research. Never writes forward ledgers.

This module is intentionally separate from the prospective/forward recorders.
It consumes saved horse probabilities, historical quoted odds and confirmed
payouts and produces research-only retrospective CSVs. It does not recommend
or place bets.

The probability baseline is Plackett-Luce/Harville built from saved win
probabilities. Only combinations that have a usable historical quote are
scored, which avoids materialising every 3連単 outcome for every race.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from itertools import permutations
import math
from pathlib import Path
import re
from typing import Iterable

METHOD = 'retrospective-all-bet-pl-win-v1-20261021'
DEFAULT_THRESHOLDS = (1.00, 1.05, 1.10, 1.15, 1.20, 1.30, 1.50)

BET_ALIASES = {
    'win': '単勝', '単勝': '単勝',
    'place': '複勝', '複勝': '複勝',
    'frame_quinella': '枠連', 'wakuren': '枠連', '枠連': '枠連',
    'quinella': '馬連', 'umaren': '馬連', '馬連': '馬連',
    'wide': 'ワイド', 'ワイド': 'ワイド',
    'exacta': '馬単', 'umatan': '馬単', '馬単': '馬単',
    'trio': '3連複', 'sanrenpuku': '3連複', '3連複': '3連複', '三連複': '3連複',
    'trifecta': '3連単', 'sanrentan': '3連単', '3連単': '3連単', '三連単': '3連単',
}
UNORDERED = {'枠連', '馬連', 'ワイド', '3連複'}
ORDERED = {'馬単', '3連単'}
SINGLES = {'単勝', '複勝'}
ALL_BETS = ('単勝', '複勝', '枠連', '馬連', 'ワイド', '馬単', '3連複', '3連単')

PRED_ALIASES = {
    'race_id': ('race_id', 'raceID', 'レースID'),
    'horse_no': ('horse_no', 'horseNumber', 'number', '馬番'),
    'win_prob': ('win_prob', 'ai_win_prob', 'aiProbability', 'AI勝率', '予測勝率'),
    'frame': ('frame', '枠', '枠番'),
    'place_count': ('place_count', '複勝着内数'),
    'is_oos': ('is_oos', 'oos', 'OOS'),
}
ODDS_ALIASES = {
    'race_id': ('race_id', 'raceID', 'レースID'),
    'bet_type': ('bet_type', 'betType', '券種'),
    'combo': ('combination', 'combo', 'outcome', '組合せ', '組み合わせ', '組合せkey'),
    'odds': ('odds', 'final_odds', '最終オッズ', 'オッズ'),
}
PAYOUT_ALIASES = {
    'race_id': ('race_id', 'raceID', 'レースID'),
    'bet_type': ('bet_type', 'betType', '券種'),
    'combo': ('combination', 'combo', 'outcome', '組合せ', '組み合わせ', '組合せkey'),
    'payout': ('payout_yen', 'payout', '払戻円', '払戻'),
}


def _field(row: dict, aliases: Iterable[str], *, required: bool = True):
    for name in aliases:
        if name in row and str(row[name]).strip() != '':
            return row[name]
    if required:
        raise ValueError('missing column/value: ' + '/'.join(aliases))
    return None


def _number(value, *, positive: bool = False):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '').replace('円', '').replace('倍', '')
    if not text or text.lower() in {'nan', 'none', 'null', 'na', '-', '票数なし', '発売なし'}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _integer(value, *, low: int = 1, high: int = 18):
    number = _number(value)
    if number is None or not float(number).is_integer():
        raise ValueError(f'invalid integer: {value!r}')
    number = int(number)
    if not low <= number <= high:
        raise ValueError(f'integer out of range: {number}')
    return number


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'oos', '○'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'insample', 'in_sample', '×'}:
        return False
    raise ValueError(f'invalid OOS flag: {value!r}')


def canonical_bet(value) -> str:
    text = str(value).strip()
    if text not in BET_ALIASES:
        raise ValueError(f'unsupported bet type: {text}')
    return BET_ALIASES[text]


def combo_numbers(value) -> tuple[int, ...]:
    if isinstance(value, (tuple, list)):
        values = value
    else:
        text = str(value).strip()
        values = re.findall(r'\d+', text)
        if len(values) == 1 and len(values[0]) in {4, 6}:
            token = values[0]
            values = [token[i:i + 2] for i in range(0, len(token), 2)]
    if not values:
        raise ValueError(f'invalid combination: {value!r}')
    return tuple(_integer(v) for v in values)


def canonical_combo(bet_type, value) -> str:
    bet = canonical_bet(bet_type)
    nums = combo_numbers(value)
    expected = 1 if bet in SINGLES else 3 if bet in {'3連複', '3連単'} else 2
    if len(nums) != expected or len(set(nums)) != len(nums) and bet != '枠連':
        raise ValueError(f'invalid {bet} combination: {value!r}')
    if bet in UNORDERED:
        nums = tuple(sorted(nums))
    return '-'.join(str(n) for n in nums)


def read_csv(path: Path) -> list[dict]:
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def prediction_index(rows: Iterable[dict]):
    races = defaultdict(dict)
    metadata = defaultdict(dict)
    oos_seen = False
    oos_verified = True
    for row in rows:
        rid = str(_field(row, PRED_ALIASES['race_id'])).strip()
        if not rid:
            raise ValueError('empty race id')
        horse = _integer(_field(row, PRED_ALIASES['horse_no']))
        p = _number(_field(row, PRED_ALIASES['win_prob']), positive=True)
        if p is None:
            raise ValueError('positive win probability required')
        # Accept either 0..1 or percent units. Normalisation below is authoritative.
        if horse in races[rid]:
            raise ValueError(f'duplicate prediction {rid}/{horse}')
        races[rid][horse] = p
        frame = _field(row, PRED_ALIASES['frame'], required=False)
        if frame is not None:
            metadata[rid].setdefault('frames', {})[horse] = _integer(frame, high=8)
        place_count = _field(row, PRED_ALIASES['place_count'], required=False)
        if place_count is not None:
            pc = _integer(place_count, low=2, high=3)
            old = metadata[rid].get('place_count')
            if old is not None and old != pc:
                raise ValueError(f'inconsistent place_count in {rid}')
            metadata[rid]['place_count'] = pc
        flag = _field(row, PRED_ALIASES['is_oos'], required=False)
        if flag is not None:
            oos_seen = True
            oos_verified = oos_verified and _bool(flag)
    if oos_seen and not oos_verified:
        raise ValueError('in-sample prediction row found; retrospective OOS study aborted')
    for rid, weights in races.items():
        total = math.fsum(weights.values())
        if total <= 0:
            raise ValueError(f'empty probability mass: {rid}')
        races[rid] = {horse: value / total for horse, value in weights.items()}
    return dict(races), dict(metadata), oos_seen


def odds_rows(rows: Iterable[dict]) -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        rid = str(_field(row, ODDS_ALIASES['race_id'])).strip()
        bet = canonical_bet(_field(row, ODDS_ALIASES['bet_type']))
        combo = canonical_combo(bet, _field(row, ODDS_ALIASES['combo']))
        odds = _number(_field(row, ODDS_ALIASES['odds']), positive=True)
        # "票数なし" / null / zero quote is unavailable, never a zero-odds bet.
        if odds is None:
            continue
        key = (rid, bet, combo)
        if key in seen:
            raise ValueError(f'duplicate odds quote: {key}')
        seen.add(key)
        out.append({'race_id': rid, 'bet_type': bet, 'combo': combo, 'odds': odds})
    return out


def payout_index(rows: Iterable[dict]):
    payouts = {}
    coverage = set()
    for row in rows:
        rid = str(_field(row, PAYOUT_ALIASES['race_id'])).strip()
        bet = canonical_bet(_field(row, PAYOUT_ALIASES['bet_type']))
        combo = canonical_combo(bet, _field(row, PAYOUT_ALIASES['combo']))
        amount = _number(_field(row, PAYOUT_ALIASES['payout']), positive=True)
        if amount is None:
            raise ValueError(f'invalid payout: {rid}/{bet}/{combo}')
        coverage.add((rid, bet))
        key = (rid, bet, combo)
        if key in payouts:
            raise ValueError(f'duplicate payout: {key}')
        payouts[key] = amount
    return payouts, coverage


def _exacta(weights, a, b):
    if a == b or a not in weights or b not in weights:
        return 0.0
    return weights[a] * weights[b] / max(1.0 - weights[a], 1e-15)


def _trifecta(weights, a, b, c):
    if len({a, b, c}) < 3 or any(n not in weights for n in (a, b, c)):
        return 0.0
    after_a = 1.0 - weights[a]
    after_ab = 1.0 - weights[a] - weights[b]
    if after_a <= 0 or after_ab <= 0:
        return 0.0
    return weights[a] * weights[b] / after_a * weights[c] / after_ab


def _place_count(weights, meta):
    if 'place_count' in meta:
        return meta['place_count']
    n = len(weights)
    if n < 5:
        return None
    return 2 if n <= 7 else 3


def probability(weights: dict[int, float], meta: dict, bet_type: str, combo: str):
    """Return baseline probability for one historically quoted combination."""
    bet = canonical_bet(bet_type)
    nums = combo_numbers(combo)
    if bet in UNORDERED:
        nums = tuple(sorted(nums))
    if bet == '単勝':
        return weights.get(nums[0])
    if bet == '馬単':
        return _exacta(weights, *nums)
    if bet == '馬連':
        a, b = nums
        return _exacta(weights, a, b) + _exacta(weights, b, a)
    if bet == '3連単':
        return _trifecta(weights, *nums)
    if bet == '3連複':
        return math.fsum(_trifecta(weights, *order) for order in permutations(nums, 3))
    if bet == 'ワイド':
        a, b = nums
        if a not in weights or b not in weights or a == b:
            return 0.0
        total = 0.0
        for c in weights:
            if c in (a, b):
                continue
            total += math.fsum(_trifecta(weights, *order) for order in permutations((a, b, c), 3))
        return total
    if bet == '複勝':
        horse = nums[0]
        places = _place_count(weights, meta)
        if places is None or horse not in weights:
            return None
        if places == 2:
            return math.fsum(_exacta(weights, horse, other) + _exacta(weights, other, horse)
                             for other in weights if other != horse)
        total = 0.0
        others = [n for n in weights if n != horse]
        # Sum every ordered top-three containing the selected horse exactly once.
        for a in others:
            for b in others:
                if a == b:
                    continue
                total += (_trifecta(weights, horse, a, b) +
                          _trifecta(weights, a, horse, b) +
                          _trifecta(weights, a, b, horse))
        return total
    if bet == '枠連':
        frames = meta.get('frames', {})
        if len(frames) != len(weights):
            return None
        f1, f2 = nums
        total = 0.0
        horses = sorted(weights)
        for i, a in enumerate(horses):
            for b in horses[i + 1:]:
                if tuple(sorted((frames[a], frames[b]))) == (f1, f2):
                    total += _exacta(weights, a, b) + _exacta(weights, b, a)
        return total
    raise ValueError(f'unsupported bet: {bet}')


def score_quotes(predictions: list[dict], odds: list[dict], payouts: list[dict]):
    races, metadata, oos_scope_verified = prediction_index(predictions)
    quotes = odds_rows(odds)
    payout_map, coverage = payout_index(payouts)
    samples = []
    for quote in quotes:
        rid, bet, combo = quote['race_id'], quote['bet_type'], quote['combo']
        if rid not in races:
            continue
        prob = probability(races[rid], metadata.get(rid, {}), bet, combo)
        if prob is None or not math.isfinite(prob) or prob <= 0:
            continue
        ev = prob * quote['odds']
        covered = (rid, bet) in coverage
        amount = payout_map.get((rid, bet, combo), 0.0) if covered else None
        samples.append({
            'race_id': rid, 'bet_type': bet, 'combo': combo,
            'model_prob': prob, 'odds': quote['odds'], 'model_ev': ev,
            'settled': covered, 'hit': bool(covered and amount),
            'payout_yen_per_100': amount if covered else None,
            'oos_scope_verified': oos_scope_verified,
        })
    return samples


def _max_losing_streak(rows):
    longest = current = 0
    for row in rows:
        if row['settled'] and not row['hit']:
            current += 1
            longest = max(longest, current)
        elif row['settled'] and row['hit']:
            current = 0
    return longest


def summarise(samples: list[dict], thresholds=DEFAULT_THRESHOLDS, stake_yen=100):
    if stake_yen <= 0:
        raise ValueError('positive stake required')
    rows = []
    for threshold in thresholds:
        for bet in ALL_BETS:
            selected = [r for r in samples if r['bet_type'] == bet and r['model_ev'] >= threshold]
            settled = [r for r in selected if r['settled']]
            hits = [r for r in settled if r['hit']]
            stake = stake_yen * len(settled)
            returned = math.fsum((r['payout_yen_per_100'] or 0) * stake_yen / 100 for r in settled)
            rows.append({
                'method': METHOD, 'bet_type': bet, 'ev_threshold': threshold,
                'candidate_bets': len(selected), 'settled_bets': len(settled),
                'unresolved_bets': len(selected) - len(settled), 'hits': len(hits),
                'hit_rate': len(hits) / len(settled) if settled else None,
                'stake_yen': stake, 'return_yen': returned,
                'profit_yen': returned - stake if settled else 0.0,
                'roi_pct': returned / stake * 100 if stake else None,
                'max_losing_streak': _max_losing_streak(settled),
                'mean_model_ev': math.fsum(r['model_ev'] for r in selected) / len(selected) if selected else None,
                'mean_odds': math.fsum(r['odds'] for r in selected) / len(selected) if selected else None,
                'evaluated_races': len({r['race_id'] for r in settled}),
            })
    return rows


def write_csv(path: Path, rows: list[dict]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def run(predictions_path: Path, odds_path: Path, payouts_path: Path, output_dir: Path,
        thresholds=DEFAULT_THRESHOLDS, stake_yen=100):
    samples = score_quotes(read_csv(predictions_path), read_csv(odds_path), read_csv(payouts_path))
    summary = summarise(samples, thresholds, stake_yen)
    output_dir = Path(output_dir)
    write_csv(output_dir / 'retrospective_all_bet_samples.csv', samples)
    write_csv(output_dir / 'retrospective_all_bet_summary.csv', summary)
    write_csv(output_dir / 'retrospective_ev_grid.csv', summary)
    return {'samples': samples, 'summary': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--odds', type=Path, required=True)
    parser.add_argument('--payouts', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stake-yen', type=int, default=100)
    parser.add_argument('--thresholds', default=','.join(str(x) for x in DEFAULT_THRESHOLDS))
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    result = run(args.predictions, args.odds, args.payouts, args.output_dir, thresholds, args.stake_yen)
    verified = all(r['oos_scope_verified'] for r in result['samples']) if result['samples'] else False
    print(f'{METHOD} samples={len(result["samples"])} OOS_SCOPE_VERIFIED={verified}')
    print('research-only retrospective outputs written; forward ledgers untouched')


if __name__ == '__main__':
    main()
