"""Stream historical all-bet OOS research one race at a time.

This is the scalable execution path for the 2025 collector layout:
  races/<race_id>_odds.parquet
  payout_raw/<race_id>.html.gz

It deliberately does not concatenate all 3連単 quotes into one giant in-memory
DataFrame/CSV. Each race is normalized, settled and aggregated immediately.
Prediction provenance is validated globally before the first historical quote is
scored. Missing payout files/pools remain unresolved and are never losses.

Research-only outputs; forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
import math
from pathlib import Path
import re

import all_bet_oos_provenance as provenance
import normalize_historical_all_bet as odds_norm
import normalize_historical_payouts as payout_norm
import research_all_bet_retrospective as core

METHOD = 'retrospective-all-bet-stream-strict-oos-v1-20261021'


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
                    'candidate': 0, 'settled': 0, 'hits': 0,
                    'return_yen': 0.0, 'sum_ev': 0.0, 'sum_odds': 0.0,
                    'current_losing': 0, 'max_losing': 0, 'races': set(),
                }

    def add(self, sample: dict):
        bet = sample['bet_type']
        ev = float(sample['model_ev'])
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
            s['races'].add(sample['race_id'])
            if sample['hit']:
                s['hits'] += 1
                s['return_yen'] += (sample['payout_yen_per_100'] or 0) * self.stake_yen / 100
                s['current_losing'] = 0
            else:
                s['current_losing'] += 1
                s['max_losing'] = max(s['max_losing'], s['current_losing'])

    def rows(self, verified_prediction_rows: int, verified_prediction_races: int):
        out = []
        for threshold in self.thresholds:
            for bet in core.ALL_BETS:
                s = self.state[(threshold, bet)]
                stake = self.stake_yen * s['settled']
                returned = s['return_yen']
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
                    'max_losing_streak': s['max_losing'],
                    'mean_model_ev': s['sum_ev'] / s['candidate'] if s['candidate'] else None,
                    'mean_odds': s['sum_odds'] / s['candidate'] if s['candidate'] else None,
                    'evaluated_races': len(s['races']),
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


def run(predictions_path: Path, odds_dir: Path, payout_dir: Path, output_dir: Path,
        thresholds=core.DEFAULT_THRESHOLDS, stake_yen=100, max_races=None):
    predictions = core.read_csv(predictions_path)
    prov = provenance.validate_rows(predictions)
    predictions_by_race = group_predictions(predictions)

    odds_files = odds_norm.discover_odds_files(odds_dir)
    odds_files = sorted(odds_files, key=lambda p: (race_id_from_odds_path(p), p.name))
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
        'race_id', 'status', 'odds_file', 'payout_file', 'prediction_rows',
        'normalized_odds_rows', 'payout_rows', 'covered_bets', 'scored_samples',
        'settled_samples', 'parse_warnings',
    ]
    audit_writer = csv.DictWriter(audit_handle, fieldnames=audit_fields)
    audit_writer.writeheader()

    agg = StreamingSummary(thresholds, stake_yen)
    counts = defaultdict(int)
    processed_races = set()
    payout_coverage = defaultdict(int)

    try:
        for odds_path in odds_files:
            rid = race_id_from_odds_path(odds_path)
            pred_rows = predictions_by_race.get(rid)
            if not pred_rows:
                counts['skipped_no_prediction'] += 1
                audit_writer.writerow({
                    'race_id': rid, 'status': 'skip_no_oos_prediction', 'odds_file': odds_path.name,
                    'payout_file': '', 'prediction_rows': 0, 'normalized_odds_rows': 0,
                    'payout_rows': 0, 'covered_bets': '', 'scored_samples': 0,
                    'settled_samples': 0, 'parse_warnings': 0,
                })
                continue

            payout_path = payout_file_for(payout_dir, rid)
            if payout_path is None:
                counts['skipped_missing_payout_file'] += 1
                audit_writer.writerow({
                    'race_id': rid, 'status': 'skip_missing_payout_file', 'odds_file': odds_path.name,
                    'payout_file': '', 'prediction_rows': len(pred_rows), 'normalized_odds_rows': 0,
                    'payout_rows': 0, 'covered_bets': '', 'scored_samples': 0,
                    'settled_samples': 0, 'parse_warnings': 0,
                })
                continue

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

            settled = sum(bool(s['settled']) for s in samples)
            processed_races.add(rid)
            counts['processed'] += 1
            counts['scored_samples'] += len(samples)
            counts['settled_samples'] += settled
            audit_writer.writerow({
                'race_id': rid, 'status': 'processed', 'odds_file': odds_path.name,
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
        'odds_files_considered': len(odds_files),
        'processed_races': len(processed_races),
        'counts': dict(counts),
        'payout_coverage_races_by_bet': dict(payout_coverage),
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
                        help='Optional chronological smoke-test cap before a full batch')
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    result = run(args.predictions, args.odds_dir, args.payout_dir, args.output_dir,
                 thresholds, args.stake_yen, args.max_races)
    m = result['manifest']
    print(f'{METHOD} processed_races={m["processed_races"]} scored_samples={m["counts"].get("scored_samples", 0)}')
    print('STRICT_OOS=TRUE; streaming=TRUE; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
