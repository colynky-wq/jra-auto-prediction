"""Audit historical collector coverage against the authoritative race manifest.

The current 2025 archive is a partial-year collection.  A retrospective result
must therefore say exactly which chronological race prefix is covered instead
of calling it a full-year 2025 result.

This audit compares the race manifest with the three files required by the
all-bet study for each race:
  *_odds.parquet/csv
  *_horses.parquet/csv
  payout_raw/<race_id>.html.gz/html

A contiguous prefix is useful because it defines a transparent calendar scope;
internal holes are reported separately and must not be silently ignored.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re

RACE_RE = re.compile(r'(\d{12})')


def read_manifest(path: Path) -> list[dict]:
    with Path(path).open('r', encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError('race manifest is empty')
    required = {'レースID', '日付'}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f'race manifest missing columns: {sorted(missing)}')
    seen = set()
    for row in rows:
        rid = str(row['レースID']).strip()
        if not re.fullmatch(r'\d{12}', rid):
            raise ValueError(f'invalid race id in manifest: {rid!r}')
        if rid in seen:
            raise ValueError(f'duplicate race id in manifest: {rid}')
        seen.add(rid)
        row['レースID'] = rid
        row['日付'] = str(row['日付']).strip()
    return rows


def _ids_from_dir(directory: Path, suffix_markers: tuple[str, ...]) -> set[str]:
    ids = set()
    directory = Path(directory)
    if not directory.is_dir():
        return ids
    for path in directory.iterdir():
        if not path.is_file():
            continue
        lower = path.name.lower()
        if not any(lower.endswith(marker) for marker in suffix_markers):
            continue
        match = RACE_RE.search(path.name)
        if match:
            ids.add(match.group(1))
    return ids


def audit(manifest_path: Path, races_dir: Path, payout_dir: Path) -> dict:
    rows = read_manifest(manifest_path)
    ordered = [row['レースID'] for row in rows]
    meta = {row['レースID']: row for row in rows}
    manifest_set = set(ordered)

    odds = _ids_from_dir(races_dir, ('_odds.parquet', '_odds.csv'))
    horses = _ids_from_dir(races_dir, ('_horses.parquet', '_horses.csv'))
    payouts = _ids_from_dir(payout_dir, ('.html.gz', '.html'))
    complete = odds & horses & payouts

    prefix = 0
    for rid in ordered:
        if rid not in complete:
            break
        prefix += 1
    prefix_set = set(ordered[:prefix])
    covered_after_first_gap = complete & set(ordered[prefix:])
    internal_holes = [rid for rid in ordered[:max(
        (ordered.index(rid) + 1 for rid in complete if rid in manifest_set),
        default=0,
    )] if rid not in complete]

    first_missing = ordered[prefix] if prefix < len(ordered) else None
    last_prefix = ordered[prefix - 1] if prefix else None
    first_prefix = ordered[0] if prefix else None

    return {
        'manifest_path': str(manifest_path),
        'races_dir': str(races_dir),
        'payout_dir': str(payout_dir),
        'manifest_races': len(ordered),
        'odds_races': len(odds),
        'horse_metadata_races': len(horses),
        'payout_raw_races': len(payouts),
        'complete_three_file_races': len(complete & manifest_set),
        'contiguous_prefix_races': prefix,
        'contiguous_prefix_is_hole_free': not internal_holes and not covered_after_first_gap,
        'internal_hole_count': len(internal_holes),
        'internal_holes_first_50': internal_holes[:50],
        'covered_after_first_gap_count': len(covered_after_first_gap),
        'first_covered_race_id': first_prefix,
        'first_covered_date': meta[first_prefix]['日付'] if first_prefix else None,
        'last_contiguous_race_id': last_prefix,
        'last_contiguous_date': meta[last_prefix]['日付'] if last_prefix else None,
        'first_missing_race_id': first_missing,
        'first_missing_date': meta[first_missing]['日付'] if first_missing else None,
        'archive_covers_full_manifest': prefix == len(ordered) and complete >= manifest_set,
        'manifest_races_missing_odds': len(manifest_set - odds),
        'manifest_races_missing_horses': len(manifest_set - horses),
        'manifest_races_missing_payout_raw': len(manifest_set - payouts),
        'extra_odds_races_not_in_manifest': sorted(odds - manifest_set)[:50],
        'extra_horse_races_not_in_manifest': sorted(horses - manifest_set)[:50],
        'extra_payout_races_not_in_manifest': sorted(payouts - manifest_set)[:50],
        'scope_label': (
            'full_manifest' if prefix == len(ordered) and complete >= manifest_set
            else 'contiguous_partial_year_prefix' if not internal_holes and not covered_after_first_gap
            else 'non_contiguous_archive'
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--races-dir', type=Path, required=True)
    parser.add_argument('--payout-dir', type=Path, required=True)
    parser.add_argument('--output-json', type=Path)
    parser.add_argument('--require-contiguous-prefix', action='store_true')
    args = parser.parse_args()
    report = audit(args.manifest, args.races_dir, args.payout_dir)
    if args.require_contiguous_prefix and report['scope_label'] == 'non_contiguous_archive':
        raise SystemExit('archive coverage is not a contiguous chronological prefix')
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
