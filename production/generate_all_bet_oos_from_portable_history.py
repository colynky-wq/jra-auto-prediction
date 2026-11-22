"""Build leakage-safe 37-feature history and strict yearly OOS predictions.

This is the preferred retrospective entry point for the lightweight portable
``production_history.parquet`` archive.  The archive contains the raw 23-column
race history used by ``feature_engine.py`` rather than precomputed 37 features.

Research safeguards
-------------------
* raw history is sorted by 日付 -> レースID -> 馬番 before any lag/rating work;
* duplicate horse/race rows and a horse appearing twice on one date fail closed;
* rows after the last requested prediction year are discarded before features;
* missing 馬体重 is pre-imputed with a median computed strictly BEFORE the first
  OOS prediction year, preventing feature_engine's whole-frame median fallback
  from looking into OOS years;
* feature_engine's rating/lag/trailing features are then calculated in temporal
  order;
* yearly model selection/refit/prediction is delegated to
  generate_all_bet_oos_predictions, which enforces development -> prior-year
  selection -> refit -> next-year prediction windows.

The fixed 2025 model and its 212 rounds are never used.  Forward ledgers are
never read or written.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import feature_engine
import generate_all_bet_oos_predictions as oos

# Columns actually consumed by the portable feature engine. Extra archive
# columns are retained; exact raw column count is recorded in the audit rather
# than used as the safety condition.
REQUIRED_RAW_COLUMNS = (
    '日付', 'レースID', '馬番', '着順', 'タイム秒', '上がり3F', '頭数', '初角',
    '出典', '馬キー', '人気', '距離', '芝ダ', '騎手', '斤量', '馬体重', '増減',
    '調教師', 'クラス', '障害', '単勝オッズ', '年齢',
)
NUMERIC_COLUMNS = (
    '馬番', '着順', 'タイム秒', '上がり3F', '頭数', '初角', '人気', '距離',
    '斤量', '馬体重', '増減', '単勝オッズ', '年齢',
)


def _years(values: Iterable[int]) -> tuple[int, ...]:
    years = tuple(sorted({int(x) for x in values}))
    if not years:
        raise ValueError('at least one prediction year is required')
    return years


def validate_and_prepare_raw(frame: pd.DataFrame, years=(2023, 2024, 2025)):
    years = _years(years)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('portable history must be a non-empty DataFrame')
    missing = [c for c in REQUIRED_RAW_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f'portable history missing required raw columns ({len(missing)}): {missing}')

    d = frame.copy()
    raw_column_count = len(d.columns)
    d['日付'] = pd.to_datetime(d['日付'], errors='coerce').dt.normalize()
    if d['日付'].isna().any():
        raise ValueError(f'portable history contains {int(d["日付"].isna().sum())} invalid dates')
    d['レースID'] = d['レースID'].astype(str).str.strip()
    if d['レースID'].eq('').any():
        raise ValueError('portable history contains blank race ids')
    for column in NUMERIC_COLUMNS:
        d[column] = pd.to_numeric(d[column], errors='coerce')
    if d['馬番'].isna().any():
        raise ValueError('portable history contains non-numeric horse numbers')

    # Only history that could have been known by the end of the last requested
    # OOS year is needed. This also makes future appended 2026+ rows irrelevant.
    cutoff = pd.Timestamp(max(years), 12, 31)
    before = len(d)
    d = d.loc[d['日付'] <= cutoff].copy()
    if d.empty:
        raise ValueError(f'portable history has no rows through {cutoff.date()}')

    race_date_counts = d.groupby('レースID', sort=False)['日付'].nunique(dropna=False)
    bad_races = race_date_counts[race_date_counts != 1]
    if not bad_races.empty:
        raise ValueError(f'race ids mapped to multiple dates: {bad_races.index[:5].tolist()}')

    duplicate = d.duplicated(['レースID', '馬番'], keep=False)
    if duplicate.any():
        examples = d.loc[duplicate, ['レースID', '馬番']].head().to_dict('records')
        raise ValueError(f'duplicate race/horse rows: {examples}')

    # A JRA horse cannot legitimately run twice on the same day. Failing here
    # protects shift/cumsum lag features from ambiguous within-day ordering.
    same_day = d.duplicated(['馬キー', '日付'], keep=False) & d['馬キー'].notna()
    if same_day.any():
        examples = d.loc[same_day, ['馬キー', '日付', 'レースID']].head().to_dict('records')
        raise ValueError(f'horse appears multiple times on one date: {examples}')

    was_sorted = d.index.equals(
        d.sort_values(['日付', 'レースID', '馬番'], kind='stable').index
    )
    d = d.sort_values(['日付', 'レースID', '馬番'], kind='stable').reset_index(drop=True)

    first_oos = pd.Timestamp(min(years), 1, 1)
    historical_weights = d.loc[
        (d['日付'] < first_oos) & d['馬体重'].notna() & (d['馬体重'] > 0),
        '馬体重',
    ]
    if historical_weights.empty:
        raise ValueError(f'no positive body weights before first OOS year {min(years)}')
    body_weight_fill = float(historical_weights.median())
    if not np.isfinite(body_weight_fill) or body_weight_fill <= 0:
        raise ValueError('invalid pre-OOS body-weight median')
    missing_body_weight = int(d['馬体重'].isna().sum())
    d['馬体重'] = d['馬体重'].fillna(body_weight_fill)

    # The portable engine's operations are past-only provided the input is in
    # chronological order. add_rating records the pre-race rating before result
    # updates; add_features uses shifts/cumsums and same-day-excluding trailing
    # windows. The prefill above neutralises its only whole-frame median fallback.
    d = feature_engine.add_rating(d)
    d = feature_engine.add_features(d)

    missing_features = [c for c in oos.FEATURES_37 if c not in d.columns]
    if missing_features:
        raise RuntimeError(f'portable feature engine failed to create fixed 37 features: {missing_features}')
    if tuple(feature_engine.ALLF) != tuple(oos.FEATURES_37):
        raise RuntimeError('feature_engine.ALLF and OOS model feature order differ')

    predictable = (
        pd.to_numeric(d['市場ロジット'], errors='coerce').notna()
        & pd.to_numeric(d['馬番'], errors='coerce').notna()
    )
    audit = {
        'raw_rows_received': int(before),
        'raw_rows_through_last_oos_year': int(len(d)),
        'raw_column_count': int(raw_column_count),
        'required_raw_column_count': len(REQUIRED_RAW_COLUMNS),
        'first_date': d['日付'].min().date().isoformat(),
        'last_date': d['日付'].max().date().isoformat(),
        'race_count': int(d['レースID'].nunique()),
        'input_already_chronological': bool(was_sorted),
        'chronological_sort': ['日付', 'レースID', '馬番'],
        'first_oos_year': min(years),
        'last_oos_year': max(years),
        'body_weight_fill_source': f'dates_before_{min(years)}-01-01_only',
        'body_weight_fill_median': body_weight_fill,
        'body_weight_rows_imputed': missing_body_weight,
        'predictable_rows_after_features': int(predictable.sum()),
        'feature_count': len(oos.FEATURES_37),
        'feature_order_matches_portable_engine': True,
        'future_rows_discarded': int(before - len(d)),
        'forward_ledgers_touched': False,
    }
    return d, audit


def load_portable_history(path: Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    # Drive materialisation sometimes appends .bin to an otherwise valid
    # parquet filename, so also inspect the full name.
    if suffix in {'.parquet', '.pq'} or '.parquet' in path.name.lower():
        return pd.read_parquet(path)
    if suffix in {'.pkl', '.pickle'}:
        return pd.read_pickle(path)
    if suffix in {'.csv', '.txt'}:
        return pd.read_csv(path, low_memory=False)
    raise ValueError(f'unsupported portable history file: {path}')


def generate_from_history(frame: pd.DataFrame, years=(2023, 2024, 2025),
                          max_rounds=300, early_stopping_rounds=30):
    years = _years(years)
    featured, feature_audit = validate_and_prepare_raw(frame, years)
    predictions, fold_audits = oos.generate(
        featured, years=years, max_rounds=max_rounds,
        early_stopping_rounds=early_stopping_rounds,
    )
    if predictions.empty:
        raise RuntimeError('strict OOS generation returned no predictions')
    # Defense in depth: verify the chronology before anything is written.
    race_dates = pd.to_datetime(predictions['race_date'], errors='raise')
    train_ends = pd.to_datetime(predictions['train_end_date'], errors='raise')
    if not (train_ends < race_dates).all():
        raise RuntimeError('OOS chronology violation: train_end_date must precede every race_date')
    audit = {
        'method': 'portable-history-to-strict-oos-v1-20261021',
        'feature_build': feature_audit,
        'folds': fold_audits,
        'prediction_rows': int(len(predictions)),
        'prediction_races': int(predictions['レースID'].nunique()),
        'prediction_years': list(years),
        'strict_oos_verified': True,
        'fixed_2025_model_used': False,
        'fixed_2025_rounds_212_reused': False,
        'forward_ledgers_touched': False,
    }
    return predictions, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, required=True,
                        help='Portable production_history.parquet (raw 23-column history)')
    parser.add_argument('--years', default='2023,2024,2025')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-json', type=Path, required=True)
    parser.add_argument('--max-rounds', type=int, default=300)
    parser.add_argument('--early-stopping-rounds', type=int, default=30)
    args = parser.parse_args()

    years = _years(int(x) for x in args.years.split(',') if x.strip())
    raw = load_portable_history(args.history)
    predictions, audit = generate_from_history(
        raw, years, args.max_rounds, args.early_stopping_rounds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output, index=False, encoding='utf-8-sig')
    args.audit_json.parent.mkdir(parents=True, exist_ok=True)
    args.audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    print(
        f'rows={len(predictions)} races={predictions["レースID"].nunique()} '
        f'years={years} strict_oos=TRUE'
    )
    print('portable_history=TRUE; fixed_2025_model=FALSE; forward_ledgers_untouched=TRUE')


if __name__ == '__main__':
    main()
