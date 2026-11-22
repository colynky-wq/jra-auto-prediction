"""Strict provenance checks for retrospective OOS horse predictions.

A boolean label such as ``OOS=True`` is not sufficient evidence by itself.
For a prediction row to be marked verified out-of-sample, the row must carry
an explicit race date and the end date of the data used to fit/select the
model, and the training/selection end date must be strictly earlier than the
race date.

This module is research-only and never writes forward ledgers.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

RACE_DATE_ALIASES = ('race_date', 'date', '日付', 'レース日')
TRAIN_END_ALIASES = (
    'train_end_date', 'training_end_date', 'model_train_end_date',
    '学習終了日', '学習データ終了日', 'モデル学習終了日',
)
MODEL_ID_ALIASES = ('model_id', 'model_name', 'モデルID', 'モデル名')
FOLD_ID_ALIASES = ('fold_id', 'fold', 'OOS_fold', '検証fold')


def _field(row: dict, aliases, required=True):
    for name in aliases:
        value = row.get(name)
        if value is not None and str(value).strip() != '':
            return value
    if required:
        raise ValueError('missing provenance field: ' + '/'.join(aliases))
    return None


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError('empty provenance date')
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError as exc:
        raise ValueError(f'invalid provenance date: {value!r}') from exc


def validate_row(row: dict) -> dict:
    """Validate one prediction row and return normalized provenance metadata."""
    race_date = parse_date(_field(row, RACE_DATE_ALIASES))
    train_end = parse_date(_field(row, TRAIN_END_ALIASES))
    model_id = str(_field(row, MODEL_ID_ALIASES)).strip()
    fold_id = _field(row, FOLD_ID_ALIASES, required=False)

    if train_end >= race_date:
        raise ValueError(
            'OOS provenance violation: train_end_date must be earlier than race_date '
            f'(model={model_id}, train_end={train_end.isoformat()}, race_date={race_date.isoformat()})'
        )

    return {
        'race_date': race_date.isoformat(),
        'train_end_date': train_end.isoformat(),
        'model_id': model_id,
        'fold_id': None if fold_id is None else str(fold_id).strip(),
        'oos_verified': True,
    }


def validate_rows(rows: Iterable[dict]) -> dict:
    """Validate all rows and enforce consistent provenance within each race."""
    rows = list(rows)
    if not rows:
        raise ValueError('no prediction rows supplied for OOS provenance validation')

    by_race = {}
    verified = []
    for row in rows:
        rid = str(row.get('race_id') or row.get('raceID') or row.get('レースID') or '').strip()
        if not rid:
            raise ValueError('missing race id in provenance row')
        meta = validate_row(row)
        signature = (meta['race_date'], meta['train_end_date'], meta['model_id'], meta['fold_id'])
        old = by_race.get(rid)
        if old is not None and old != signature:
            raise ValueError(f'inconsistent OOS provenance within race {rid}')
        by_race[rid] = signature
        verified.append(meta)

    return {
        'verified': True,
        'row_count': len(rows),
        'race_count': len(by_race),
        'races': {
            rid: {
                'race_date': signature[0],
                'train_end_date': signature[1],
                'model_id': signature[2],
                'fold_id': signature[3],
            }
            for rid, signature in by_race.items()
        },
    }


def has_strict_provenance(row: dict) -> bool:
    """Return True only when all strict provenance fields are present."""
    try:
        _field(row, RACE_DATE_ALIASES)
        _field(row, TRAIN_END_ALIASES)
        _field(row, MODEL_ID_ALIASES)
    except ValueError:
        return False
    return True
