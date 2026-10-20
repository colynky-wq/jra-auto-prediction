"""Normalize historical JRA odds CSVs for retrospective all-bet research.

Historical collectors store pools with different column shapes. In particular,
place and wide quotes are ranges. This adapter emits the narrow schema consumed
by research_all_bet_retrospective.py and deliberately uses the *lower* end of
an odds range for EV selection. The upper end is retained for audit only.

No forward files are read or written by this module.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

import research_all_bet_retrospective as core

DIRECT_COMBO = ('組合せ', '組み合わせ', '組合せkey', 'combination', 'combo', 'outcome')
DIRECT_ODDS = ('最終オッズ', 'odds', 'final_odds', 'オッズ')
LOW_ODDS = ('最終オッズ下限', 'ワイド下限', '複勝下限', 'odds_low', 'low')
HIGH_ODDS = ('最終オッズ上限', 'ワイド上限', '複勝上限', 'odds_high', 'high')


def _first(row: dict, names, required=False):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != '':
            return value
    if required:
        raise ValueError('missing historical column: ' + '/'.join(names))
    return None


def _parts(row: dict, bet: str):
    direct = _first(row, DIRECT_COMBO)
    if direct is not None:
        return direct
    if bet in {'単勝', '複勝'}:
        return (_first(row, ('馬番', 'horse_no', 'number'), True),)
    if bet == '枠連':
        return (
            _first(row, ('枠1', '枠番1', '1枠', 'frame1'), True),
            _first(row, ('枠2', '枠番2', '2枠', 'frame2'), True),
        )
    if bet == '馬単':
        return (
            _first(row, ('1着馬番', '馬番1', 'horse1'), True),
            _first(row, ('2着馬番', '馬番2', 'horse2'), True),
        )
    if bet in {'馬連', 'ワイド'}:
        return (
            _first(row, ('馬番1', '1着馬番', 'horse1'), True),
            _first(row, ('馬番2', '2着馬番', 'horse2'), True),
        )
    if bet == '3連単':
        return (
            _first(row, ('1着馬番', '馬番1', 'horse1'), True),
            _first(row, ('2着馬番', '馬番2', 'horse2'), True),
            _first(row, ('3着馬番', '馬番3', 'horse3'), True),
        )
    if bet == '3連複':
        return (
            _first(row, ('馬番1', '1着馬番', 'horse1'), True),
            _first(row, ('馬番2', '2着馬番', 'horse2'), True),
            _first(row, ('馬番3', '3着馬番', 'horse3'), True),
        )
    raise ValueError(f'unsupported historical bet: {bet}')


def normalize_odds_rows(rows: Iterable[dict], source_name: str = '') -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        rid = str(_first(row, ('レースID', 'raceID', 'race_id'), True)).strip()
        bet = core.canonical_bet(_first(row, ('券種', 'betType', 'bet_type'), True))
        combo = core.canonical_combo(bet, _parts(row, bet))

        direct = core._number(_first(row, DIRECT_ODDS), positive=True)
        low = core._number(_first(row, LOW_ODDS), positive=True)
        high = core._number(_first(row, HIGH_ODDS), positive=True)
        if direct is not None:
            low = high = direct
            basis = 'exact_quote'
        elif low is not None:
            if high is None:
                high = low
            if high < low:
                raise ValueError(f'odds range reversed: {rid}/{bet}/{combo}')
            basis = 'lower_bound_conservative'
        else:
            # Includes 票数なし and other unavailable quotes.
            continue

        key = (rid, bet, combo)
        if key in seen:
            raise ValueError(f'duplicate normalized odds: {key}')
        seen.add(key)
        out.append({
            'レースID': rid,
            '券種': bet,
            '組合せ': combo,
            '最終オッズ': low,
            '最終オッズ上限_監査用': high,
            'オッズ採用基準': basis,
            'source_file': source_name,
        })
    return out


def read_csv(path: Path):
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ['レースID', '券種', '組合せ', '最終オッズ', '最終オッズ上限_監査用', 'オッズ採用基準', 'source_file']
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalize_files(paths: Iterable[Path]):
    result = []
    seen = set()
    for path in paths:
        for row in normalize_odds_rows(read_csv(path), Path(path).name):
            key = (row['レースID'], row['券種'], row['組合せ'])
            if key in seen:
                raise ValueError(f'duplicate across historical files: {key}')
            seen.add(key)
            result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', required=True,
                        help='Historical structured odds CSV; repeat for multiple pools/files')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = normalize_files(args.input)
    write_csv(args.output, rows)
    conservative = sum(r['オッズ採用基準'] == 'lower_bound_conservative' for r in rows)
    print(f'normalized={len(rows)} conservative_range_quotes={conservative}')
    print('retrospective research input only; forward ledgers untouched')


if __name__ == '__main__':
    main()
