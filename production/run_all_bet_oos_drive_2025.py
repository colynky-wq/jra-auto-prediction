"""One-command strict-OOS historical all-bet research for the current Drive layout.

Expected Google Drive mount (default):
  /content/gdrive_live/MyDrive/code_output/
    github_actions_stage/production/production_history.parquet
    jra_data/historical_odds_2025/races/*_odds.parquet
    jra_data/historical_odds_2025/races/*_horses.parquet
    jra_data/historical_odds_2025/payout_raw/*.html.gz

The current archive contains 2025 historical odds, so the fast default rebuilds
only the 2025 strict walk-forward OOS probabilities.  That model is selected on
2024 and refit only through 2024-12-31; the fixed-2025 model and its 212 rounds
are never reused.  The general OOS generator still supports 2023/2024/2025 via
``--years`` when matching historical quote archives are added later.

Default mode is a 20-race smoke test. Pass ``--full`` for all discovered 2025
historical odds files. Forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import all_bet_oos_provenance as provenance
import generate_all_bet_oos_from_portable_history as portable
import research_all_bet_retrospective as core
import run_all_bet_oos_historical_batch as batch

METHOD = 'drive-2025-strict-oos-all-bet-v2-20261021'
DEFAULT_DRIVE_ROOT = Path('/content/gdrive_live/MyDrive/code_output')
DEFAULT_YEARS = (2025,)
DEFAULT_SMOKE_RACES = 20


def resolve_paths(drive_root: Path, output_dir: Path | None = None) -> dict[str, Path]:
    root = Path(drive_root)
    output = Path(output_dir) if output_dir is not None else root / 'jra_data' / 'research_all_bet_oos_2025'
    return {
        'drive_root': root,
        'history': root / 'github_actions_stage' / 'production' / 'production_history.parquet',
        'odds_dir': root / 'jra_data' / 'historical_odds_2025' / 'races',
        'payout_dir': root / 'jra_data' / 'historical_odds_2025' / 'payout_raw',
        'output_dir': output,
        'predictions': output / 'strict_oos_predictions_2025.csv',
        'prediction_audit': output / 'strict_oos_prediction_audit.json',
        'batch_output': output / 'retrospective_2025',
        'run_manifest': output / 'drive_2025_run_manifest.json',
    }


def validate_paths(paths: dict[str, Path]) -> None:
    missing = []
    if not paths['history'].is_file():
        missing.append(str(paths['history']))
    if not paths['odds_dir'].is_dir():
        missing.append(str(paths['odds_dir']))
    if not paths['payout_dir'].is_dir():
        missing.append(str(paths['payout_dir']))
    if missing:
        raise FileNotFoundError('required historical research inputs are missing:\n' + '\n'.join(missing))


def write_predictions(predictions, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(path, index=False, encoding='utf-8-sig')


def generate_predictions(paths: dict[str, Path], years=DEFAULT_YEARS,
                         max_rounds: int = 300, early_stopping_rounds: int = 30):
    raw = portable.load_portable_history(paths['history'])
    predictions, audit = portable.generate_from_history(
        raw,
        years=tuple(int(y) for y in years),
        max_rounds=int(max_rounds),
        early_stopping_rounds=int(early_stopping_rounds),
    )
    write_predictions(predictions, paths['predictions'])
    paths['prediction_audit'].write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    provenance.validate_rows(core.read_csv(paths['predictions']))
    return predictions, audit


def validate_reusable_predictions(path: Path):
    if not Path(path).is_file():
        raise FileNotFoundError(f'--reuse-predictions requested but file is missing: {path}')
    rows = core.read_csv(path)
    audit = provenance.validate_rows(rows)
    return rows, audit


def run(drive_root: Path = DEFAULT_DRIVE_ROOT, output_dir: Path | None = None,
        years=DEFAULT_YEARS, smoke_races: int = DEFAULT_SMOKE_RACES,
        full: bool = False, reuse_predictions: bool = False,
        max_rounds: int = 300, early_stopping_rounds: int = 30,
        thresholds=core.DEFAULT_THRESHOLDS, stake_yen: int = 100):
    paths = resolve_paths(drive_root, output_dir)
    validate_paths(paths)
    paths['output_dir'].mkdir(parents=True, exist_ok=True)

    years = tuple(int(y) for y in years)
    if not years:
        raise ValueError('at least one prediction year is required')

    if reuse_predictions:
        _, prediction_audit = validate_reusable_predictions(paths['predictions'])
        prediction_stage = {
            'reused': True,
            'strict_oos_verified': True,
            'row_count': prediction_audit['row_count'],
            'race_count': prediction_audit['race_count'],
        }
    else:
        predictions, audit = generate_predictions(
            paths, years=years, max_rounds=max_rounds,
            early_stopping_rounds=early_stopping_rounds,
        )
        prediction_stage = {
            'reused': False,
            'strict_oos_verified': True,
            'prediction_rows': int(len(predictions)),
            'prediction_races': int(predictions['レースID'].nunique()),
            'prediction_years': list(years),
            'fixed_2025_model_used': False,
            'fixed_2025_rounds_212_reused': False,
            'feature_build': audit.get('feature_build'),
            'folds': audit.get('folds'),
        }

    max_races = None if full else int(smoke_races)
    if max_races is not None and max_races <= 0:
        raise ValueError('smoke_races must be positive unless --full is used')

    batch_result = batch.run(
        paths['predictions'],
        paths['odds_dir'],
        paths['payout_dir'],
        paths['batch_output'],
        thresholds=thresholds,
        stake_yen=stake_yen,
        max_races=max_races,
    )

    manifest = {
        'method': METHOD,
        'mode': 'full' if full else 'smoke',
        'smoke_races': None if full else max_races,
        'source_paths': {k: str(v) for k, v in paths.items()
                         if k in {'drive_root', 'history', 'odds_dir', 'payout_dir'}},
        'output_paths': {k: str(v) for k, v in paths.items()
                         if k in {'output_dir', 'predictions', 'prediction_audit', 'batch_output'}},
        'prediction_stage': prediction_stage,
        'batch_manifest': batch_result['manifest'],
        'strict_oos_verified': True,
        'fixed_2025_model_used': False,
        'fixed_2025_rounds_212_reused': False,
        'forward_ledgers_touched': False,
    }
    paths['run_manifest'].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return {'paths': paths, 'manifest': manifest, 'summary': batch_result['summary']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive-root', type=Path, default=DEFAULT_DRIVE_ROOT)
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--years', default='2025',
                        help='Comma-separated OOS years; current odds archive only needs 2025')
    parser.add_argument('--smoke-races', type=int, default=DEFAULT_SMOKE_RACES)
    parser.add_argument('--full', action='store_true', help='Process every discovered 2025 historical odds file')
    parser.add_argument('--reuse-predictions', action='store_true',
                        help='Reuse an existing prediction CSV only after strict provenance validation')
    parser.add_argument('--max-rounds', type=int, default=300)
    parser.add_argument('--early-stopping-rounds', type=int, default=30)
    parser.add_argument('--stake-yen', type=int, default=100)
    parser.add_argument('--thresholds', default=','.join(str(x) for x in core.DEFAULT_THRESHOLDS))
    args = parser.parse_args()

    years = tuple(int(x) for x in args.years.split(',') if x.strip())
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    result = run(
        drive_root=args.drive_root,
        output_dir=args.output_dir,
        years=years,
        smoke_races=args.smoke_races,
        full=args.full,
        reuse_predictions=args.reuse_predictions,
        max_rounds=args.max_rounds,
        early_stopping_rounds=args.early_stopping_rounds,
        thresholds=thresholds,
        stake_yen=args.stake_yen,
    )
    m = result['manifest']['batch_manifest']
    print(
        f'{METHOD} mode={result["manifest"]["mode"]} '
        f'processed_races={m["processed_races"]} '
        f'scored_samples={m["counts"].get("scored_samples", 0)}'
    )
    print(f'output={result["paths"]["output_dir"]}')
    print('STRICT_OOS=TRUE; fixed_2025_model=FALSE; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
