"""Quality gate for retrospective strict-OOS all-bet research outputs.

This validator does not judge profitability.  It only answers whether a run is
technically eligible to be interpreted: strict OOS provenance was verified,
forward ledgers were untouched, real historical quotes were scored and settled,
and (for a full run) every one of the eight standard JRA pools has settled
coverage.
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


def validate_manifest(manifest: dict, *, require_all_bets: bool = False) -> dict:
    if not isinstance(manifest, dict):
        raise ResearchRunValidationError('run manifest must be a JSON object')
    if manifest.get('strict_oos_verified') is not True:
        raise ResearchRunValidationError('strict_oos_verified is not TRUE')
    if manifest.get('fixed_2025_model_used') is not False:
        raise ResearchRunValidationError('fixed 2025 model must not be used')
    if manifest.get('fixed_2025_rounds_212_reused') is not False:
        raise ResearchRunValidationError('fixed 2025 selected rounds must not be reused')
    if manifest.get('forward_ledgers_touched') is not False:
        raise ResearchRunValidationError('forward ledgers were touched or status is unknown')

    batch = manifest.get('batch_manifest')
    if not isinstance(batch, dict):
        raise ResearchRunValidationError('batch_manifest is missing')
    if batch.get('oos_scope_verified') is not True:
        raise ResearchRunValidationError('batch OOS scope was not verified')
    if batch.get('forward_ledgers_touched') is not False:
        raise ResearchRunValidationError('batch touched forward ledgers or status is unknown')

    processed = _positive_int(batch.get('processed_races'), 'processed_races')
    counts = batch.get('counts') or {}
    scored = _positive_int(counts.get('scored_samples'), 'scored_samples')
    settled = _positive_int(counts.get('settled_samples'), 'settled_samples')
    if settled > scored:
        raise ResearchRunValidationError('settled_samples exceeds scored_samples')

    scored_by_bet = batch.get('scored_samples_by_bet') or {}
    settled_by_bet = batch.get('settled_samples_by_bet') or {}
    missing_scored = [bet for bet in core.ALL_BETS if int(scored_by_bet.get(bet, 0) or 0) <= 0]
    missing_settled = [bet for bet in core.ALL_BETS if int(settled_by_bet.get(bet, 0) or 0) <= 0]

    if require_all_bets and missing_scored:
        raise ResearchRunValidationError('full run has no scored samples for: ' + ','.join(missing_scored))
    if require_all_bets and missing_settled:
        raise ResearchRunValidationError('full run has no settled samples for: ' + ','.join(missing_settled))

    frame_counts = {
        key: int(value)
        for key, value in counts.items()
        if str(key).startswith('frame_mapping_')
    }
    has_frame_source = any(
        value > 0 for key, value in frame_counts.items()
        if key in {'frame_mapping_prediction_frames', 'frame_mapping_horse_file_frames'}
    )
    if require_all_bets and not has_frame_source:
        raise ResearchRunValidationError('full run never established a usable horse->frame mapping for 枠連')

    return {
        'eligible_for_roi_interpretation': True,
        'require_all_bets': bool(require_all_bets),
        'processed_races': processed,
        'scored_samples': scored,
        'settled_samples': settled,
        'scored_bets_present': [bet for bet in core.ALL_BETS if bet not in missing_scored],
        'settled_bets_present': [bet for bet in core.ALL_BETS if bet not in missing_settled],
        'missing_scored_bets': missing_scored,
        'missing_settled_bets': missing_settled,
        'frame_mapping_counts': frame_counts,
        'note': 'Technical eligibility only; profitability is not asserted.',
    }


def validate_file(path: Path, *, require_all_bets: bool = False) -> dict:
    manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_manifest(manifest, require_all_bets=require_all_bets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True,
                        help='drive_2025_run_manifest.json produced by run_all_bet_oos_drive_2025.py')
    parser.add_argument('--full', action='store_true',
                        help='Require settled coverage for all eight standard pools')
    parser.add_argument('--output-json', type=Path)
    args = parser.parse_args()
    report = validate_file(args.manifest, require_all_bets=args.full)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('QUALITY_GATE=PASS')


if __name__ == '__main__':
    main()
