"""Quality gate for retrospective strict-OOS all-bet research outputs.

This validator does not judge profitability. It only answers whether a run is
technically eligible to be interpreted: strict OOS provenance was verified,
forward ledgers were untouched, historical archive scope is explicit and
contiguous, real historical quotes were scored and settled, race-level losing
streak/trigami semantics are active, the Top-K portfolio grid was generated,
and (for a full available-archive run) all eight standard pools have coverage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import research_all_bet_retrospective as core


class ResearchRunValidationError(RuntimeError):
    pass


def _positive_int(value, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchRunValidationError(f'{label} is not an integer: {value!r}') from exc
    if number <= 0:
        raise ResearchRunValidationError(f'{label} must be positive, got {number}')
    return number


def _archive_scope(manifest: dict) -> dict:
    archive = manifest.get('archive_coverage'); study = manifest.get('study_scope')
    if not isinstance(archive, dict): raise ResearchRunValidationError('archive_coverage is missing')
    if not isinstance(study, dict): raise ResearchRunValidationError('study_scope is missing')
    label = str(archive.get('scope_label') or study.get('scope_label') or '').strip()
    if label not in {'full_manifest', 'contiguous_partial_year_prefix'}:
        raise ResearchRunValidationError(f'archive scope is not an accepted contiguous scope: {label!r}')
    if archive.get('contiguous_prefix_is_hole_free') is not True:
        raise ResearchRunValidationError('historical archive is not a hole-free contiguous prefix')
    if int(archive.get('internal_hole_count', 0) or 0) != 0:
        raise ResearchRunValidationError('historical archive contains internal holes')
    if int(archive.get('covered_after_first_gap_count', 0) or 0) != 0:
        raise ResearchRunValidationError('historical archive contains races after the first gap')
    covered = _positive_int(archive.get('contiguous_prefix_races'), 'contiguous_prefix_races')
    manifest_races = _positive_int(archive.get('manifest_races'), 'manifest_races')
    if covered > manifest_races: raise ResearchRunValidationError('contiguous_prefix_races exceeds manifest_races')
    first_date = archive.get('first_covered_date') or study.get('first_covered_date')
    last_date = archive.get('last_contiguous_date') or study.get('last_contiguous_date')
    if not first_date or not last_date: raise ResearchRunValidationError('archive first/last covered date is missing')
    calendar_full = bool(archive.get('archive_covers_full_manifest'))
    if calendar_full != bool(manifest.get('full_calendar_year', False)):
        raise ResearchRunValidationError('full_calendar_year flag disagrees with archive coverage audit')
    if calendar_full and covered != manifest_races:
        raise ResearchRunValidationError('full calendar-year flag set but manifest coverage is incomplete')
    if label == 'full_manifest' and not calendar_full:
        raise ResearchRunValidationError('scope_label full_manifest disagrees with archive full flag')
    if label == 'contiguous_partial_year_prefix' and calendar_full:
        raise ResearchRunValidationError('partial-year scope cannot be marked full calendar year')
    return {
        'scope_label': label, 'calendar_year_complete': calendar_full,
        'manifest_races': manifest_races, 'covered_races': covered,
        'first_covered_date': str(first_date), 'last_covered_date': str(last_date),
        'first_missing_race_id': archive.get('first_missing_race_id') or study.get('first_missing_race_id'),
    }


def _portfolio_gate(manifest: dict) -> dict:
    portfolio = manifest.get('portfolio_grid')
    if not isinstance(portfolio, dict):
        raise ResearchRunValidationError('portfolio_grid metadata is missing')
    row_count = _positive_int(portfolio.get('row_count'), 'portfolio_grid.row_count')
    top_k = portfolio.get('top_k_values')
    if not isinstance(top_k, list) or not top_k:
        raise ResearchRunValidationError('portfolio_grid top_k_values are missing')
    try:
        values = [int(x) for x in top_k]
    except (TypeError, ValueError) as exc:
        raise ResearchRunValidationError('portfolio_grid top_k_values are invalid') from exc
    if any(x <= 0 for x in values):
        raise ResearchRunValidationError('portfolio_grid top_k_values must be positive')
    definition = str(portfolio.get('trigami_definition') or '')
    if 'total race return' not in definition or 'total selected-ticket stake' not in definition:
        raise ResearchRunValidationError('portfolio_grid race-level trigami definition is missing')
    return {'row_count': row_count, 'top_k_values': values, 'trigami_definition': definition}


def validate_manifest(manifest: dict, *, require_all_bets: bool = False) -> dict:
    if not isinstance(manifest, dict): raise ResearchRunValidationError('run manifest must be a JSON object')
    if manifest.get('strict_oos_verified') is not True: raise ResearchRunValidationError('strict_oos_verified is not TRUE')
    if manifest.get('fixed_2025_model_used') is not False: raise ResearchRunValidationError('fixed 2025 model must not be used')
    if manifest.get('fixed_2025_rounds_212_reused') is not False: raise ResearchRunValidationError('fixed 2025 selected rounds must not be reused')
    if manifest.get('forward_ledgers_touched') is not False: raise ResearchRunValidationError('forward ledgers were touched or status is unknown')
    archive = _archive_scope(manifest)
    portfolio = _portfolio_gate(manifest)

    batch = manifest.get('batch_manifest')
    if not isinstance(batch, dict): raise ResearchRunValidationError('batch_manifest is missing')
    if batch.get('oos_scope_verified') is not True: raise ResearchRunValidationError('batch OOS scope was not verified')
    if batch.get('forward_ledgers_touched') is not False: raise ResearchRunValidationError('batch touched forward ledgers or status is unknown')
    if batch.get('losing_streak_unit') != 'race': raise ResearchRunValidationError('losing streak must be measured in chronological races')
    trigami_definition = str(batch.get('trigami_definition') or '').strip()
    if 'race_has_hit' not in trigami_definition or 'race_stake' not in trigami_definition:
        raise ResearchRunValidationError('race-level trigami definition is missing or incompatible')

    processed = _positive_int(batch.get('processed_races'), 'processed_races')
    counts = batch.get('counts') or {}
    scored = _positive_int(counts.get('scored_samples'), 'scored_samples')
    settled = _positive_int(counts.get('settled_samples'), 'settled_samples')
    if settled > scored: raise ResearchRunValidationError('settled_samples exceeds scored_samples')
    if processed > archive['covered_races']: raise ResearchRunValidationError('processed_races exceeds audited archive coverage')

    scored_by_bet = batch.get('scored_samples_by_bet') or {}; settled_by_bet = batch.get('settled_samples_by_bet') or {}
    missing_scored = [bet for bet in core.ALL_BETS if int(scored_by_bet.get(bet, 0) or 0) <= 0]
    missing_settled = [bet for bet in core.ALL_BETS if int(settled_by_bet.get(bet, 0) or 0) <= 0]
    if require_all_bets and missing_scored:
        raise ResearchRunValidationError('full available-archive run has no scored samples for: ' + ','.join(missing_scored))
    if require_all_bets and missing_settled:
        raise ResearchRunValidationError('full available-archive run has no settled samples for: ' + ','.join(missing_settled))

    frame_counts = {key: int(value) for key, value in counts.items() if str(key).startswith('frame_mapping_')}
    has_frame_source = any(value > 0 for value in frame_counts.values())
    if require_all_bets and not has_frame_source:
        raise ResearchRunValidationError('full available-archive run never established a usable horse->frame mapping for 枠連')

    return {
        'eligible_for_roi_interpretation': True, 'require_all_bets': bool(require_all_bets),
        'calendar_year_complete': archive['calendar_year_complete'], 'study_scope_label': archive['scope_label'],
        'study_first_date': archive['first_covered_date'], 'study_last_date': archive['last_covered_date'],
        'archive_covered_races': archive['covered_races'], 'archive_manifest_races': archive['manifest_races'],
        'first_missing_race_id': archive['first_missing_race_id'], 'processed_races': processed,
        'scored_samples': scored, 'settled_samples': settled, 'losing_streak_unit': 'race',
        'trigami_definition': trigami_definition, 'portfolio_grid_rows': portfolio['row_count'],
        'portfolio_top_k_values': portfolio['top_k_values'],
        'scored_bets_present': [bet for bet in core.ALL_BETS if bet not in missing_scored],
        'settled_bets_present': [bet for bet in core.ALL_BETS if bet not in missing_settled],
        'missing_scored_bets': missing_scored, 'missing_settled_bets': missing_settled,
        'frame_mapping_counts': frame_counts,
        'note': ('Technical eligibility only; profitability is not asserted. '
                 'calendar_year_complete=false means results apply only to the audited partial-year prefix.'),
    }


def validate_file(path: Path, *, require_all_bets: bool = False) -> dict:
    return validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')), require_all_bets=require_all_bets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--output-json', type=Path)
    args = parser.parse_args()
    report = validate_file(args.manifest, require_all_bets=args.full)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2)); print('QUALITY_GATE=PASS')


if __name__ == '__main__':
    main()
