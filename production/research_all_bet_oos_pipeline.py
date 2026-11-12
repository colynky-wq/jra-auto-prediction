"""Strict entry point for retrospective OOS all-bet research.

This wrapper is the approved retrospective entry point. It refuses prediction
files unless every row proves that the model's training/selection data ended
strictly before the race date. A plain ``OOS=True`` flag is intentionally not
enough.

The underlying probability/settlement implementation lives in
research_all_bet_retrospective.py. Forward ledgers are never read or written.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import all_bet_oos_provenance as provenance
import research_all_bet_retrospective as core

METHOD = 'retrospective-all-bet-strict-oos-v1-20261021'


def score_quotes_strict(predictions: list[dict], odds: list[dict], payouts: list[dict]):
    audit = provenance.validate_rows(predictions)
    samples = core.score_quotes(predictions, odds, payouts)
    for row in samples:
        row['oos_scope_verified'] = True
        row['oos_verification_method'] = 'train_end_date<race_date'
    return samples, audit


def run(predictions_path: Path, odds_path: Path, payouts_path: Path, output_dir: Path,
        thresholds=core.DEFAULT_THRESHOLDS, stake_yen=100):
    predictions = core.read_csv(predictions_path)
    odds = core.read_csv(odds_path)
    payouts = core.read_csv(payouts_path)
    samples, audit = score_quotes_strict(predictions, odds, payouts)
    summary = core.summarise(samples, thresholds, stake_yen)
    for row in summary:
        row['method'] = METHOD
        row['oos_scope_verified'] = True
        row['verified_prediction_rows'] = audit['row_count']
        row['verified_prediction_races'] = audit['race_count']
    output_dir = Path(output_dir)
    core.write_csv(output_dir / 'retrospective_oos_all_bet_samples.csv', samples)
    core.write_csv(output_dir / 'retrospective_oos_all_bet_summary.csv', summary)
    core.write_csv(output_dir / 'retrospective_oos_ev_grid.csv', summary)
    return {'samples': samples, 'summary': summary, 'audit': audit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--odds', type=Path, required=True)
    parser.add_argument('--payouts', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stake-yen', type=int, default=100)
    parser.add_argument('--thresholds', default=','.join(str(x) for x in core.DEFAULT_THRESHOLDS))
    args = parser.parse_args()
    thresholds = tuple(float(x) for x in args.thresholds.split(',') if x.strip())
    result = run(args.predictions, args.odds, args.payouts, args.output_dir, thresholds, args.stake_yen)
    print(
        f'{METHOD} samples={len(result["samples"])} '
        f'verified_prediction_races={result["audit"]["race_count"]}'
    )
    print('STRICT_OOS=TRUE; forward ledgers untouched')


if __name__ == '__main__':
    main()
