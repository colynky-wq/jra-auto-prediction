"""Fast fail-closed audit of every saved historical payout HTML file.

This intentionally does NOT load odds, predictions, LightGBM, or portfolio
research. It exists to surface all remaining payout-parser edge cases before the
expensive full retrospective run is attempted.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re

import payout_parse_rescue_v2 as parser

DEFAULT_PAYOUT_DIR = Path(
    '/content/gdrive_live/MyDrive/code_output/jra_data/historical_odds_2025/payout_raw'
)


def race_id(path: Path) -> str:
    match = re.search(r'(\d{12})', path.name)
    return match.group(1) if match else path.stem


def run(payout_dir: Path, output: Path | None = None):
    payout_dir = Path(payout_dir)
    files = sorted(list(payout_dir.glob('*.html.gz')) + list(payout_dir.glob('*.html')))
    if not files:
        raise ValueError(f'no payout HTML files found under {payout_dir}')

    unresolved = []
    passed = 0
    rescued_place = 0
    rescued_wide = 0
    coverage_counts: dict[int, int] = {}

    for index, path in enumerate(files, 1):
        rid = race_id(path)
        try:
            html = parser.base.read_saved_html(path)
            rows, audit = parser.parse_payout_html(html, rid, path.name, strict=True)
            passed += 1
            rescued_place += int(audit.get('rescued_place_segments') or 0)
            rescued_wide += int(audit.get('rescued_wide_segments') or 0)
            coverage = int(audit.get('covered_bet_count') or 0)
            coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        except Exception as exc:
            unresolved.append({
                'race_id': rid,
                'file': path.name,
                'error_type': type(exc).__name__,
                'error': str(exc),
            })

        if index % 250 == 0 or index == len(files):
            print(
                f'progress={index}/{len(files)} passed={passed} '
                f'unresolved={len(unresolved)} rescued_place={rescued_place} '
                f'rescued_wide={rescued_wide}'
            )

    output = Path(output) if output else payout_dir.parent / 'payout_parser_archive_audit.csv'
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ['race_id', 'file', 'error_type', 'error']
    with output.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(unresolved)

    print('\n=== PAYOUT PARSER ARCHIVE AUDIT ===')
    print(f'total_files={len(files)}')
    print(f'passed={passed}')
    print(f'unresolved={len(unresolved)}')
    print(f'rescued_place_segments={rescued_place}')
    print(f'rescued_wide_segments={rescued_wide}')
    print(f'coverage_counts={dict(sorted(coverage_counts.items()))}')
    print(f'audit_csv={output}')

    if unresolved:
        print('\n=== FIRST UNRESOLVED CASES ===')
        for row in unresolved[:30]:
            print(f"{row['race_id']} | {row['error']}")
        raise SystemExit(2)

    print('PAYOUT_PARSER_ARCHIVE=PASS')
    return {
        'total_files': len(files),
        'passed': passed,
        'unresolved': 0,
        'rescued_place_segments': rescued_place,
        'rescued_wide_segments': rescued_wide,
        'coverage_counts': coverage_counts,
        'audit_csv': str(output),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--payout-dir', type=Path, default=DEFAULT_PAYOUT_DIR)
    ap.add_argument('--output', type=Path, default=None)
    args = ap.parse_args()
    run(args.payout_dir, args.output)


if __name__ == '__main__':
    main()
