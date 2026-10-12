from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from horse_profile_runtime import venue_from_race_id
from research_course_affinity import add_course_affinity_features


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
OUT_PATH = REPO / "reports" / "research_course_affinity_model.json"

EXTRA = ["同場適性", "同場回数c", "同場芝ダ適性", "同場芝ダ回数c"]
MAX_ROUNDS = 300
EARLY_STOPPING = 30
BOOTSTRAP_N = 2000
SEED = 20261012

PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "lambda_l2": 10.0,
    "verbosity": -1,
    "num_threads": 2,
    "seed": 20261009,
    "feature_fraction_seed": 20261009,
    "deterministic": True,
    "force_col_wise": True,
}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, dtype=float), -50.0, 50.0)))


def normalize_race_probability(raw: np.ndarray, race_ids: pd.Series) -> np.ndarray:
    x = pd.Series(np.asarray(raw, dtype=float), index=race_ids.index)
    sums = x.groupby(race_ids, sort=False).transform("sum")
    out = np.divide(x.to_numpy(), sums.to_numpy(), out=np.zeros(len(x), dtype=float), where=sums.to_numpy() > 0)
    return np.clip(out, 1e-12, 1.0)


def prepare() -> pd.DataFrame:
    d = pd.read_parquet(HISTORY_PATH)
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce")
    d = d.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    # Exact production 37-feature engine first.
    d = add_rating(d)
    d = add_features(d)

    # Four candidate course-affinity features are computed from prior races only.
    course = add_course_affinity_features(
        d[["レースID", "日付", "馬番", "馬キー", "芝ダ", "障害", "着順", "頭数"]].copy()
    )
    for col in EXTRA:
        d[col] = course[col].to_numpy()

    d["同場適性"] = pd.to_numeric(d["同場適性"], errors="coerce").fillna(0.5)
    d["同場芝ダ適性"] = pd.to_numeric(d["同場芝ダ適性"], errors="coerce").fillna(0.5)
    d["同場回数c"] = pd.to_numeric(d["同場回数c"], errors="coerce").fillna(0.0)
    d["同場芝ダ回数c"] = pd.to_numeric(d["同場芝ダ回数c"], errors="coerce").fillna(0.0)

    d["年"] = d["日付"].dt.year
    d["目的"] = (pd.to_numeric(d["着順"], errors="coerce") == 1).astype(int)
    d["競馬場"] = d["レースID"].map(venue_from_race_id)

    keep = (
        d["対象"].fillna(False).astype(bool)
        & d["年"].between(2014, 2025)
        & d["市場ロジット"].notna()
    )
    d = d.loc[keep].copy()

    # Only complete races with one winner are used for race-normalized evaluation.
    race_winners = d.groupby("レースID", sort=False)["目的"].transform("sum")
    d = d.loc[race_winners == 1].copy()

    if d.empty:
        raise RuntimeError("no eligible research rows")
    return d


def train_rounds(train: pd.DataFrame, valid: pd.DataFrame, features: list[str]) -> tuple[lgb.Booster, int]:
    train_set = lgb.Dataset(
        train[features].astype(np.float32),
        label=train["目的"].to_numpy(dtype=int),
        init_score=train["市場ロジット"].to_numpy(dtype=float),
        feature_name=features,
        free_raw_data=False,
    )
    valid_set = lgb.Dataset(
        valid[features].astype(np.float32),
        label=valid["目的"].to_numpy(dtype=int),
        init_score=valid["市場ロジット"].to_numpy(dtype=float),
        feature_name=features,
        reference=train_set,
        free_raw_data=False,
    )
    model = lgb.train(
        PARAMS,
        train_set,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[valid_set],
        valid_names=["2024"],
        callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)],
    )
    return model, int(model.best_iteration or MAX_ROUNDS)


def fit_final(train: pd.DataFrame, features: list[str], rounds: int) -> lgb.Booster:
    ds = lgb.Dataset(
        train[features].astype(np.float32),
        label=train["目的"].to_numpy(dtype=int),
        init_score=train["市場ロジット"].to_numpy(dtype=float),
        feature_name=features,
        free_raw_data=False,
    )
    return lgb.train(PARAMS, ds, num_boost_round=int(rounds))


def predict(model: lgb.Booster, df: pd.DataFrame, features: list[str]) -> np.ndarray:
    correction = model.predict(df[features].astype(np.float32), raw_score=True)
    pre = sigmoid(df["市場ロジット"].to_numpy(dtype=float) + correction)
    return normalize_race_probability(pre, df["レースID"].astype(str))


def metrics(df: pd.DataFrame, probability: np.ndarray) -> dict:
    y = df["目的"].to_numpy(dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), 1e-12, 1.0 - 1e-12)
    row_logloss = -np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    brier = np.mean((p - y) ** 2)

    tmp = pd.DataFrame(
        {
            "raceID": df["レースID"].astype(str).to_numpy(),
            "y": y,
            "p": p,
        },
        index=df.index,
    )
    winner = tmp.loc[tmp["y"] == 1.0]
    winner_nll = float(-np.log(winner["p"]).mean())
    top_idx = tmp.groupby("raceID", sort=False)["p"].idxmax()
    top1 = float(tmp.loc[top_idx, "y"].mean())

    return {
        "rows": int(len(df)),
        "races": int(df["レースID"].nunique()),
        "rowBinaryLogloss": round(float(row_logloss), 8),
        "rowBrier": round(float(brier), 8),
        "winnerNegativeLogProbability": round(winner_nll, 8),
        "top1WinRatePct": round(top1 * 100.0, 3),
    }


def paired_day_bootstrap(df: pd.DataFrame, base_p: np.ndarray, extended_p: np.ndarray) -> dict:
    y = df["目的"].to_numpy(dtype=float)
    base = np.clip(np.asarray(base_p, dtype=float), 1e-12, 1.0 - 1e-12)
    ext = np.clip(np.asarray(extended_p, dtype=float), 1e-12, 1.0 - 1e-12)
    base_loss = -(y * np.log(base) + (1.0 - y) * np.log(1.0 - base))
    ext_loss = -(y * np.log(ext) + (1.0 - y) * np.log(1.0 - ext))

    dates = df["日付"].dt.strftime("%Y-%m-%d").to_numpy()
    day_frame = pd.DataFrame({"date": dates, "improvement": base_loss - ext_loss})
    daily = day_frame.groupby("date", sort=False)["improvement"].mean().to_numpy(dtype=float)
    if len(daily) == 0:
        return {"days": 0, "meanImprovement": None, "ci95Low": None, "ci95High": None, "ciEntirelyAboveZero": False}

    rng = np.random.default_rng(SEED)
    sampled = rng.choice(daily, size=(BOOTSTRAP_N, len(daily)), replace=True).mean(axis=1)
    lo, hi = np.quantile(sampled, [0.025, 0.975])
    mean = float(daily.mean())
    return {
        "days": int(len(daily)),
        "bootstrapRuns": BOOTSTRAP_N,
        "meanImprovement": round(mean, 8),
        "ci95Low": round(float(lo), 8),
        "ci95High": round(float(hi), 8),
        "ciEntirelyAboveZero": bool(lo > 0.0),
    }


def feature_importance(model: lgb.Booster, features: list[str]) -> list[dict]:
    gain = model.feature_importance(importance_type="gain")
    total = float(gain.sum())
    rows = []
    for name, value in zip(features, gain):
        if name not in EXTRA:
            continue
        share = float(value / total * 100.0) if total > 0 else 0.0
        rows.append({"feature": name, "gainSharePct": round(share, 4)})
    rows.sort(key=lambda x: x["gainSharePct"], reverse=True)
    return rows


def main() -> None:
    d = prepare()
    train_2023 = d[d["年"] <= 2023].copy()
    valid_2024 = d[d["年"] == 2024].copy()
    final_train = d[d["年"] <= 2024].copy()
    eval_2025 = d[d["年"] == 2025].copy()

    if min(len(train_2023), len(valid_2024), len(eval_2025)) == 0:
        raise RuntimeError("one or more research splits are empty")

    base_features = list(ALLF)
    extended_features = list(ALLF) + EXTRA

    _, base_rounds = train_rounds(train_2023, valid_2024, base_features)
    _, extended_rounds = train_rounds(train_2023, valid_2024, extended_features)

    base_model = fit_final(final_train, base_features, base_rounds)
    extended_model = fit_final(final_train, extended_features, extended_rounds)

    market_p = eval_2025["市場確率"].to_numpy(dtype=float)
    base_p = predict(base_model, eval_2025, base_features)
    extended_p = predict(extended_model, eval_2025, extended_features)

    market_metrics = metrics(eval_2025, market_p)
    base_metrics = metrics(eval_2025, base_p)
    extended_metrics = metrics(eval_2025, extended_p)
    boot = paired_day_bootstrap(eval_2025, base_p, extended_p)

    delta = {
        "rowBinaryLoglossBaseMinusExtended": round(base_metrics["rowBinaryLogloss"] - extended_metrics["rowBinaryLogloss"], 8),
        "rowBrierBaseMinusExtended": round(base_metrics["rowBrier"] - extended_metrics["rowBrier"], 8),
        "winnerNllBaseMinusExtended": round(base_metrics["winnerNegativeLogProbability"] - extended_metrics["winnerNegativeLogProbability"], 8),
        "top1WinRateExtendedMinusBasePct": round(extended_metrics["top1WinRatePct"] - base_metrics["top1WinRatePct"], 3),
    }

    confirmation_positive = bool(
        delta["rowBinaryLoglossBaseMinusExtended"] > 0
        and delta["rowBrierBaseMinusExtended"] >= 0
        and delta["winnerNllBaseMinusExtended"] > 0
        and boot["ciEntirelyAboveZero"]
    )

    venue_coverage = float(eval_2025["競馬場"].notna().mean() * 100.0)
    prior_venue = float((eval_2025["同場回数c"] > 0).mean() * 100.0)
    prior_venue_surface = float((eval_2025["同場芝ダ回数c"] > 0).mean() * 100.0)

    payload = {
        "schemaVersion": 1,
        "generatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "researchOnly": True,
        "productionModelChanged": False,
        "purpose": "37特徴量の市場補正モデルに競馬場適性4特徴を追加したとき、同一プロトコルで2025成績が改善するか確認する。",
        "protocol": {
            "roundSelectionTrain": "2014-2023",
            "roundSelectionValidation": "2024",
            "finalTraining": "2014-2024",
            "confirmationEvaluation": "2025",
            "2026Used": False,
            "marketAsInitScore": True,
            "maxRounds": MAX_ROUNDS,
            "earlyStopping": EARLY_STOPPING,
            "note": "競馬場適性の探索段階ですでに2025の層別傾向を確認済みのため、2025を完全な未観測テストとは呼びません。本番採用判断は2026以降の前向き結果でも確認します。",
        },
        "featureSets": {
            "baseline37": base_features,
            "extended41": extended_features,
            "added": EXTRA,
        },
        "data": {
            "trainRowsThrough2023": int(len(train_2023)),
            "validationRows2024": int(len(valid_2024)),
            "finalTrainRowsThrough2024": int(len(final_train)),
            "evaluationRows2025": int(len(eval_2025)),
            "evaluationRaces2025": int(eval_2025["レースID"].nunique()),
            "venueParseCoveragePct2025": round(venue_coverage, 3),
            "hasPriorVenueRacePct2025": round(prior_venue, 3),
            "hasPriorVenueSurfaceRacePct2025": round(prior_venue_surface, 3),
        },
        "selectedRounds": {"baseline37": base_rounds, "extended41": extended_rounds},
        "metrics2025": {
            "market": market_metrics,
            "baseline37": base_metrics,
            "extended41": extended_metrics,
        },
        "delta": delta,
        "pairedDayBootstrap": boot,
        "addedFeatureGain": feature_importance(extended_model, extended_features),
        "confirmationPositive": confirmation_positive,
        "decision": (
            "research_candidate_continue_forward_validation"
            if confirmation_positive
            else "do_not_promote_based_on_current_test"
        ),
        "nextStep": (
            "本番37特徴量モデルは変更しない。改善が確認できても、2026以降の保存済み発走前予測で前向き比較してから昇格判断する。"
        ),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
