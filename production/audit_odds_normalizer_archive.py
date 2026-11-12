"""Fast fail-closed audit of every saved historical odds parquet/CSV.

This intentionally avoids predictions, payouts, portfolio research and model
training. It validates only that every collector odds file can be normalized
under the retrospective all-bet schema before the expensive full study runs.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import normalize_historical_all_bet as base
import normalize_historical_all_bet_v2 as normalizer

DEFAULT_ODDS_DIR = Path(
    '/content/gdrive_live/MyDrive/code_output/jra_data/historical_odds_2025/races'
)


def run(odds_dir: Path, output: Path | None = None):
    odds_dir = Path(odds_dir)
    files = base.discover_odds_files(odds_dir)
    if not files:
        raise ValueError(f'no historical odds files found under {odds_dir}')

    unresolved = []
    passed = 0
    normalized_rows = 0
    bet_counts = Counter()
    schema_counts = Counter()

    for index, path in enumerate(files, 1):
        try:
            rows = base.read_table(path)
            schema = tuple(rows[0].keys()) if rows else ()
            schema_counts[schema] += 1
            normalized = normalizer.normalize_odds_rows(rows, path.name)
            passed += 1
            normalized_rows += len(normalized)
            bet_counts.update(row['券種'] for row in normalized)
        except Exception as exc:
            try:
                rows = base.read_table(path)
                columns = list(rows[0].keys()) if rows else []
            except Exception:
                columns = []
            unresolved.append({
                'file': path.name,
                'error_type': type(exc).__name__,
                'error': str(exc),
                'columns': '|'.join(columns),
            })

        if index % 250 == 0 or index == len(files):
            print(
                f'progress={index}/{len(files)} passed={passed} '
                f'unresolved={len(unresolved)} normalized_rows={normalized_rows}'
            )

    output = Path(output) if output else odds_dir.parent / 'odds_normalizer_archive_audit.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ['file', 'error_type', 'error', 'columns']
    with output.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unresolved)

    print('\n=== ODDS NORMALIZER ARCHIVE AUDIT ===')
    print(f'total_files={len(files)}')
    print(f'passed={passed}')
    print(f'unresolved={len(unresolved)}')
    print(f'normalized_rows={normalized_rows}')
    print(f'covered_bets={dict(bet_counts)}')
    print(f'unique_schemas={len(schema_counts)}')
    print(f'audit_csv={output}')

    if unresolved:
        print('\n=== FIRST UNRESOLVED CASES ===')
        for row in unresolved[:30]:
            print(f"{row['file']} | {row['error']}")
        raise SystemExit(2)

    print('ODDS_NORMALIZER_ARCHIVE=PASS')
    return {
        'total_files': len(files),
        'passed': passed,
        'unresolved': 0,
        'normalized_rows': normalized_rows,
        'covered_bets': dict(bet_counts),
        'unique_schemas': len(schema_counts),
        'audit_csv': str(output),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--odds-dir', type=Path, default=DEFAULT_ODDS_DIR)
    ap.add_argument('--output', type=Path, default=None)
    args = ap.parse_args()
    run(args.odds_dir, args.output)


if __name__ == '__main__':
    main()
