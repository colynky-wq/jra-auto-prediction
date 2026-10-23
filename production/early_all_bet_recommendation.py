"""Same-day morning real-odds cross-pool research snapshot.

This is deliberately separate from all_bet_v1/triple_v1/Wide frozen live
records.  It reads the already-published early RaceData prediction, fetches the
current real odds without writing those frozen research inputs, and produces the
same research-only AllBetRecommendationData contract across nine ordinary bet
families.  The later live pipeline remains free to record its own first formal
pre-race observations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path

import all_bet_baseline as single
import all_bet_forward as added
import build_signal_data_v2 as signals
import cross_bet_recommendation as cross
import live_monitor_dry_run as live_base
import research_triple_baseline as triple_model
import triple_forward as triple
import wide_odds_runtime as wide_odds
import all_bet_recommendation_shadow as shadow

JST = added.JST
VERSION = 'all-bet-morning-real-odds-shadow-v1-20261023'


def _load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def _normalised_race(race: dict) -> dict:
    race = deepcopy(race)
    horses = race.get('horses') or []
    single.roster(horses)
    for key in ('aiProbability', 'marketProbability'):
        values = [float(h[key]) for h in horses]
        total = sum(values)
        if total <= 0:
            raise ValueError('positive win probability total required')
        for horse, value in zip(horses, values):
            horse[key] = value / total * 100.0
    return race


def _ordinary(kind: str, race: dict, evidence, fetch) -> tuple[dict, list[dict], dict | None]:
    try:
        ai = single.probabilities(race['horses'], 'aiProbability', kind, evidence)
        market = single.probabilities(race['horses'], 'marketProbability', kind, evidence)
        raw = fetch(race['raceID'], kind)
        if not isinstance(raw, dict) or raw.get('status') != 'middle':
            raise ValueError('pre-race odds status required')
        odds = added.parse_table(raw['data']['odds'][str(single.TYPES[kind])], ai, kind)
        record_like = {'prediction': race, 'entryEvidence': evidence}
        candidates = [
            cross._row(kind, key, cross._labels(record_like, kind, key), probability, market[key],
                       odds[key]['low'], odds[key]['high'], sourceScope='same_day_morning_real_odds')
            for key, probability in ai.items()
        ]
        return {'status': 'ready', 'candidates': candidates}, candidates, {'ai': ai, 'market': market, 'odds': odds}
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {'status': 'held', 'candidates': []}, [], None


def _triple(kind: str, race: dict, fetch) -> tuple[dict, list[dict]]:
    try:
        index = 1 if kind == 'trio' else 0
        ai_distribution = triple_model.distributions(race['horses'], 'aiProbability')[index]
        market_distribution = triple_model.distributions(race['horses'], 'marketProbability')[index]
        ai = {single.outcome_key(numbers): probability for numbers, probability in ai_distribution.items()}
        market = {single.outcome_key(numbers): probability for numbers, probability in market_distribution.items()}
        raw = fetch(race['raceID'], kind)
        odds = triple.odds_map(raw, kind, sorted(single.roster(race['horses'])))
        record_like = {'prediction': race, 'entryEvidence': None}
        candidates = [
            cross._row(kind, key, cross._labels(record_like, kind, key), probability, market[key],
                       odds[key], odds[key], sourceScope='same_day_morning_real_odds')
            for key, probability in ai.items()
        ]
        return {'status': 'ready', 'candidates': candidates}, candidates
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {'status': 'held', 'candidates': []}, []


def _support(race: dict, win_data: dict | None, place_data: dict | None) -> tuple[dict, list[dict]]:
    if not win_data or not place_data:
        return {'status': 'held', 'candidates': []}, []
    names = {f"{int(h['number']):02}": str(h['name']) for h in race['horses']}
    candidates = []
    for key in sorted(place_data['ai']):
        wa, pa = win_data['ai'][key], place_data['ai'][key]
        wm, pm = win_data['market'][key], place_data['market'][key]
        w_odds, p_odds = win_data['odds'][key], place_data['odds'][key]
        low = (wa * w_odds['low'] + pa * p_odds['low']) / 2.0
        high = (wa * w_odds['high'] + pa * p_odds['high']) / 2.0
        candidates.append({
            'betType': 'support', 'betTypeLabel': shadow.LABELS['support'],
            'id': key, 'label': f"{int(key)}番 {names[key]}（単勝100円＋複勝100円）",
            'probabilityPct': round(pa * 100, 6), 'marketProbabilityPct': round(pm * 100, 6),
            'winProbabilityPct': round(wa * 100, 6), 'oddsLow': None, 'oddsHigh': None,
            'winOddsLow': round(w_odds['low'], 6), 'winOddsHigh': round(w_odds['high'], 6),
            'placeOddsLow': round(p_odds['low'], 6), 'placeOddsHigh': round(p_odds['high'], 6),
            'expectedMultiplierLow': round(low, 6), 'expectedMultiplierHigh': round(high, 6),
            'sourceScope': 'same_day_morning_real_odds',
        })
    return {'status': 'ready', 'candidates': candidates}, candidates


def _wide(race: dict, fetch) -> tuple[dict, list[dict], dict | None]:
    snapshot = None
    try:
        snapshot = fetch(
            str(race['raceID']), race_date=str(race['raceDate']), venue=str(race.get('venue') or ''),
            race_number=int(race.get('raceNumber') or 0), start_time=str(race.get('startTime') or ''), retries=1,
        )
    except Exception:
        pass
    signal = signals.build_live_race_signal(race, snapshot)
    if not snapshot or not signal:
        return {'status': 'held', 'candidates': []}, [], signal
    try:
        pairs = (signal.get('wideEvaluation') or {}).get('validationPairs') or []
        candidates = []
        for pair in pairs:
            low, high = pair.get('wideOddsLow'), pair.get('wideOddsHigh')
            probability = pair.get('aiWideProbability')
            if low is None or high is None or probability is None:
                continue
            key = single.outcome_key(sorted((int(pair['number1']), int(pair['number2']))))
            label = f"{int(pair['number1'])}番 {pair['name1']} − {int(pair['number2'])}番 {pair['name2']}"
            candidates.append(cross._row(
                'wide', key, label, float(probability) / 100.0, None, float(low), float(high),
                sourceScope='same_day_morning_real_odds_all_pairs'))
        if not candidates:
            raise ValueError('no connected Wide candidates')
        return {'status': 'ready', 'candidates': candidates}, candidates, signal
    except (ValueError, KeyError, TypeError, OverflowError):
        return {'status': 'held', 'candidates': []}, [], signal


def build_one(race: dict, evidence, *, fetch_simple=added.fetch_odds,
              fetch_triple=triple.fetch_odds, fetch_wide=wide_odds.fetch_wide_snapshot) -> dict:
    race = _normalised_race(race)
    rid = str(race['raceID'])
    bets = {}
    pool = []
    raw = {}
    for kind in single.TYPES:
        row, candidates, data = _ordinary(kind, race, evidence, fetch_simple)
        bets[(rid, kind)] = row
        pool.extend(candidates)
        raw[kind] = data
    for kind in triple.TYPES:
        row, candidates = _triple(kind, race, fetch_triple)
        bets[(rid, kind)] = row
        pool.extend(candidates)
    row, candidates, signal = _wide(race, fetch_wide)
    bets[(rid, 'wide')] = row
    pool.extend(candidates)
    row, candidates = _support(race, raw.get('win'), raw.get('place'))
    bets[(rid, 'support')] = row
    pool.extend(candidates)
    if signal is None:
        signal = signals.build_live_race_signal(race, None)
    result = shadow.build_race(race, bets, signal, pool)
    result['snapshotStage'] = 'morning'
    result['realOddsSnapshot'] = True
    return result


def _entry_evidence(race: dict, date8: str):
    try:
        entries, _, _ = live_base.fetch_jra_entry_table(str(race['raceID']), date8, JST)
        return entries.attrs.get('allBetEntryEvidence')
    except Exception:
        return None


def _network_one(race: dict, date8: str) -> dict:
    evidence = _entry_evidence(race, date8)
    return build_one(race, evidence)


def build(root: Path, *, workers: int = 4) -> dict:
    root = Path(root)
    races = _load(root / 'RaceData.json')
    if not isinstance(races, list) or not races:
        raise ValueError('RaceData.json must be a nonempty array')
    days = {str(r.get('raceDate') or '') for r in races}
    if len(days) != 1:
        raise ValueError('one race day required')
    day = next(iter(days))
    date8 = day.replace('-', '')
    if datetime.now(JST).date().isoformat() != day:
        raise ValueError('morning real-odds snapshot is same-day only')
    live_base.TARGET_DATE8 = date8

    by_id = {}
    workers = max(1, min(int(workers), 8))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='early-all-bet') as executor:
        jobs = {executor.submit(_network_one, race, date8): str(race['raceID']) for race in races}
        for future in as_completed(jobs):
            rid = jobs[future]
            try:
                by_id[rid] = future.result()
            except Exception as error:
                race = next(r for r in races if str(r['raceID']) == rid)
                bets = {(rid, kind): {'status': 'held', 'candidates': []} for kind in shadow.BET_TYPES}
                fallback = signals.build_live_race_signal(_normalised_race(race), None)
                value = shadow.build_race(race, bets, fallback, [])
                value['snapshotStage'] = 'morning'
                value['realOddsSnapshot'] = False
                value['captureErrorType'] = type(error).__name__
                by_id[rid] = value

    built = [by_id[str(r['raceID'])] for r in sorted(races, key=lambda x: (str(x.get('startTime') or ''), str(x['raceID'])))]
    counts = {status: sum(r['recommendationStatus'] == status for r in built)
              for status in ('candidate', 'skip', 'odds_pending', 'signal_pending')}
    return {
        'schemaVersion': 2,
        'version': VERSION,
        'status': 'research_display_only',
        'raceDate': day,
        'snapshotStage': 'morning',
        'generatedAtJST': datetime.now(JST).isoformat(timespec='seconds'),
        'prospectiveStartDate': shadow.PROSPECTIVE_START,
        'rulesFrozenAtJST': '2026-10-23',
        'formalRecommendationsEnabled': False,
        'autoBetEnabled': False,
        'stakeSizingEnabled': False,
        'frozenLiveResearchChanged': False,
        'ordinaryBetTypes': [{'id': k, 'label': shadow.LABELS[k], **shadow.RULES[k]} for k in shadow.BET_TYPES],
        'win5Policy': 'WIN5は5対象レース共通締切の別研究として扱い、1レース単位の9券種横断順位には混ぜない。',
        'candidateScope': '朝時点の実オッズを別スナップショットとして取得し、9券種の全組合せ（ワイドを含む）を比較。直前の凍結研究記録は変更しない。',
        'scoreMeaning': 'researchScore = 表示確率 × 保守的期待値超過分 ÷ 券種リスク係数。研究上の並び順であり利益予測ではない。',
        'counts': counts,
        'races': built,
    }


def main() -> None:
    root = Path(os.environ.get('KEIBAAI_ROOT', Path(__file__).resolve().parent.parent))
    stage = os.environ.get('KEIBAAI_EARLY_STAGE', 'morning').strip() or 'morning'
    if stage != 'morning':
        shadow.main()
        return
    workers = int(os.environ.get('KEIBAAI_EARLY_ALL_BET_WORKERS', '4'))
    value = build(root, workers=workers)
    shadow.write_json(root / 'AllBetRecommendationData.json', value)
    print('EARLY_ALL_BET_REAL_ODDS', value['raceDate'], value['counts'])


if __name__ == '__main__':
    main()
