"""Build deterministic yearly OOS folds for retrospective all-bet research.

The manifest is deliberately simple and strict:
- prediction year Y may use data only through Y-1-12-31;
- training and prediction windows must never overlap;
- folds are metadata only: this module does not train a model and does not
  write or modify any forward ledger;
- the 2025 retrospective fold therefore ends training at 2024-12-31.

This manifest is intended to be consumed by a later walk-forward prediction
builder so every historical prediction can carry auditable provenance.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_TRAIN_START = date(2014, 1, 1)
DEFAULT_YEARS = (2023, 2024, 2025)
SCHEMA_VERSION = 'yearly-strict-oos-v1-20261021'


@dataclass(frozen=True)
class Fold:
    fold_id: str
    schema_version: str
    train_start_date: str
    train_end_date: str
    predict_start_date: str
    predict_end_date: str
    model_id: str
    oos_rule: str

    def validate(self) -> 'Fold':
        train_start = date.fromisoformat(self.train_start_date)
        train_end = date.fromisoformat(self.train_end_date)
        pred_start = date.fromisoformat(self.predict_start_date)
        pred_end = date.fromisoformat(self.predict_end_date)
        if train_start > train_end:
            raise ValueError(f'invalid training range in {self.fold_id}')
        if pred_start > pred_end:
            raise ValueError(f'invalid prediction range in {self.fold_id}')
        if train_end >= pred_start:
            raise ValueError(f'OOS overlap in {self.fold_id}: train_end must be earlier than predict_start')
        return self


def yearly_fold(year: int, train_start: date = DEFAULT_TRAIN_START) -> Fold:
    year = int(year)
    if year <= train_start.year:
        raise ValueError('prediction year must be later than train_start year')
    train_end = date(year - 1, 12, 31)
    pred_start = date(year, 1, 1)
    pred_end = date(year, 12, 31)
    fold_id = f'oos_{year}_train_through_{year - 1}'
    return Fold(
        fold_id=fold_id,
        schema_version=SCHEMA_VERSION,
        train_start_date=train_start.isoformat(),
        train_end_date=train_end.isoformat(),
        predict_start_date=pred_start.isoformat(),
        predict_end_date=pred_end.isoformat(),
        model_id=f'LightGBM_walkforward_{year}_train_through_{year - 1}',
        oos_rule='train_end_date<predict_start_date',
    ).validate()


def build_folds(years: Iterable[int] = DEFAULT_YEARS, train_start: date = DEFAULT_TRAIN_START) -> list[Fold]:
    values = sorted({int(y) for y in years})
    if not values:
        raise ValueError('at least one prediction year is required')
    folds = [yearly_fold(year, train_start) for year in values]
    # Defensive cross-fold checks. Prediction windows must not overlap and the
    # manifest must be chronological/deterministic.
    for left, right in zip(folds, folds[1:]):
        if date.fromisoformat(left.predict_end_date) >= date.fromisoformat(right.predict_start_date):
            raise ValueError(f'prediction fold overlap: {left.fold_id}/{right.fold_id}')
    return folds


def fold_for_race_date(race_date: date, folds: Sequence[Fold]) -> Fold:
    matches = [
        fold for fold in folds
        if date.fromisoformat(fold.predict_start_date) <= race_date <= date.fromisoformat(fold.predict_end_date)
    ]
    if len(matches) != 1:
        raise ValueError(f'race date {race_date.isoformat()} must map to exactly one OOS fold; matches={len(matches)}')
    return matches[0]


def validate_model_assignment(fold: Fold, *, model_id: str, train_end_date: str) -> None:
    """Reject a model whose provenance is incompatible with the fold."""
    fold.validate()
    supplied_end = date.fromisoformat(str(train_end_date))
    required_end = date.fromisoformat(fold.train_end_date)
    pred_start = date.fromisoformat(fold.predict_start_date)
    if supplied_end >= pred_start:
        raise ValueError(
            f'OOS model assignment violation: model={model_id} train_end={supplied_end.isoformat()} '
            f'is not earlier than prediction start {pred_start.isoformat()}'
        )
    if supplied_end != required_end:
        raise ValueError(
            f'fold provenance mismatch: {fold.fold_id} requires train_end={required_end.isoformat()}, '
            f'got {supplied_end.isoformat()} for model={model_id}'
        )


def write_manifest(path: Path, folds: Sequence[Fold]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(fold.validate()) for fold in folds]
    fields = list(rows[0]) if rows else [field for field in Fold.__dataclass_fields__]
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--years', default=','.join(str(y) for y in DEFAULT_YEARS))
    parser.add_argument('--train-start', default=DEFAULT_TRAIN_START.isoformat())
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    years = tuple(int(token) for token in args.years.split(',') if token.strip())
    train_start = date.fromisoformat(args.train_start)
    folds = build_folds(years, train_start)
    write_manifest(args.output, folds)
    for fold in folds:
        print(
            f'{fold.fold_id}: train={fold.train_start_date}..{fold.train_end_date} '
            f'predict={fold.predict_start_date}..{fold.predict_end_date}'
        )
    print('STRICT_OOS_FOLD_MANIFEST=TRUE; forward ledgers untouched')


if __name__ == '__main__':
    main()
