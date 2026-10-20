"""Stream historical all-bet OOS research one race at a time.

This is the scalable execution path for the 2025 collector layout:
  races/<race_id>_odds.parquet
  races/<race_id>_horses.parquet
  payout_raw/<race_id>.html.gz

It deliberately does not concatenate all 3連単 quotes into one giant in-memory
DataFrame/CSV. Each race is normalized, settled and aggregated immediately.
Prediction provenance is validated globally before the first historical quote is
scored. Missing payout files/pools remain unresolved and are never losses.

Horse metadata is used to add the horse->frame map required for 枠連 when the
OOS prediction file itself has no 枠 column. If no complete frame map exists,
only 枠連 is naturally unscorable; the other seven pools remain usable.

The streaming summary is race-aware. A threshold means every settled quoted
combination in that pool with model EV >= threshold is staked equally. Besides
ticket ROI, the summary therefore reports race-level hit/profit rates, trigami
(hit but race return below total race stake), and consecutive losing races.

Research-only outputs; forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
from pathlib import Path
import re

import all_bet_oos_provenance as provenance
import normalize_historical_all_bet as odds_norm
import normalize_historical_payouts as payout_norm
import research_all_bet_retrospective as core

METHOD = 'retrospective-all-bet-stream-strict-oos-v3-20261021'
HORSE_NO_ALIASES = ('馬番', 'horse_no', 'horseNumber', 'number', '番号')
FRAME_ALIASES = ('枠', '枠番', 'frame', 'frame_no', 'frameNumber')


def race_id_from_odds_path(path: Path) -> str:
    match = re.search(r'(\d{12})_odds\.(?:parquet|csv)$', Path(path).name, re.I)
    if not match:
        raise ValueError(f'cannot infer race id from odds filename: {Path(path).name}')
    return match.group(1)


def group_predictions(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rid = str(core._field(row, core.PRED_ALIASES['race_id'])).strip()
        grouped[rid].append(row)
    return dict(grouped)


def payout_file_for(payout_dir: Path, race_id: str):
    payout_dir = Path(payout_dir)
    for name in (f'{race_id}.html.gz', f'{race_id}.html'):
        path = payout_dir / name
        if path.exists():
            return path
    return None


def horse_file_for(odds_path: Path, race_id: str):
    """Return the collector horse metadata file next to a race odds file."""
    root = Path(odds_path).parent
    for name in (f'{race_id}_horses.parquet', f'{race_id}_horses.csv'):
        path = root / name
        if path.exists():
            return path
    return None


def _first_present(row: dict, aliases):
    for name in aliases:
        if name in row:
            value = row.get(name)
            if value is not None and str(value).strip() not in {'', 'nan', 'None', '<NA>'}:
                return value
    return None


def frame_map_from_horse_rows(rows: list[dict]) -> dict[int, int]:
    """Build an authoritative horse-number -> frame-number map from collector rows."""
    mapping: dict[int, int] = {}
    for row in rows:
        horse_raw = _first_present(row, HORSE_NO_ALIASES)
        frame_raw = _first_present(row, FRAME_ALIASES)
        if horse_raw is None or frame_raw is None:
            continue
        horse = core._integer(horse_raw, high=18)
        frame = core._integer(frame_raw, high=8)
        old = mapping.get(horse)
        if old is not None and old != frame:
            raise ValueError(f'conflicting frame mapping for horse {horse}: {old} vs {frame}')
        mapping[horse] = frame
    return mapping


def _prediction_frames_complete(pred_rows: list[dict]) -> bool:
    if not pred_rows:
        return False
    for row in pred_rows:
        frame = _first_present(row, FRAME_ALIASES)
        if frame is None:
            return False
        try:
            core._integer(frame, high=8)
        except ValueError:
            return False
    return True


def enrich_prediction_frames(pred_rows: list[dict], horse_rows: list[dict] | None):
    """Return (copied_prediction_rows, status) with complete 枠 when available."""
    copied = [dict(row) for row in pred_rows]
    if _prediction_frames_complete(copied):
        return copied, 'prediction_frames'
    if not horse_rows:
        return copied, 'frame_unavailable'

    mapping = frame_map_from_horse_rows(horse_rows)
    if not mapping:
        return copied, 'frame_unavailable'

    for row in copied:
        horse = core._integer(core._field(row, core.PRED_ALIASES['horse_no']), high=18)
        if horse not in mapping:
            return [dict(r) for r in pred_rows], 'frame_incomplete'
        row['枠'] = mapping[horse]
    return copied, 'horse_file_frames'


def _max_losing_race_streak(portfolios: dict[str, dict], stake_yen: int) -> int:
    longest = current = 0
    for race in portfolios.values():
        stake = stake_yen * race['settled_bets']
        if stake > 0 and race['return_yen'] < stake:
            current += 1
            longest = max(longest, current)
        elif stake > 0:
            current = 0
    return longest


class StreamingSummary:
    def __init__(self, thresholds=core.DEFAULT_THRESHOLDS, stake_yen=100):
        self.thresholds = tuple(float(x) for x in thresholds)
        self.stake_yen = int(stake_yen)
        if self.stake_yen <= 0:
            raise ValueError('positive stake required')
        self.state = {}
        for threshold in self.thresholds:
            for bet in core.ALL_BETS:
                self.state[(threshold, bet)] = {
                    'candidate': 0,
                    'settled': 0,
                    'hits': 0,
                    'return_yen': 0.0,
                    'sum_ev': 0.0,
                    'sum_odds': 0.0,
                    # insertion order is chronological because run() processes
                    # races by provenance race_date then race_id.
                    'race_portfolios': {},
                }

    def add(self, sample: dict):
        bet = sample['bet_type']
        ev = float(sample['model_ev'])
        rid = str(sample['race_id'])
        for threshold in self.thresholds:
            if ev < threshold:
                continue
            s = self.state[(threshold, bet)]
            s['candidate'] += 1
            s['sum_ev'] += ev
            s['sum_odds'] += float(sample['odds'])
            if not sample['settled']:
                continue

            s['settled'] += 1
            race = s['race_portfolios'].setdefault(
                rid,
                {'settled_bets': 0, 'hits': 0, 'return_yen': 0.0},
            )
            race['settled_bets'] += 1
            if sample['hit']:
                s['hits'] += 1
                race['hits'] += 1
                returned = (sample['payout_yen_per_100'] or 0) * self.stake_yen / 100
                s['return_yen'] += returned
                race['return_yen'] += returned

    def rows(self, verified_prediction_rows: int, verified_prediction_races: int):
        out = []
        for threshold in self.thresholds:
            for bet in core.ALL_BETS:
                s = self.state[(threshold, bet)]
                stake = self.stake_yen * s['settled']
                returned = s['return_yen']
                portfolios = s['race_portfolios']
                settled_races = len(portfolios)
                hit_races = 0
                profitable_races = 0
                non_loss_races = 0
                trigami_races = 0
                for race in portfolios.values():
                    race_stake = self.stake_yen * race['settled_bets']
                    race_return = race['return_yen']
                    if race['hits'] > 0:
                        hit_races += 1
                        if race_return < race_stake:
                            trigami_races += 1
                    if race_return > race_stake:
                        profitable_races += 1
                    if race_return >= race_stake:
                        non_loss_races += 1

                max_losing_races = _max_losing_race_streak(portfolios, self.stake_yen)
                out.append({
                    'method': METHOD,
                    'bet_type': bet,
                    'ev_threshold': threshold,
                    'candidate_bets': s['candidate'],
                    'settled_bets': s['settled'],
                    'unresolved_bets': s['candidate'] - s['settled'],
                    'hits': s['hits'],
                    'hit_rate': s['hits'] / s['settled'] if s['settled'] else None,
                    'stake_yen': stake,
                    'return_yen': returned,
                    'profit_yen': returned - stake if s['settled'] else 0.0,
                    'roi_pct': returned / stake * 100 if stake else None,
                    # Retained column name for app/backward compatibility, but
                    # its unit is now explicitly race, not arbitrary ticket order.
                    'max_losing_streak': max_losing_races,
                    'max_losing_streak_unit': 'race',
                    'mean_model_ev': s['sum_ev'] / s['candidate'] if s['candidate'] else None,
                    'mean_odds': s['sum_odds'] / s['candidate'] if s['candidate'] else None,
                    'evaluated_races': settled_races,
                    'hit_races': hit_races,
                    'hit_race_rate': hit_races / settled_races if settled_races else None,
                    'profitable_races': profitable_races,
                    'profitable_race_rate': profitable_races / settled_races if settled_races else None,
                    'non_loss_races': non_loss_races,
                    'non_loss_race_rate': non_loss_races / settled_races if settled_races else None,
                    'trigami_races': trigami_races,
                    'trigami_rate_among_hit_races': trigami_races / hit_races if hit_races else None,
                    'mean_tickets_per_settled_race': s['settled'] / settled_races if settled_races else None,
                    'oos_scope_verified': True,
                    'verified_prediction_rows': verified_prediction_rows,
                    'verified_prediction_races': verified_prediction_races,
                })
        return out


def _write_rows(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _race_date_sort_key(path: Path, provenance_audit: dict):
    rid = race_id_from_odds_path(path)
    meta = provenance_audit.get('races', {}).get(rid, {})
    # Unscorable/no-prediction races (for example some excluded race types) are
    # placed after date-proven OOS races rather than falsely pretending race_id
    # lexical order is calendar order.
    race_date = str(meta.get('race_date') or '9999-12-31')
    return race_date, rid, path.name


def run(predictions_path: Path, odds_dir: Path, payout_dir: Path, output_dir: Path,
        thresholds=core.DEFAULT_THRESHOLDS, stake_yen=100, max_races=None):
    predictions = core.read_csv(predictions_path)
    prov = provenance.validate_rows(predictions)
    predictions_by_race = group_predictions(predictions)

    odds_files = odds_norm.discover_odds_files(odds_dir)
    odds_files = sorted(odds_files, key=lambda p: _race_date_sort_key(p, prov))
    if max_races is not None:
        odds_files = odds_files[:int(max_races)]
    if not odds_files:
        raise ValueError(f'no historical odds files discovered under {odds_dir}')

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / 'retrospective_oos_all_bet_samples.csv'
    audit_path = output_dir / 'retrospective_oos_race_audit.csv'
    summary_path = output_dir / 'retrospective_oos_all_bet_summary.csv'
    manifest_path = output_dir / 'retrospective_oos_manifest.json'

    sample_handle = sample_path.open('w', encoding='utf-8-sig', newline='')
    audit_handle = audit_path.open('w', encoding='utf-8-sig', newline='')
    sample_writer = None
    audit_fields = [
        'race_id', 'status', 'odds_file', 'horse_file', 'frame_mapping', 'payout_file',
        'prediction_rows', 'normalized_odds_rows', 'payout_rows', 'covered_bets',
        'scored_samples', 'settled_samples', 'parse_warnings',
    ]
    audit_writer = csv.DictWriter(audit_handle, fieldnames=audit_fields)
    audit_writer.writeheader()

    agg = StreamingSummary(thresholds, stake_yen)
    counts = defaultdict(int)
    processed_races = set()
    payout_coverage = defaultdict(int)
    scored_by_bet = defaultdict(int)
    settled_by_bet = defaultdict(int)

    try:
        for odds_path in odds_files:
            rid = race_id_from_odds_path(odds_path)
            pred_rows = predictions_by_race.get(rid)
            if not pred_rows:
                counts['skipped_no_prediction'] += 1
                audit_writer.writerow({
                    'race_id': rid, 'status': 'skip_no_oos_prediction', 'odds_file': odds_path.name,
                    'horse_file': '', 'frame_mapping': '', 'payout_file': '', 'prediction_rows': 0,
                    'normalized_odds_rows': 0, 'payout_rows': 0, 'covered_bets': '',
                    'scored_samples': 0, 'settled_samples': 0, 'parse_warnings': 0,
                })
                continue

            payout_path = payout_file_for(payout_dir, rid)
            if payout_path is None:
                counts['skipped_missing_payout_file'] += 1
                audit_writer.writerow({
                    'race_id': rid, 'status': 'skip_missing_payout_file', 'odds_file': odds_path.name,
                    'horse_file': '', 'frame_mapping': '', 'payout_file': '',
                    'prediction_rows': len(pred_rows), 'normalized_odds_rows': 0,
                    'payout_rows': 0, 'covered_bets': '', 'scored_samples': 0,
                    'settled_samples': 0, 'parse_warnings': 0,
                })
                continue

            horse_path = horse_file_for(odds_path, rid)
            horse_rows = odds_norm.read_table(horse_path) if horse_path is not None else None
            pred_rows, frame_status = enrich_prediction_frames(pred_rows, horse_rows)
            counts[f'frame_mapping_{frame_status}'] += 1

            normalized_odds = odds_norm.normalize_odds_rows(odds_norm.read_table(odds_path), odds_path.name)
            payout_rows, payout_audit = payout_norm.parse_payout_html(
                payout_norm.read_saved_html(payout_path), rid, payout_path.name, strict=True,
            )
            for bet in filter(None, payout_audit['covered_bets'].split(',')):
                payout_coverage[bet] += 1

            samples = core.score_quotes(pred_rows, normalized_odds, payout_rows)
            for sample in samples:
                sample['oos_scope_verified'] = True
                sample['oos_verification_method'] = 'train_end_date<race_date'
                if sample_writer is None:
                    sample_writer = csv.DictWriter(sample_handle, fieldnames=list(sample))
                    sample_writer.writeheader()
                sample_writer.writerow(sample)
                agg.add(sample)
                scored_by_bet[sample['bet_type']] += 1
                if sample['settled']:
                    settled_by_bet[sample['bet_type']] += 1

            settled = sum(bool(s['settled']) for s in samples)
            processed_races.add(rid)
            counts['processed'] += 1
            counts['scored_samples'] += len(samples)
            counts['settled_samples'] += settled
            audit_writer.writerow({
                'race_id': rid, 'status': 'processed', 'odds_file': odds_path.name,
                'horse_file': horse_path.name if horse_path else '', 'frame_mapping': frame_status,
                'payout_file': payout_path.name, 'prediction_rows': len(pred_rows),
                'normalized_odds_rows': len(normalized_odds), 'payout_rows': len(payout_rows),
                'covered_bets': payout_audit['covered_bets'], 'scored_samples': len(samples),
                'settled_samples': settled, 'parse_warnings': payout_audit['warning_count'],
            })
    finally:
        sample_handle.close()
        audit_handle.close()

    summary = agg.rows(prov['row_count'], prov['race_count'])
    _write_rows(summary_path, summary)
    manifest = {
        'method': METHOD,
        'oos_scope_verified': True,
        'prediction_audit': prov,
        'odds_file_order': 'prediction_race_date_then_race_id',
        'losing_streak_unit': 'race',
        'trigami_definition': 'race_has_hit_and_return_yen<race_stake_yen',
        'odds_files_considered': len(odds_files),
        'processed_races': len(processed_races),
        'counts': dict(counts),
        'payout_coverage_races_by_bet': dict(payout_coverage),
        'scored_samples_by_bet': {bet: scored_by_bet.get(bet, 0) for bet in core.ALL_BETS},
        'settled_samples_by_bet': {bet: settled_by_bet.get(bet, 0) for bet in core.ALL_BETS},
        'thresholds': list(map(float, thresholds)),
        'stake_yen': int(stake_yen),
        'forward_ledgers_touched': False,
        'outputs': {
            'samples': sample_path.name,
            'race_audit': audit_path.name,
            'summary': summary_path.name,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'summary': summary, 'manifest': manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--odds-dir', type=Path, required=True)
    parser.add_argument('--payout-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stake-yen', type=int, default=100)
    parser.add_argument('--thresholds', default=','.join(str(x) for x in core.DEFAULT_THRESHOLDS))
    parser.add_argument('--max-races', type=int, default=None,
                        help='Optional provenance-date-ordered smoke-test cap before a full batch')
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    result = run(args.predictions, args.odds_dir, args.payout_dir, args.output_dir,
                 thresholds, args.stake_yen, args.max_races)
    m = result['manifest']
    print(f'{METHOD} processed_races={m["processed_races"]} scored_samples={m["counts"].get("scored_samples", 0)}')
    print('STRICT_OOS=TRUE; streaming=TRUE; race_portfolio_metrics=TRUE; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
