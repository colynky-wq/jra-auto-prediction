"""One-command strict-OOS historical all-bet research for the current Drive layout.

Expected Google Drive mount (default):
  /content/gdrive_live/MyDrive/code_output/
    github_actions_stage/production/production_history.parquet
    jra_data/historical_odds_2025/race_manifest_2025.csv
    jra_data/historical_odds_2025/races/*_odds.parquet
    jra_data/historical_odds_2025/races/*_horses.parquet
    jra_data/historical_odds_2025/payout_raw/*.html.gz

The current archive is not assumed to cover the entire calendar year. Before
model generation, the runner audits the authoritative race manifest against the
actual odds/horse/payout files and refuses a non-contiguous archive. The run
manifest records the exact first/last covered date and whether the archive is
full-year or only a chronological partial-year prefix.

The fast default rebuilds only the 2025 strict walk-forward OOS probabilities.
That model is selected on 2024 and refit only through 2024-12-31; the fixed-2025
model and its 212 rounds are never reused.

After OOS generation, predictions are restricted to races actually present in
the historical archive and enriched with deterministic JRA frame numbers using
the original card horse numbers from *_horses.parquet. This makes 枠連 scorable
even though the collector horse metadata does not have an explicit 枠 column.

The batch then writes the ordinary EV-threshold summary plus a race-level Top-K
portfolio grid for trigami control. Default mode is a 20-race smoke test.
``--full`` means every race currently available in the audited archive, not
necessarily the full 2025 calendar year. Forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import add_oos_frame_numbers as frame_enrich
import all_bet_oos_provenance as provenance
import audit_historical_archive_coverage as archive_coverage
import generate_all_bet_oos_from_portable_history as portable
import research_all_bet_portfolio_grid as portfolio_grid
import research_all_bet_retrospective as core
import run_all_bet_oos_historical_batch as batch

METHOD = 'drive-2025-strict-oos-all-bet-v4-20261021'
DEFAULT_DRIVE_ROOT = Path('/content/gdrive_live/MyDrive/code_output')
DEFAULT_YEARS = (2025,)
DEFAULT_SMOKE_RACES = 20


def resolve_paths(drive_root: Path, output_dir: Path | None = None) -> dict[str, Path]:
    root = Path(drive_root)
    historical_root = root / 'jra_data' / 'historical_odds_2025'
    output = Path(output_dir) if output_dir is not None else root / 'jra_data' / 'research_all_bet_oos_2025'
    batch_output = output / 'retrospective_2025'
    return {
        'drive_root': root,
        'history': root / 'github_actions_stage' / 'production' / 'production_history.parquet',
        'race_manifest': historical_root / 'race_manifest_2025.csv',
        'odds_dir': historical_root / 'races',
        'payout_dir': historical_root / 'payout_raw',
        'output_dir': output,
        'archive_audit': output / 'historical_archive_coverage.json',
        'predictions': output / 'strict_oos_predictions_2025.csv',
        'predictions_with_frames': output / 'strict_oos_predictions_2025_archive_with_frames.csv',
        'prediction_audit': output / 'strict_oos_prediction_audit.json',
        'frame_audit': output / 'strict_oos_frame_audit.json',
        'batch_output': batch_output,
        'portfolio_grid': batch_output / 'retrospective_oos_portfolio_grid.csv',
        'run_manifest': output / 'drive_2025_run_manifest.json',
    }


def validate_paths(paths: dict[str, Path]) -> None:
    missing = []
    for key in ('history', 'race_manifest'):
        if not paths[key].is_file():
            missing.append(str(paths[key]))
    for key in ('odds_dir', 'payout_dir'):
        if not paths[key].is_dir():
            missing.append(str(paths[key]))
    if missing:
        raise FileNotFoundError('required historical research inputs are missing:\n' + '\n'.join(missing))


def audit_archive(paths: dict[str, Path]) -> dict:
    report = archive_coverage.audit(paths['race_manifest'], paths['odds_dir'], paths['payout_dir'])
    paths['archive_audit'].parent.mkdir(parents=True, exist_ok=True)
    paths['archive_audit'].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if report['scope_label'] == 'non_contiguous_archive':
        raise RuntimeError(
            'historical archive has internal holes; do not run ROI research until coverage is repaired'
        )
    if int(report['contiguous_prefix_races']) <= 0:
        raise RuntimeError('historical archive has no complete contiguous race coverage')
    return report


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
    paths['prediction_audit'].write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    provenance.validate_rows(core.read_csv(paths['predictions']))
    return predictions, audit


def validate_reusable_predictions(path: Path):
    if not Path(path).is_file():
        raise FileNotFoundError(f'--reuse-predictions requested but file is missing: {path}')
    rows = core.read_csv(path)
    audit = provenance.validate_rows(rows)
    return rows, audit


def _race_id(row: dict) -> str:
    return str(core._field(row, core.PRED_ALIASES['race_id'])).strip()


def enrich_archive_prediction_frames(paths: dict[str, Path]) -> dict:
    """Restrict OOS rows to saved archive races, then attach deterministic JRA frames."""
    rows = core.read_csv(paths['predictions'])
    horse_files = frame_enrich.discover_horse_files(paths['odds_dir'])
    if not horse_files:
        raise RuntimeError('no *_horses.parquet files found for frame enrichment')

    archive_rows = [row for row in rows if _race_id(row) in horse_files]
    if not archive_rows:
        raise RuntimeError('no OOS prediction rows overlap the saved historical archive')

    enriched, race_audit = frame_enrich.enrich_rows(archive_rows, horse_files)
    frame_enrich.write_csv(paths['predictions_with_frames'], enriched)
    provenance.validate_rows(core.read_csv(paths['predictions_with_frames']))

    full_races = len({_race_id(row) for row in rows})
    archive_races = len({_race_id(row) for row in enriched})
    report = {
        'method': 'JRA_original_card_frame_allocation',
        'source_prediction_rows': len(rows),
        'source_prediction_races': full_races,
        'archive_prediction_rows': len(enriched),
        'archive_prediction_races': archive_races,
        'horse_metadata_races': len(horse_files),
        'excluded_prediction_races_without_archive_files': full_races - archive_races,
        'race_audit': race_audit,
        'forward_ledgers_touched': False,
    }
    paths['frame_audit'].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def run_portfolio_grid(paths: dict[str, Path], thresholds, stake_yen: int) -> list[dict]:
    samples = paths['batch_output'] / 'retrospective_oos_all_bet_samples.csv'
    if not samples.is_file():
        raise FileNotFoundError(f'batch sample file missing: {samples}')
    rows = portfolio_grid.evaluate_file_streaming(
        samples,
        thresholds=thresholds,
        top_ks=portfolio_grid.DEFAULT_TOP_K,
        stake_yen=stake_yen,
    )
    portfolio_grid.write_csv(paths['portfolio_grid'], rows)
    return rows


def run(drive_root: Path = DEFAULT_DRIVE_ROOT, output_dir: Path | None = None,
        years=DEFAULT_YEARS, smoke_races: int = DEFAULT_SMOKE_RACES,
        full: bool = False, reuse_predictions: bool = False,
        max_rounds: int = 300, early_stopping_rounds: int = 30,
        thresholds=core.DEFAULT_THRESHOLDS, stake_yen: int = 100):
    paths = resolve_paths(drive_root, output_dir)
    validate_paths(paths)
    paths['output_dir'].mkdir(parents=True, exist_ok=True)
    archive_audit = audit_archive(paths)

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

    frame_stage = enrich_archive_prediction_frames(paths)

    max_races = None if full else int(smoke_races)
    if max_races is not None and max_races <= 0:
        raise ValueError('smoke_races must be positive unless --full is used')

    batch_result = batch.run(
        paths['predictions_with_frames'], paths['odds_dir'], paths['payout_dir'], paths['batch_output'],
        thresholds=thresholds, stake_yen=stake_yen, max_races=max_races,
    )
    portfolio_rows = run_portfolio_grid(paths, thresholds, stake_yen)

    manifest = {
        'method': METHOD,
        'mode': 'full_available_archive' if full else 'smoke',
        'full_calendar_year': bool(archive_audit['archive_covers_full_manifest']) if full else False,
        'smoke_races': None if full else max_races,
        'study_scope': {
            'scope_label': archive_audit['scope_label'],
            'manifest_races': archive_audit['manifest_races'],
            'contiguous_prefix_races': archive_audit['contiguous_prefix_races'],
            'first_covered_date': archive_audit['first_covered_date'],
            'last_contiguous_date': archive_audit['last_contiguous_date'],
            'first_covered_race_id': archive_audit['first_covered_race_id'],
            'last_contiguous_race_id': archive_audit['last_contiguous_race_id'],
            'first_missing_race_id': archive_audit['first_missing_race_id'],
            'archive_covers_full_manifest': archive_audit['archive_covers_full_manifest'],
        },
        'archive_coverage': archive_audit,
        'source_paths': {k: str(v) for k, v in paths.items()
                         if k in {'drive_root', 'history', 'race_manifest', 'odds_dir', 'payout_dir'}},
        'output_paths': {k: str(v) for k, v in paths.items()
                         if k in {'output_dir', 'archive_audit', 'predictions', 'predictions_with_frames',
                                  'prediction_audit', 'frame_audit', 'batch_output', 'portfolio_grid'}},
        'prediction_stage': prediction_stage,
        'frame_stage': frame_stage,
        'batch_manifest': batch_result['manifest'],
        'portfolio_grid': {
            'method': portfolio_grid.METHOD,
            'row_count': len(portfolio_rows),
            'top_k_values': list(portfolio_grid.DEFAULT_TOP_K),
            'trigami_definition': 'race has >=1 hit and total race return < total selected-ticket stake',
        },
        'strict_oos_verified': True,
        'fixed_2025_model_used': False,
        'fixed_2025_rounds_212_reused': False,
        'forward_ledgers_touched': False,
    }
    paths['run_manifest'].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {
        'paths': paths,
        'manifest': manifest,
        'summary': batch_result['summary'],
        'portfolio_grid': portfolio_rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drive-root', type=Path, default=DEFAULT_DRIVE_ROOT)
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--years', default='2025', help='Comma-separated OOS years; current quote archive needs 2025')
    parser.add_argument('--smoke-races', type=int, default=DEFAULT_SMOKE_RACES)
    parser.add_argument('--full', action='store_true',
                        help='Process every race currently present in the audited archive (not necessarily full calendar 2025)')
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
        drive_root=args.drive_root, output_dir=args.output_dir, years=years,
        smoke_races=args.smoke_races, full=args.full, reuse_predictions=args.reuse_predictions,
        max_rounds=args.max_rounds, early_stopping_rounds=args.early_stopping_rounds,
        thresholds=thresholds, stake_yen=args.stake_yen,
    )
    m = result['manifest']['batch_manifest']
    scope = result['manifest']['study_scope']
    print(
        f'{METHOD} mode={result["manifest"]["mode"]} '
        f'processed_races={m["processed_races"]} scored_samples={m["counts"].get("scored_samples", 0)}'
    )
    print(
        f'scope={scope["scope_label"]} {scope["first_covered_date"]}..{scope["last_contiguous_date"]} '
        f'covered_races={scope["contiguous_prefix_races"]}/{scope["manifest_races"]}'
    )
    print(
        f'frame_races={result["manifest"]["frame_stage"]["archive_prediction_races"]} '
        f'portfolio_grid_rows={result["manifest"]["portfolio_grid"]["row_count"]}'
    )
    print(f'output={result["paths"]["output_dir"]}')
    print('STRICT_OOS=TRUE; fixed_2025_model=FALSE; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
