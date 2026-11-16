"""Day-block bootstrap uncertainty for strict-OOS all-bet portfolio research.

The input sample CSV can be very large, so this module streams one race at a
time.  For each bet type × EV threshold × per-race Top-K cap it aggregates the
selected stake and return by calendar day, then resamples whole days with
replacement.  Whole-day resampling preserves within-day race/ticket dependence
better than treating millions of individual tickets as independent.

Output is research-only.  A 95% interval crossing 100% ROI is explicitly not
evidence of a profitable strategy.  Historical quotes are saved final odds; this
module does not claim the same EV was observable at the app's live pre-race time.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

import research_all_bet_portfolio_grid as grid
import research_all_bet_retrospective as core

METHOD = 'retrospective-all-bet-day-bootstrap-v1-20261021'
DEFAULT_REPS = 2000
DEFAULT_SEED = 20261021

RACE_ID_ALIASES = ('レースID', 'race_id', 'raceID')
DATE_ALIASES = ('日付', 'date', 'race_date', 'レース日')


def _first(row: dict, aliases):
    for name in aliases:
        if name in row and str(row[name]).strip() not in {'', 'nan', 'None'}:
            return row[name]
    return None


def load_race_dates(path: Path) -> dict[str, str]:
    mapping = {}
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            rid = _first(row, RACE_ID_ALIASES)
            date = _first(row, DATE_ALIASES)
            if rid is None or date is None:
                continue
            rid = str(rid).strip()
            text = str(date).strip().replace('/', '-').replace('.', '-')
            if len(text) == 8 and text.isdigit():
                text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
            old = mapping.get(rid)
            if old is not None and old != text:
                raise ValueError(f'conflicting race dates for {rid}: {old} vs {text}')
            mapping[rid] = text
    if not mapping:
        raise ValueError('race manifest produced no race_id/date mapping')
    return mapping


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y'}


def _number(row, key):
    value = core._number(row.get(key))
    if value is None:
        raise ValueError(f'missing numeric {key}')
    return float(value)


def _configs(thresholds, top_ks):
    return [
        (float(threshold), int(top_k), bet)
        for threshold in thresholds
        for top_k in top_ks
        for bet in core.ALL_BETS
    ]


def _consume_race(race_rows: list[dict], race_date: str, state: dict,
                  thresholds, top_ks, stake_yen: int):
    if not race_rows:
        return
    for threshold in thresholds:
        for top_k in top_ks:
            for bet in core.ALL_BETS:
                eligible = [
                    row for row in race_rows
                    if core.canonical_bet(row['bet_type']) == bet
                    and _bool(row.get('settled'))
                    and _number(row, 'model_ev') >= float(threshold)
                ]
                eligible.sort(key=lambda row: (-_number(row, 'model_ev'), str(row.get('combo', ''))))
                selected = eligible[:int(top_k)]
                if not selected:
                    continue
                stake = stake_yen * len(selected)
                returned = sum(
                    (core._number(row.get('payout_yen_per_100')) or 0.0) * stake_yen / 100.0
                    for row in selected if _bool(row.get('hit'))
                )
                day = state[(float(threshold), int(top_k), bet)][race_date]
                day['stake'] += stake
                day['return'] += returned
                day['races'] += 1
                day['tickets'] += len(selected)


def aggregate_days(samples_path: Path, race_dates: dict[str, str],
                   thresholds=core.DEFAULT_THRESHOLDS,
                   top_ks=grid.DEFAULT_TOP_K, stake_yen: int = 100):
    if stake_yen <= 0:
        raise ValueError('stake_yen must be positive')
    thresholds = tuple(map(float, thresholds)); top_ks = tuple(map(int, top_ks))
    state = {
        key: defaultdict(lambda: {'stake': 0.0, 'return': 0.0, 'races': 0, 'tickets': 0})
        for key in _configs(thresholds, top_ks)
    }
    current_rid = None
    current_rows = []
    seen = set()

    def flush(rid, rows):
        if rid is None or not rows:
            return
        if rid not in race_dates:
            raise ValueError(f'race date missing from manifest for {rid}')
        _consume_race(rows, race_dates[rid], state, thresholds, top_ks, stake_yen)

    with Path(samples_path).open('r', encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            rid = str(row['race_id']).strip()
            if current_rid is None:
                current_rid = rid
            if rid != current_rid:
                if rid in seen:
                    raise ValueError('sample CSV must be grouped by race')
                seen.add(current_rid)
                flush(current_rid, current_rows)
                current_rid = rid
                current_rows = []
            current_rows.append(row)
    flush(current_rid, current_rows)
    return state


def bootstrap_rows(state: dict, reps: int = DEFAULT_REPS, seed: int = DEFAULT_SEED):
    reps = int(reps)
    if reps <= 0:
        raise ValueError('bootstrap reps must be positive')
    rng = np.random.default_rng(int(seed))
    out = []
    for (threshold, top_k, bet), by_day in state.items():
        days = sorted(by_day)
        if not days:
            out.append({
                'method': METHOD, 'bet_type': bet, 'ev_threshold': threshold,
                'top_k_per_race': top_k, 'day_count': 0, 'selected_bets': 0,
                'evaluated_races': 0, 'stake_yen': 0.0, 'return_yen': 0.0,
                'roi_pct': None, 'roi_ci_low_pct': None, 'roi_ci_median_pct': None,
                'roi_ci_high_pct': None, 'p_roi_gt_100': None,
                'bootstrap_reps': reps, 'bootstrap_seed': int(seed),
                'uncertainty_status': 'no_selected_bets',
                'forward_ledgers_touched': False,
            })
            continue
        stakes = np.asarray([by_day[d]['stake'] for d in days], dtype=float)
        returns = np.asarray([by_day[d]['return'] for d in days], dtype=float)
        races = sum(by_day[d]['races'] for d in days)
        tickets = sum(by_day[d]['tickets'] for d in days)
        total_stake = float(stakes.sum()); total_return = float(returns.sum())
        n = len(days)
        indices = rng.integers(0, n, size=(reps, n))
        boot_stake = stakes[indices].sum(axis=1)
        boot_return = returns[indices].sum(axis=1)
        valid = boot_stake > 0
        boot_roi = np.full(reps, np.nan, dtype=float)
        boot_roi[valid] = boot_return[valid] / boot_stake[valid] * 100.0
        finite = boot_roi[np.isfinite(boot_roi)]
        if finite.size:
            low, median, high = np.quantile(finite, [0.025, 0.5, 0.975])
            p_gt = float(np.mean(finite > 100.0))
        else:
            low = median = high = p_gt = np.nan
        out.append({
            'method': METHOD, 'bet_type': bet, 'ev_threshold': threshold,
            'top_k_per_race': top_k, 'day_count': n, 'selected_bets': tickets,
            'evaluated_races': races, 'stake_yen': total_stake,
            'return_yen': total_return,
            'roi_pct': total_return / total_stake * 100.0 if total_stake else None,
            'roi_ci_low_pct': float(low) if np.isfinite(low) else None,
            'roi_ci_median_pct': float(median) if np.isfinite(median) else None,
            'roi_ci_high_pct': float(high) if np.isfinite(high) else None,
            'p_roi_gt_100': p_gt if np.isfinite(p_gt) else None,
            'bootstrap_reps': reps, 'bootstrap_seed': int(seed),
            'uncertainty_status': 'ok' if n >= 20 else 'few_days',
            'forward_ledgers_touched': False,
        })
    return out


def write_csv(path: Path, rows: list[dict]):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8'); return
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def run(samples: Path, race_manifest: Path, output: Path,
        thresholds=core.DEFAULT_THRESHOLDS, top_ks=grid.DEFAULT_TOP_K,
        stake_yen: int = 100, reps: int = DEFAULT_REPS, seed: int = DEFAULT_SEED):
    dates = load_race_dates(race_manifest)
    state = aggregate_days(samples, dates, thresholds, top_ks, stake_yen)
    rows = bootstrap_rows(state, reps, seed)
    write_csv(output, rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--race-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--thresholds', default=','.join(map(str, core.DEFAULT_THRESHOLDS)))
    parser.add_argument('--top-k', default=','.join(map(str, grid.DEFAULT_TOP_K)))
    parser.add_argument('--stake-yen', type=int, default=100)
    parser.add_argument('--reps', type=int, default=DEFAULT_REPS)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    top_ks = tuple(int(x) for x in args.top_k.split(',') if x.strip())
    rows = run(args.samples, args.race_manifest, args.output, thresholds, top_ks,
               args.stake_yen, args.reps, args.seed)
    print(f'{METHOD} rows={len(rows)} reps={args.reps} output={args.output}')
    print('bootstrap_unit=calendar_day; forward_ledgers_touched=FALSE')


if __name__ == '__main__':
    main()
