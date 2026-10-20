"""Add JRA frame numbers to strict-OOS prediction rows from collector horse metadata.

The 2025 collector horse parquet does not store 枠/枠番.  However it does store
all card horse numbers, including non-sale/scratched entries, so the original
field size is recoverable as the maximum horse number.  JRA frame allocation is
then deterministic:
- 1..8 horses: one horse per frame 1..N;
- 9..16: inner frames have one horse and outer frames have two;
- 17..18: inner frames have two horses and the outermost frame(s) have three.

This module enriches research-only OOS CSVs.  It never touches forward ledgers.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re

import pandas as pd

HORSE_NO_ALIASES = ('馬番', 'horse_no', 'horseNumber', 'number', '番号')
RACE_ID_ALIASES = ('レースID', 'race_id', 'raceID')


def _first(row: dict, aliases):
    for name in aliases:
        if name in row:
            value = row.get(name)
            if value is not None and str(value).strip() not in {'', 'nan', 'None', '<NA>'}:
                return value
    return None


def _int(value, low=1, high=18):
    number = int(float(value))
    if not low <= number <= high:
        raise ValueError(f'integer out of range: {number}')
    return number


def frame_sizes(field_size: int) -> list[int]:
    """Return horses-per-frame sizes from frame 1 outward for an original JRA field."""
    n = _int(field_size)
    if n <= 8:
        return [1] * n
    if n <= 16:
        return [1] * (16 - n) + [2] * (n - 8)
    return [2] * (24 - n) + [3] * (n - 16)


def frame_for_horse(horse_no: int, field_size: int) -> int:
    horse = _int(horse_no)
    n = _int(field_size)
    if horse > n:
        raise ValueError(f'horse number {horse} exceeds original field size {n}')
    end = 0
    for frame, size in enumerate(frame_sizes(n), 1):
        end += size
        if horse <= end:
            return frame
    raise AssertionError('unreachable frame allocation')


def read_table(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() in {'.parquet', '.pq', '.bin'}:
        return pd.read_parquet(path).to_dict(orient='records')
    if path.suffix.lower() == '.csv':
        return pd.read_csv(path, low_memory=False).to_dict(orient='records')
    raise ValueError(f'unsupported horse metadata format: {path}')


def race_id_from_horse_path(path: Path) -> str:
    m = re.search(r'(\d{12})_horses\.(?:parquet|csv|bin)$', Path(path).name, re.I)
    if not m:
        raise ValueError(f'cannot infer race id: {Path(path).name}')
    return m.group(1)


def metadata_field_size(rows: list[dict]) -> int:
    numbers = []
    for row in rows:
        raw = _first(row, HORSE_NO_ALIASES)
        if raw is not None:
            numbers.append(_int(raw))
    if not numbers:
        raise ValueError('horse metadata has no horse numbers')
    unique = sorted(set(numbers))
    field_size = max(unique)
    expected = list(range(1, field_size + 1))
    if unique != expected:
        raise ValueError(f'horse metadata is not a complete original card: got={unique}, expected={expected}')
    return field_size


def discover_horse_files(odds_dir: Path) -> dict[str, Path]:
    root = Path(odds_dir)
    found = {}
    for path in list(root.glob('*_horses.parquet')) + list(root.glob('*_horses.csv')):
        rid = race_id_from_horse_path(path)
        found[rid] = path
    return found


def enrich_rows(predictions: list[dict], horse_files: dict[str, Path]):
    cache = {}
    out = []
    audit = {}
    for row in predictions:
        copied = dict(row)
        rid = str(_first(copied, RACE_ID_ALIASES) or '').strip()
        if not rid:
            raise ValueError('prediction row missing race id')
        horse = _int(_first(copied, HORSE_NO_ALIASES))
        path = horse_files.get(rid)
        if path is None:
            raise ValueError(f'missing collector horse metadata for OOS race {rid}')
        if rid not in cache:
            field_size = metadata_field_size(read_table(path))
            cache[rid] = field_size
            audit[rid] = {'horse_file': path.name, 'original_field_size': field_size}
        copied['枠'] = frame_for_horse(horse, cache[rid])
        out.append(copied)
    return out, audit


def read_csv(path: Path) -> list[dict]:
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    fields = list(rows[0])
    if '枠' not in fields:
        fields.append('枠')
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--odds-dir', type=Path, required=True,
                        help='Collector races directory containing *_horses.parquet')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = read_csv(args.predictions)
    enriched, audit = enrich_rows(rows, discover_horse_files(args.odds_dir))
    write_csv(args.output, enriched)
    print(f'rows={len(enriched)} races={len(audit)} frame_source=JRA_original_card_allocation')
    print('forward_ledgers_touched=FALSE')


if __name__ == '__main__':
    main()
