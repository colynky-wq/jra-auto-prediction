"""Race-level portfolio grid for retrospective all-bet research.

Consumes ``retrospective_oos_all_bet_samples.csv`` and compares EV thresholds
with a per-race Top-K cap. This is research-only: it does not place bets and it
does not touch forward ledgers.

Why this exists: an EV threshold alone can select dozens or hundreds of exotic
combinations in one race. The app's eventual decision rule must therefore study
both probability/value and the size of the race-level ticket portfolio. Trigami
is defined on total race stake versus total race return, not per ticket.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import research_all_bet_retrospective as core

DEFAULT_TOP_K = (1, 3, 5, 10, 20)


def _float(row, key):
    value = core._number(row.get(key))
    if value is None:
        raise ValueError(f'missing numeric {key}')
    return float(value)


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y'}


def select_top_k(rows: list[dict], *, bet: str, threshold: float, top_k: int) -> list[dict]:
    if top_k <= 0:
        raise ValueError('top_k must be positive')
    bet = core.canonical_bet(bet)
    by_race: dict[str, list[dict]] = {}
    for row in rows:
        if core.canonical_bet(row['bet_type']) != bet:
            continue
        if not _bool(row.get('settled')):
            continue
        if _float(row, 'model_ev') < float(threshold):
            continue
        rid = str(row['race_id'])
        by_race.setdefault(rid, []).append(row)

    selected = []
    for rid, race_rows in by_race.items():
        ranked = sorted(
            race_rows,
            key=lambda r: (-_float(r, 'model_ev'), str(r.get('combo', ''))),
        )
        selected.extend(ranked[:int(top_k)])
    return selected


def _race_portfolios(rows: list[dict], stake_yen: int):
    races: dict[str, dict] = {}
    for row in rows:
        rid = str(row['race_id'])
        race = races.setdefault(rid, {'tickets': 0, 'hits': 0, 'return_yen': 0.0})
        race['tickets'] += 1
        if _bool(row.get('hit')):
            race['hits'] += 1
            payout = core._number(row.get('payout_yen_per_100')) or 0.0
            race['return_yen'] += payout * stake_yen / 100.0
    return races


def _max_losing_races(races: dict[str, dict], stake_yen: int) -> int:
    longest = current = 0
    for race in races.values():
        stake = stake_yen * race['tickets']
        if race['return_yen'] < stake:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def evaluate(rows: list[dict], thresholds=core.DEFAULT_THRESHOLDS,
             top_ks=DEFAULT_TOP_K, stake_yen: int = 100) -> list[dict]:
    if stake_yen <= 0:
        raise ValueError('stake_yen must be positive')
    out = []
    for threshold in thresholds:
        for top_k in top_ks:
            for bet in core.ALL_BETS:
                selected = select_top_k(
                    rows, bet=bet, threshold=float(threshold), top_k=int(top_k)
                )
                races = _race_portfolios(selected, stake_yen)
                stake = stake_yen * len(selected)
                returned = sum(r['return_yen'] for r in races.values())
                hits = sum(1 for row in selected if _bool(row.get('hit')))
                hit_races = sum(r['hits'] > 0 for r in races.values())
                profitable = sum(r['return_yen'] > stake_yen * r['tickets'] for r in races.values())
                non_loss = sum(r['return_yen'] >= stake_yen * r['tickets'] for r in races.values())
                trigami = sum(
                    r['hits'] > 0 and r['return_yen'] < stake_yen * r['tickets']
                    for r in races.values()
                )
                out.append({
                    'method': 'retrospective-topk-portfolio-grid-v1-20261021',
                    'bet_type': bet,
                    'ev_threshold': float(threshold),
                    'top_k_per_race': int(top_k),
                    'selected_bets': len(selected),
                    'evaluated_races': len(races),
                    'hits': hits,
                    'hit_races': hit_races,
                    'stake_yen': stake,
                    'return_yen': returned,
                    'profit_yen': returned - stake if stake else 0.0,
                    'roi_pct': returned / stake * 100 if stake else None,
                    'profitable_races': profitable,
                    'profitable_race_rate': profitable / len(races) if races else None,
                    'non_loss_races': non_loss,
                    'non_loss_race_rate': non_loss / len(races) if races else None,
                    'trigami_races': trigami,
                    'trigami_rate_among_hit_races': trigami / hit_races if hit_races else None,
                    'max_losing_race_streak': _max_losing_races(races, stake_yen),
                    'mean_tickets_per_race': len(selected) / len(races) if races else None,
                    'forward_ledgers_touched': False,
                })
    return out


def read_csv(path: Path) -> list[dict]:
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--thresholds', default=','.join(map(str, core.DEFAULT_THRESHOLDS)))
    parser.add_argument('--top-k', default=','.join(map(str, DEFAULT_TOP_K)))
    parser.add_argument('--stake-yen', type=int, default=100)
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    top_ks = tuple(int(x) for x in args.top_k.split(',') if x.strip())
    rows = evaluate(read_csv(args.samples), thresholds, top_ks, args.stake_yen)
    write_csv(args.output, rows)
    print(f'portfolio_grid_rows={len(rows)} output={args.output}')
    print('research-only; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
