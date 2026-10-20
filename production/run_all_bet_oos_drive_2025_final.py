"""Final one-command 2025 strict-OOS all-bet historical research runner.

Runs the full currently audited Drive archive, then adds calendar-day bootstrap
uncertainty and the strict technical quality gate.  This is the command to use
when moving from smoke/plumbing validation to the full available historical
study.  It never reads or writes forward ledgers.

Default behavior intentionally rebuilds the strict 2025 OOS prediction instead
of trusting a pre-existing CSV.  ``--reuse-predictions`` is allowed only because
the underlying runner re-validates date-proven provenance before use.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import research_all_bet_uncertainty as uncertainty
import run_all_bet_oos_drive_2025 as drive_run
import validate_all_bet_oos_run as quality_gate
import payout_parse_rescue as payout_rescue

METHOD = 'drive-2025-strict-oos-all-bet-final-v2-20261021'


def run(drive_root: Path = drive_run.DEFAULT_DRIVE_ROOT, output_dir: Path | None = None,
        reuse_predictions: bool = False, bootstrap_reps: int = uncertainty.DEFAULT_REPS,
        bootstrap_seed: int = uncertainty.DEFAULT_SEED, stake_yen: int = 100):
    # The historical batch intentionally uses a strict parser.  Keep that
    # fail-closed contract, but allow the rescue wrapper to resolve only those
    # concatenated 複勝 rows whose split is uniquely corroborated by other payout
    # pools on the same saved result page.
    drive_run.batch.payout_norm.parse_payout_html = payout_rescue.parse_payout_html

    base = drive_run.run(
        drive_root=drive_root,
        output_dir=output_dir,
        years=(2025,),
        full=True,
        reuse_predictions=bool(reuse_predictions),
        stake_yen=int(stake_yen),
    )
    paths = base['paths']
    samples = paths['batch_output'] / 'retrospective_oos_all_bet_samples.csv'
    uncertainty_path = paths['batch_output'] / 'retrospective_oos_day_bootstrap.csv'

    uncertainty_rows = uncertainty.run(
        samples=samples,
        race_manifest=paths['race_manifest'],
        output=uncertainty_path,
        stake_yen=int(stake_yen),
        reps=int(bootstrap_reps),
        seed=int(bootstrap_seed),
    )
    nonempty = [row for row in uncertainty_rows if int(row.get('selected_bets') or 0) > 0]
    if not nonempty:
        raise RuntimeError('day-bootstrap produced no evaluated portfolio configurations')
    max_days = max(int(row.get('day_count') or 0) for row in nonempty)
    if max_days <= 0:
        raise RuntimeError('day-bootstrap has no calendar-day coverage')

    manifest_path = paths['run_manifest']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['uncertainty'] = {
        'method': uncertainty.METHOD,
        'output': str(uncertainty_path),
        'row_count': len(uncertainty_rows),
        'nonempty_configurations': len(nonempty),
        'max_day_count': max_days,
        'bootstrap_unit': 'calendar_day',
        'bootstrap_reps': int(bootstrap_reps),
        'bootstrap_seed': int(bootstrap_seed),
        'interpretation': (
            '95% ROI interval is descriptive historical uncertainty; final saved odds are not '
            'equivalent to the live pre-race quote available to the app.'
        ),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    technical = quality_gate.validate_manifest(manifest, require_all_bets=True)
    final_report = {
        'method': METHOD,
        'quality_gate': 'PASS',
        'technical': technical,
        'uncertainty': manifest['uncertainty'],
        'full_available_archive_completed': True,
        'calendar_year_complete': bool(technical['calendar_year_complete']),
        'forward_ledgers_touched': False,
        'deployment_warning': (
            'Historical saved odds are research evidence only. Do not promote a ticket strategy to '
            'the live app until forward pre-race snapshots reproduce the advantage.'
        ),
    }
    report_path = paths['output_dir'] / 'full_research_quality_gate.json'
    report_path.write_text(json.dumps(final_report, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'base': base,
        'uncertainty_rows': uncertainty_rows,
        'final_report': final_report,
        'report_path': report_path,
        'uncertainty_path': uncertainty_path,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive-root', type=Path, default=drive_run.DEFAULT_DRIVE_ROOT)
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--reuse-predictions', action='store_true')
    parser.add_argument('--bootstrap-reps', type=int, default=uncertainty.DEFAULT_REPS)
    parser.add_argument('--bootstrap-seed', type=int, default=uncertainty.DEFAULT_SEED)
    parser.add_argument('--stake-yen', type=int, default=100)
    args = parser.parse_args()
    result = run(
        drive_root=args.drive_root,
        output_dir=args.output_dir,
        reuse_predictions=args.reuse_predictions,
        bootstrap_reps=args.bootstrap_reps,
        bootstrap_seed=args.bootstrap_seed,
        stake_yen=args.stake_yen,
    )
    report = result['final_report']
    tech = report['technical']
    print(
        f'{METHOD} QUALITY_GATE=PASS processed_races={tech["processed_races"]} '
        f'settled_samples={tech["settled_samples"]} day_bootstrap_configs='
        f'{report["uncertainty"]["nonempty_configurations"]}'
    )
    print(f'report={result["report_path"]}')
    print(f'uncertainty={result["uncertainty_path"]}')
    print('FULL_AVAILABLE_ARCHIVE=TRUE; FORWARD_LEDGERS_TOUCHED=FALSE')


if __name__ == '__main__':
    main()
