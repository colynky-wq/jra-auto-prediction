
# ============================================================
# KeibaAI GitHub Actions Production Verify
#
# GitHub Actions上で
#
# production_history.parquet
# feature_engine.py
# lightgbm_fixed_2025.txt
#
# だけを使って同じAI予測を再現できるか確認。
# ============================================================

import json
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

from feature_engine import (
    ALLF,
    add_rating,
    add_features,
)


HERE = Path(
    __file__
).resolve().parent


HISTORY_PATH = (
    HERE
    / "production_history.parquet"
)

MODEL_PATH = (
    HERE
    / "lightgbm_fixed_2025.txt"
)

EXPECTED_PATH = (
    HERE
    / "verify_expected.json"
)


# ============================================================
# 1. ファイル確認
# ============================================================

for path in [

    HISTORY_PATH,
    MODEL_PATH,
    EXPECTED_PATH,

]:

    if not path.exists():

        raise FileNotFoundError(
            path
        )


# ============================================================
# 2. 正解値読込
# ============================================================

with open(
    EXPECTED_PATH,
    "r",
    encoding="utf-8"
) as f:

    expected = json.load(
        f
    )


RACE_ID = str(
    expected[
        "race_id"
    ]
)


RACE_DATE = pd.Timestamp(
    expected[
        "race_date"
    ]
)


EXPECTED_FEATURES = list(
    expected[
        "feature_names"
    ]
)


if list(
    ALLF
) != EXPECTED_FEATURES:

    raise RuntimeError(
        "feature_engine.ALLF と"
        "検証用37特徴量順が一致しません"
    )


print(
    "=" * 100
)

print(
    "KeibaAI GitHub Actions Production Verify"
)

print(
    "=" * 100
)

print(
    "race:",
    RACE_ID
)

print(
    "date:",
    RACE_DATE.date()
)


# ============================================================
# 3. 23列履歴
# ============================================================

hist = pd.read_parquet(
    HISTORY_PATH
)


hist[
    "日付"
] = pd.to_datetime(
    hist[
        "日付"
    ],
    errors="coerce"
)


print(
    "history:",
    f"{len(hist):,}",
    "rows /",
    len(hist.columns),
    "columns"
)


if len(
    hist.columns
) != 23:

    raise RuntimeError(
        f"履歴が23列ではありません: {len(hist.columns)}"
    )


# ============================================================
# 4. 検証レース
# ============================================================

current = (

    hist.loc[
        hist[
            "レースID"
        ]
        .astype(str)
        .eq(
            RACE_ID
        )
    ]

    .copy()

    .sort_values(
        "馬番"
    )

    .reset_index(
        drop=True
    )
)


if len(
    current
) != int(
    expected[
        "horse_count"
    ]
):

    raise RuntimeError(
        "検証レースの頭数が一致しません"
    )


# ============================================================
# 5. 発走前状態へ戻す
# ============================================================

current[
    "着順"
] = np.nan

current[
    "タイム秒"
] = np.nan

current[
    "上がり3F"
] = np.nan

current[
    "初角"
] = np.nan

current[
    "出典"
] = (
    "当日予測_再現テスト"
)

current[
    "障害"
] = False


# ============================================================
# 6. テスト日より前だけ履歴
# ============================================================

history = (

    hist.loc[
        hist[
            "日付"
        ]
        .dt.normalize()
        <
        RACE_DATE.normalize()
    ]

    .copy()
)


work = pd.concat(
    [
        history,
        current,
    ],
    ignore_index=True,
)


work[
    "日付"
] = pd.to_datetime(
    work[
        "日付"
    ],
    errors="coerce"
)


work = (

    work

    .sort_values(
        [
            "日付",
            "レースID",
            "馬番",
        ]
    )

    .reset_index(
        drop=True
    )
)


print(
    "feature input:",
    f"{len(work):,}"
)


# ============================================================
# 7. 特徴量再計算
# ============================================================

print(
    "add_rating..."
)


work = add_rating(
    work
)


print(
    "add_features..."
)


work = add_features(
    work
)


# ============================================================
# 8. 検証レースだけ抽出
# ============================================================

replay = (

    work.loc[
        (
            work[
                "日付"
            ]
            .dt.normalize()
            .eq(
                RACE_DATE.normalize()
            )
        )

        &

        (
            work[
                "レースID"
            ]
            .astype(str)
            .eq(
                RACE_ID
            )
        )
    ]

    .copy()

    .sort_values(
        "馬番"
    )

    .reset_index(
        drop=True
    )
)


if len(
    replay
) != len(
    expected[
        "horses"
    ]
):

    raise RuntimeError(
        "再計算後頭数不一致"
    )


# ============================================================
# 9. 馬・特徴量正解値作成
# ============================================================

expected_feature_values = []

expected_market = []

expected_ai = []

expected_ratio = []

expected_ev = []

expected_candidates = []


for i, horse in enumerate(
    expected[
        "horses"
    ]
):

    actual_number = int(
        replay.iloc[
            i
        ][
            "馬番"
        ]
    )


    actual_name = str(
        replay.iloc[
            i
        ][
            "馬名"
        ]
    )


    if (
        actual_number
        !=
        int(
            horse[
                "number"
            ]
        )
    ):

        raise RuntimeError(
            f"馬番不一致: {i}"
        )


    if (
        actual_name
        !=
        str(
            horse[
                "name"
            ]
        )
    ):

        raise RuntimeError(
            f"馬名不一致: {i}"
        )


    expected_feature_values.append(
        horse[
            "features"
        ]
    )

    expected_market.append(
        horse[
            "market_probability"
        ]
    )

    expected_ai.append(
        horse[
            "ai_probability"
        ]
    )

    expected_ratio.append(
        horse[
            "ai_market_ratio"
        ]
    )

    expected_ev.append(
        horse[
            "expected_index"
        ]
    )

    expected_candidates.append(
        horse[
            "research_candidate"
        ]
    )


# ============================================================
# 10. 37特徴量比較
# ============================================================

expected_feature_values = np.asarray(
    expected_feature_values,
    dtype=float,
)


actual_feature_values = (

    replay[
        ALLF
    ]

    .to_numpy(
        dtype=float
    )
)


feature_max_diff = float(
    np.max(
        np.abs(
            expected_feature_values
            -
            actual_feature_values
        )
    )
)


print(
    "37 features max diff:",
    feature_max_diff
)


if (
    feature_max_diff
    >
    float(
        expected[
            "feature_tolerance"
        ]
    )
):

    raise RuntimeError(
        "37特徴量不一致: "
        f"{feature_max_diff}"
    )


# ============================================================
# 11. 固定LightGBM
# ============================================================

model = lgb.Booster(
    model_file=str(
        MODEL_PATH
    )
)


if list(
    model.feature_name()
) != list(
    ALLF
):

    raise RuntimeError(
        "固定LightGBMと"
        "feature_engineの37特徴量順が違います"
    )


X = (

    replay[
        ALLF
    ]

    .astype(
        np.float32
    )
)


if not np.isfinite(
    X.to_numpy()
).all():

    raise RuntimeError(
        "37特徴量にinf/-infがあります"
    )


tree = model.predict(
    X,
    raw_score=True,
    validate_features=True,
)


market_logit = (
    replay[
        "市場ロジット"
    ]
    .to_numpy(
        dtype=float
    )
)


final_logit = (
    market_logit
    +
    tree
)


p_raw = (

    1.0

    /

    (
        1.0

        +

        np.exp(
            -np.clip(
                final_logit,
                -50,
                50,
            )
        )
    )
)


ai = (
    p_raw
    /
    p_raw.sum()
)


market = (
    replay[
        "市場確率"
    ]
    .to_numpy(
        dtype=float
    )
)


ratio = (
    ai
    /
    market
)


odds = (
    replay[
        "単勝オッズ"
    ]
    .to_numpy(
        dtype=float
    )
)


ev = (
    ai
    *
    odds
    *
    100
)


# ============================================================
# 12. 数値差
# ============================================================

pred_tol = float(
    expected[
        "prediction_tolerance"
    ]
)


ev_tol = float(
    expected[
        "ev_tolerance"
    ]
)


market_diff = float(
    np.max(
        np.abs(
            market
            -
            np.asarray(
                expected_market,
                dtype=float
            )
        )
    )
)


ai_diff = float(
    np.max(
        np.abs(
            ai
            -
            np.asarray(
                expected_ai,
                dtype=float
            )
        )
    )
)


ratio_diff = float(
    np.max(
        np.abs(
            ratio
            -
            np.asarray(
                expected_ratio,
                dtype=float
            )
        )
    )
)


ev_diff = float(
    np.max(
        np.abs(
            ev
            -
            np.asarray(
                expected_ev,
                dtype=float
            )
        )
    )
)


actual_candidates = (

    (
        ratio
        >=
        1.20
    )

    &

    (
        ev
        >=
        100
    )
)


candidate_match = bool(
    np.array_equal(
        actual_candidates,
        np.asarray(
            expected_candidates,
            dtype=bool
        )
    )
)


print(
    "market max diff:",
    market_diff
)

print(
    "AI max diff:",
    ai_diff
)

print(
    "ratio max diff:",
    ratio_diff
)

print(
    "expected index max diff:",
    ev_diff
)

print(
    "candidate match:",
    candidate_match
)


# ============================================================
# 13. 判定
# ============================================================

if market_diff > pred_tol:

    raise RuntimeError(
        "市場確率不一致"
    )


if ai_diff > pred_tol:

    raise RuntimeError(
        "AI勝率不一致"
    )


if ratio_diff > pred_tol:

    raise RuntimeError(
        "AI÷市場不一致"
    )


if ev_diff > ev_tol:

    raise RuntimeError(
        "期待払戻指数不一致"
    )


if not candidate_match:

    raise RuntimeError(
        "研究候補不一致"
    )


# ============================================================
# 14. 成功
# ============================================================

print()
print(
    "=" * 100
)

print(
    "★★★★★ KeibaAI Production Verify SUCCESS"
)

print(
    "=" * 100
)

print(
    "23列履歴        : OK"
)

print(
    "37特徴量再計算  : OK"
)

print(
    "固定LightGBM    : OK"
)

print(
    "市場確率        : OK"
)

print(
    "AI勝率          : OK"
)

print(
    "AI÷市場         : OK"
)

print(
    "期待払戻指数    : OK"
)

print(
    "研究候補        : OK"
)

print(
    "=" * 100
)
