"""Generate leakage-safe yearly OOS horse win probabilities for all-bet research.

For prediction year Y this module uses only information available before Y:
1. development train: through Y-2-12-31
2. early-stopping selection: Y-1-01-01 .. Y-1-12-31
3. refit: all data through Y-1-12-31 using the selected round count
4. predict: Y-01-01 .. Y-12-31

Thus 2025 predictions are selected on 2024 and refit only through 2024-12-31.
The fixed-2025 model / its 212 rounds are intentionally NOT reused because
that choice saw 2025 and would contaminate earlier retrospective OOS research.

The model follows the existing market-logit-residual design. ``市場ロジット``
is supplied as LightGBM init_score during training and added back to the raw
tree residual before sigmoid at prediction time.

This file is research-only. It never writes forward ledgers.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import build_all_bet_oos_folds as fold_manifest

FEATURES_37 = (
    '1着率', 'log間隔', 'クラス値f', 'クラス差', '乗り替わり', '内枠先行', '出走数c',
    '前走上がり差f', '前走人気f', '前走着順率f', '前走秒差f', '同芝ダ回数c', '同芝ダ平均',
    '同距離帯回数c', '同距離帯平均', '増減f', '増減絶対', '市場ロジット', '平均着順', '年齢',
    '斤量', '斤量変化', '昇級', '枠位置', '点数差', '脚質初角', '芝ダ替わり', '調教師log数',
    '調教師勝率', '調教師複勝率', '距離変化', '降級', '頭数f', '馬体重f', '騎手log数',
    '騎手勝率', '騎手複勝率',
)

DATE_ALIASES = ('日付', 'date', 'race_date', 'レース日')
RACE_ID_ALIASES = ('レースID', 'race_id', 'raceID')
HORSE_NO_ALIASES = ('馬番', 'horse_no', 'horseNumber', 'number')
FINISH_ALIASES = ('着順', 'finish', 'finish_position', '着順数値')
FRAME_ALIASES = ('枠', '枠番', 'frame')

PARAMS = {
    'objective': 'binary',
    'metric': 'binary_logloss',
    'learning_rate': 0.03,
    'num_leaves': 15,
    'min_data_in_leaf': 200,
    'feature_fraction': 0.9,
    'lambda_l2': 10.0,
    'verbosity': -1,
    'num_threads': 2,
    'seed': 20261009,
    'feature_fraction_seed': 20261009,
    'deterministic': True,
    'force_col_wise': True,
}


@dataclass(frozen=True)
class WalkForwardWindow:
    prediction_year: int
    development_start: str
    development_end: str
    selection_start: str
    selection_end: str
    refit_start: str
    refit_end: str
    predict_start: str
    predict_end: str
    fold_id: str

    def validate(self) -> 'WalkForwardWindow':
        d0 = date.fromisoformat(self.development_start)
        d1 = date.fromisoformat(self.development_end)
        s0 = date.fromisoformat(self.selection_start)
        s1 = date.fromisoformat(self.selection_end)
        r0 = date.fromisoformat(self.refit_start)
        r1 = date.fromisoformat(self.refit_end)
        p0 = date.fromisoformat(self.predict_start)
        p1 = date.fromisoformat(self.predict_end)
        if not (d0 <= d1 < s0 <= s1 < p0 <= p1):
            raise ValueError(f'non-chronological walk-forward window: {self}')
        if r0 != d0 or r1 != s1:
            raise ValueError('refit must cover development + selection history through selection end')
        return self


def walkforward_window(year: int, train_start: date = fold_manifest.DEFAULT_TRAIN_START) -> WalkForwardWindow:
    year = int(year)
    if year - 2 < train_start.year:
        raise ValueError('prediction year leaves no prior selection year after train_start')
    fold = fold_manifest.yearly_fold(year, train_start)
    window = WalkForwardWindow(
        prediction_year=year,
        development_start=train_start.isoformat(),
        development_end=date(year - 2, 12, 31).isoformat(),
        selection_start=date(year - 1, 1, 1).isoformat(),
        selection_end=date(year - 1, 12, 31).isoformat(),
        refit_start=train_start.isoformat(),
        refit_end=date(year - 1, 12, 31).isoformat(),
        predict_start=date(year, 1, 1).isoformat(),
        predict_end=date(year, 12, 31).isoformat(),
        fold_id=fold.fold_id,
    )
    return window.validate()


def _find_column(frame: pd.DataFrame, aliases: Iterable[str], label: str) -> str:
    for name in aliases:
        if name in frame.columns:
            return name
    raise ValueError(f'missing {label} column; expected one of {tuple(aliases)}')


def inspect_schema(frame: pd.DataFrame) -> dict:
    missing = [name for name in FEATURES_37 if name not in frame.columns]
    if missing:
        raise ValueError(f'missing fixed-model features ({len(missing)}): {missing}')
    return {
        'date': _find_column(frame, DATE_ALIASES, 'date'),
        'race_id': _find_column(frame, RACE_ID_ALIASES, 'race id'),
        'horse_no': _find_column(frame, HORSE_NO_ALIASES, 'horse number'),
        'finish': _find_column(frame, FINISH_ALIASES, 'finish'),
        'frame': next((name for name in FRAME_ALIASES if name in frame.columns), None),
    }


def load_feature_frame(path: Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {'.pkl', '.pickle', '.bin'}:
        frame = pd.read_pickle(path)
    elif suffix in {'.parquet', '.pq'}:
        frame = pd.read_parquet(path)
    elif suffix in {'.csv', '.txt'}:
        frame = pd.read_csv(path, low_memory=False)
    else:
        raise ValueError(f'unsupported feature file extension: {path.suffix}')
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('feature file did not contain a non-empty pandas DataFrame')
    return frame


def prepare_frame(frame: pd.DataFrame, schema: dict) -> pd.DataFrame:
    result = frame.copy()
    result['_race_date'] = pd.to_datetime(result[schema['date']], errors='coerce').dt.normalize()
    result['_finish_numeric'] = pd.to_numeric(result[schema['finish']], errors='coerce')
    result['_target_win'] = (result['_finish_numeric'] == 1).astype('int8')
    result['_race_id'] = result[schema['race_id']].astype(str).str.strip()
    result['_horse_no'] = pd.to_numeric(result[schema['horse_no']], errors='coerce')
    result['_market_logit'] = pd.to_numeric(result['市場ロジット'], errors='coerce')
    bad = result['_race_date'].isna() | result['_horse_no'].isna() | result['_market_logit'].isna()
    # Rows without a numeric finish cannot train, but can theoretically be prediction rows.
    result['_trainable'] = (~bad) & result['_finish_numeric'].notna()
    result['_predictable'] = ~bad
    return result


def sigmoid_from_base_and_residual(base_logit, residual_raw):
    base = np.asarray(base_logit, dtype=float)
    residual = np.asarray(residual_raw, dtype=float)
    z = np.clip(base + residual, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-z))


def _slice_dates(frame: pd.DataFrame, start: str, end: str, *, trainable: bool) -> pd.DataFrame:
    mask = (
        (frame['_race_date'] >= pd.Timestamp(start))
        & (frame['_race_date'] <= pd.Timestamp(end))
        & frame['_predictable']
    )
    if trainable:
        mask &= frame['_trainable']
    return frame.loc[mask].copy()


def _lgb_module():
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError('LightGBM is required for real OOS generation; install lightgbm>=4.6') from exc
    return lgb


def _train_select_rounds(development: pd.DataFrame, selection: pd.DataFrame,
                         max_rounds: int, early_stopping_rounds: int) -> int:
    if development.empty or selection.empty:
        raise ValueError('development and selection samples must both be non-empty')
    lgb = _lgb_module()
    train_set = lgb.Dataset(
        development[list(FEATURES_37)],
        label=development['_target_win'].astype(int),
        init_score=development['_market_logit'].astype(float),
        feature_name=list(FEATURES_37),
        free_raw_data=False,
    )
    valid_set = lgb.Dataset(
        selection[list(FEATURES_37)],
        label=selection['_target_win'].astype(int),
        init_score=selection['_market_logit'].astype(float),
        reference=train_set,
        feature_name=list(FEATURES_37),
        free_raw_data=False,
    )
    booster = lgb.train(
        dict(PARAMS), train_set,
        num_boost_round=int(max_rounds),
        valid_sets=[valid_set], valid_names=['selection'],
        callbacks=[lgb.early_stopping(int(early_stopping_rounds), verbose=False)],
    )
    best = int(booster.best_iteration or 0)
    if best <= 0:
        raise RuntimeError('LightGBM did not produce a valid best_iteration')
    return best


def _refit(refit: pd.DataFrame, rounds: int):
    if refit.empty or rounds <= 0:
        raise ValueError('non-empty refit sample and positive rounds required')
    lgb = _lgb_module()
    dataset = lgb.Dataset(
        refit[list(FEATURES_37)],
        label=refit['_target_win'].astype(int),
        init_score=refit['_market_logit'].astype(float),
        feature_name=list(FEATURES_37),
        free_raw_data=False,
    )
    return lgb.train(dict(PARAMS), dataset, num_boost_round=int(rounds))


def generate_for_year(frame: pd.DataFrame, schema: dict, year: int,
                      max_rounds: int = 300, early_stopping_rounds: int = 30):
    window = walkforward_window(year)
    development = _slice_dates(frame, window.development_start, window.development_end, trainable=True)
    selection = _slice_dates(frame, window.selection_start, window.selection_end, trainable=True)
    refit = _slice_dates(frame, window.refit_start, window.refit_end, trainable=True)
    predict = _slice_dates(frame, window.predict_start, window.predict_end, trainable=False)
    if predict.empty:
        raise ValueError(f'no prediction rows for {year}')

    rounds = _train_select_rounds(development, selection, max_rounds, early_stopping_rounds)
    booster = _refit(refit, rounds)
    residual = booster.predict(predict[list(FEATURES_37)], raw_score=True)
    probabilities = sigmoid_from_base_and_residual(predict['_market_logit'].to_numpy(float), residual)

    out = pd.DataFrame({
        'レースID': predict['_race_id'].to_numpy(),
        '馬番': predict['_horse_no'].astype(int).to_numpy(),
        'AI勝率': probabilities,
        'race_date': predict['_race_date'].dt.strftime('%Y-%m-%d').to_numpy(),
        'train_end_date': window.refit_end,
        'selection_start_date': window.selection_start,
        'selection_end_date': window.selection_end,
        'model_id': f'LightGBM_walkforward_{year}_selected_on_{year - 1}_rounds_{rounds}',
        'fold_id': window.fold_id,
        'OOS': True,
        'selected_rounds': rounds,
    })
    if schema.get('frame'):
        out['枠'] = pd.to_numeric(predict[schema['frame']], errors='coerce').astype('Int64').to_numpy()
    return out, {
        **asdict(window),
        'selected_rounds': rounds,
        'development_rows': len(development),
        'selection_rows': len(selection),
        'refit_rows': len(refit),
        'prediction_rows': len(predict),
        'model_params': dict(PARAMS),
        'features': list(FEATURES_37),
    }


def generate(frame: pd.DataFrame, years=(2023, 2024, 2025), max_rounds=300, early_stopping_rounds=30):
    schema = inspect_schema(frame)
    prepared = prepare_frame(frame, schema)
    outputs = []
    audits = []
    for year in sorted({int(y) for y in years}):
        out, audit = generate_for_year(prepared, schema, year, max_rounds, early_stopping_rounds)
        outputs.append(out)
        audits.append(audit)
    combined = pd.concat(outputs, ignore_index=True)
    combined = combined.sort_values(['race_date', 'レースID', '馬番'], kind='stable').reset_index(drop=True)
    return combined, audits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features', type=Path, required=True)
    parser.add_argument('--years', default='2023,2024,2025')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-json', type=Path, required=True)
    parser.add_argument('--max-rounds', type=int, default=300)
    parser.add_argument('--early-stopping-rounds', type=int, default=30)
    args = parser.parse_args()

    years = tuple(int(x) for x in args.years.split(',') if x.strip())
    frame = load_feature_frame(args.features)
    predictions, audits = generate(frame, years, args.max_rounds, args.early_stopping_rounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output, index=False, encoding='utf-8-sig')
    args.audit_json.parent.mkdir(parents=True, exist_ok=True)
    args.audit_json.write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'rows={len(predictions)} races={predictions["レースID"].nunique()} years={years}')
    print('STRICT_WALK_FORWARD_OOS=TRUE; fixed-2025 model/212 rounds not reused; forward ledgers untouched')


if __name__ == '__main__':
    main()
