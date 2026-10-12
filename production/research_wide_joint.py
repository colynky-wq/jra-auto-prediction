# ============================================================
# KeibaAI - Wide (both horses Top3) joint-probability research
#
# Purpose
# - Reuse the existing historical scraped dataset and the same 37 pre-race
#   features to build a first coherent wide probability model quickly.
# - Train a Top3 correction model without touching production models.
# - Convert horse-level Top3 marginals into a coherent distribution over
#   unordered Top3 sets, then derive P(A and B both finish Top3).
#
# Clean evaluation protocol
# - Train: <= 2023
# - Early stopping / model selection: 2024
# - Final untouched test: 2025
# - 2026 is never used.
#
# This script does NOT use wide odds. It evaluates probability quality only.
# Live wide odds will be collected separately before ROI / budget allocation.
# ============================================================

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from topk_runtime import market_top2_top3
from wide_joint_model import fit_top3_set_distribution


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
ARTIFACT_DIR = REPO / "wide_model_artifacts"

TRAIN_END = pd.Timestamp("2023-12-31")
VALID_START = pd.Timestamp("2024-01-01")
VALID_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2025-01-01")
TEST_END = pd.Timestamp("2025-12-31")

MAX_BOOST_ROUNDS = 300
EARLY_STOPPING_ROUNDS = 30
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
    "seed": SEED,
    "feature_fraction_seed": SEED,
    "deterministic": True,
    "force_col_wise": True,
}

BASE_COLS = [
    "レースID",
    "日付",
    "馬番",
    "馬名",
    "馬キー",
    "年齢",
    "斤量",
    "騎手",
    "調教師",
    "着順",
    "人気",
    "単勝オッズ",
    "出典",
    "芝ダ",
    "距離",
    "クラス",
    "障害",
    "馬体重",
    "増減",
    "タイム秒",
    "上がり3F",
    "初角",
    "頭数",
]


def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI WIDE JOINT MODEL RESEARCH: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, dtype=float), -50.0, 50.0)))


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=float), 1e-9, 1.0 - 1e-9)
    return np.log(q / (1.0 - q))


def binary_logloss(y: np.ndarray, p: np.ndarray) -> float:
    yy = np.asarray(y, dtype=float)
    pp = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    return float(-np.mean(yy * np.log(pp) + (1.0 - yy) * np.log(1.0 - pp)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    yy = np.asarray(y, dtype=float)
    pp = np.asarray(p, dtype=float)
    return float(np.mean((yy - pp) ** 2))


def compute_market_top3(frame: pd.DataFrame) -> np.ndarray:
    out = np.full(len(frame), np.nan, dtype=float)
    p_all = pd.to_numeric(frame["市場確率"], errors="coerce").to_numpy(dtype=float)

    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        q = p_all[pos]
        if len(q) < 3 or not np.isfinite(q).all() or (q <= 0).any():
            continue
        q = q / q.sum()
        _, top3 = market_top2_top3(q)
        out[pos] = top3

    return out


def normalize_race_total(frame: pd.DataFrame, raw_prob: np.ndarray, target_total: int) -> np.ndarray:
    p = np.clip(np.asarray(raw_prob, dtype=float), 1e-9, 1.0 - 1e-9)
    z = logit(p)
    out = np.full(len(frame), np.nan, dtype=float)

    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        zz = z[pos]
        n = len(pos)

        if n <= target_total:
            out[pos] = 1.0
            continue

        lo, hi = -30.0, 30.0
        total = float(target_total)
        for _ in range(80):
            mid = (lo + hi) / 2.0
            s = float(sigmoid(zz + mid).sum())
            if s < total:
                lo = mid
            else:
                hi = mid

        out[pos] = sigmoid(zz + (lo + hi) / 2.0)

    return out


def pair_capture(prob: np.ndarray, truth: np.ndarray, top_n: int) -> tuple[int, int]:
    order = np.argsort(-np.asarray(prob, dtype=float))
    y = np.asarray(truth, dtype=int)
    chosen = order[: min(top_n, len(order))]
    return int(y[chosen].sum()), int(y.sum())


def evaluate_joint(test: pd.DataFrame, ai_top3: np.ndarray) -> dict:
    market_pairs: list[float] = []
    ai_pairs: list[float] = []
    labels: list[int] = []

    races_used = 0
    races_skipped_tie_or_incomplete = 0
    market_fit_error_max = 0.0
    ai_fit_error_max = 0.0
    market_pair_sum: list[float] = []
    ai_pair_sum: list[float] = []

    market_capture3_hit = 0
    ai_capture3_hit = 0
    capture3_den = 0
    market_capture5_hit = 0
    ai_capture5_hit = 0
    capture5_den = 0

    ai_series = pd.Series(np.asarray(ai_top3, dtype=float), index=test.index)

    for _, g in test.groupby("レースID", sort=False):
        g = g.sort_values("馬番")
        n = len(g)
        if n < 3:
            continue

        actual_top3 = pd.to_numeric(g["着順"], errors="coerce").to_numpy(dtype=float) <= 3
        if int(actual_top3.sum()) != 3:
            races_skipped_tie_or_incomplete += 1
            continue

        market_target = g["market_top3"].to_numpy(dtype=float)
        ai_target = ai_series.loc[g.index].to_numpy(dtype=float)

        if (
            not np.isfinite(market_target).all()
            or not np.isfinite(ai_target).all()
            or abs(float(market_target.sum()) - 3.0) > 1e-6
            or abs(float(ai_target.sum()) - 3.0) > 1e-6
        ):
            continue

        market_joint = fit_top3_set_distribution(market_target)
        ai_joint = fit_top3_set_distribution(ai_target)

        market_fit_error_max = max(market_fit_error_max, float(market_joint["max_error"]))
        ai_fit_error_max = max(ai_fit_error_max, float(ai_joint["max_error"]))
        market_pair_sum.append(float(market_joint["pair_sum"]))
        ai_pair_sum.append(float(ai_joint["pair_sum"]))

        market_matrix = np.asarray(market_joint["pair"], dtype=float)
        ai_matrix = np.asarray(ai_joint["pair"], dtype=float)

        race_market: list[float] = []
        race_ai: list[float] = []
        race_y: list[int] = []

        for i in range(n - 1):
            for j in range(i + 1, n):
                race_market.append(float(market_matrix[i, j]))
                race_ai.append(float(ai_matrix[i, j]))
                race_y.append(int(actual_top3[i] and actual_top3[j]))

        market_pairs.extend(race_market)
        ai_pairs.extend(race_ai)
        labels.extend(race_y)

        mh, den = pair_capture(np.asarray(race_market), np.asarray(race_y), 3)
        ah, _ = pair_capture(np.asarray(race_ai), np.asarray(race_y), 3)
        market_capture3_hit += mh
        ai_capture3_hit += ah
        capture3_den += den

        mh5, den5 = pair_capture(np.asarray(race_market), np.asarray(race_y), 5)
        ah5, _ = pair_capture(np.asarray(race_ai), np.asarray(race_y), 5)
        market_capture5_hit += mh5
        ai_capture5_hit += ah5
        capture5_den += den5

        races_used += 1

    if not labels:
        fail("no 2025 pair labels were produced")

    y = np.asarray(labels, dtype=int)
    market = np.asarray(market_pairs, dtype=float)
    ai = np.asarray(ai_pairs, dtype=float)

    market_ll = binary_logloss(y, market)
    ai_ll = binary_logloss(y, ai)
    market_br = brier(y, market)
    ai_br = brier(y, ai)

    return {
        "racesUsed": int(races_used),
        "racesSkippedTieOrIncompleteTop3": int(races_skipped_tie_or_incomplete),
        "pairRows": int(len(y)),
        "actualWideHitRateAcrossAllPairs": float(y.mean()),
        "marketPairLogloss": market_ll,
        "aiPairLogloss": ai_ll,
        "pairLoglossImprovement": market_ll - ai_ll,
        "marketPairBrier": market_br,
        "aiPairBrier": ai_br,
        "pairBrierImprovement": market_br - ai_br,
        "marketTop3PairCapture": float(market_capture3_hit / capture3_den),
        "aiTop3PairCapture": float(ai_capture3_hit / capture3_den),
        "marketTop5PairCapture": float(market_capture5_hit / capture5_den),
        "aiTop5PairCapture": float(ai_capture5_hit / capture5_den),
        "marketMeanPairProbabilitySum": float(np.mean(market_pair_sum)),
        "aiMeanPairProbabilitySum": float(np.mean(ai_pair_sum)),
        "marketMaxMarginalFitError": float(market_fit_error_max),
        "aiMaxMarginalFitError": float(ai_fit_error_max),
    }


def main() -> None:
    print("=" * 110)
    print("KeibaAI Wide Joint Probability Research")
    print("JST:", datetime.now(JST).isoformat())
    print("train <= 2023 / validation 2024 / untouched test 2025")
    print("2026 DATA USED: NO")
    print("production models modified: NO")
    print("=" * 110)

    if not HISTORY_PATH.exists():
        fail(f"history not found: {HISTORY_PATH}")

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")

    if list(hist.columns) != BASE_COLS:
        fail("production_history.parquet is not the expected 23-column schema")

    work = hist.loc[hist["日付"] <= TEST_END].copy()
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)

    if (work["日付"].dt.year >= 2026).any():
        fail("2026 row entered research frame")

    print("source rows:", f"{len(hist):,}", "latest:", hist["日付"].max())
    print("research rows through 2025:", f"{len(work):,}")
    print("add_rating...")
    work = add_rating(work)
    print("add_features...")
    work = add_features(work)

    eligible = (
        work["対象"].fillna(False).astype(bool)
        & work["着順"].notna()
        & work["市場確率"].notna()
        & (~work["障害"].fillna(False).astype(bool))
        & (work["頭数"] >= 4)
    )
    d = work.loc[eligible].copy().reset_index(drop=True)

    if d.empty:
        fail("no eligible historical rows")
    if d[ALLF].isna().any().any():
        fail("37 features contain missing values")
    if not np.isfinite(d[ALLF].astype(float).to_numpy()).all():
        fail("37 features contain inf/-inf")

    d["target_top3"] = (d["着順"] <= 3).astype(int)
    print("computing market Top3 baseline...")
    d["market_top3"] = compute_market_top3(d)

    if d["market_top3"].isna().any():
        fail("market_top3 contains missing values")

    train = d.loc[d["日付"] <= TRAIN_END].copy().reset_index(drop=True)
    valid = d.loc[(d["日付"] >= VALID_START) & (d["日付"] <= VALID_END)].copy().reset_index(drop=True)
    test = d.loc[(d["日付"] >= TEST_START) & (d["日付"] <= TEST_END)].copy().reset_index(drop=True)

    print("train <=2023:", f"{len(train):,}", "rows /", train["レースID"].nunique(), "races")
    print("valid 2024 :", f"{len(valid):,}", "rows /", valid["レースID"].nunique(), "races")
    print("test 2025  :", f"{len(test):,}", "rows /", test["レースID"].nunique(), "races")

    if train.empty or valid.empty or test.empty:
        fail("train/valid/test split is empty")

    X_train = train[ALLF].astype(np.float32)
    X_valid = valid[ALLF].astype(np.float32)
    X_test = test[ALLF].astype(np.float32)

    y_train = train["target_top3"].astype(int).to_numpy()
    y_valid = valid["target_top3"].astype(int).to_numpy()
    y_test = test["target_top3"].astype(int).to_numpy()

    base_train = train["market_top3"].to_numpy(dtype=float)
    base_valid = valid["market_top3"].to_numpy(dtype=float)
    base_test = test["market_top3"].to_numpy(dtype=float)

    dtrain = lgb.Dataset(
        X_train,
        label=y_train,
        init_score=logit(base_train),
        feature_name=list(ALLF),
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        X_valid,
        label=y_valid,
        init_score=logit(base_valid),
        feature_name=list(ALLF),
        reference=dtrain,
        free_raw_data=False,
    )

    model = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=MAX_BOOST_ROUNDS,
        valid_sets=[dvalid],
        valid_names=["2024"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True),
            lgb.log_evaluation(50),
        ],
    )

    best_iteration = int(model.best_iteration or MAX_BOOST_ROUNDS)
    raw_test = model.predict(
        X_test,
        raw_score=True,
        num_iteration=best_iteration,
        validate_features=True,
    )
    ai_test_pre = sigmoid(logit(base_test) + raw_test)
    ai_test = normalize_race_total(test, ai_test_pre, 3)

    market_top3_ll = binary_logloss(y_test, base_test)
    ai_top3_ll = binary_logloss(y_test, ai_test)
    market_top3_br = brier(y_test, base_test)
    ai_top3_br = brier(y_test, ai_test)

    print("2025 horse Top3 market logloss:", market_top3_ll)
    print("2025 horse Top3 AI logloss    :", ai_top3_ll)
    print("2025 horse Top3 improvement   :", market_top3_ll - ai_top3_ll)

    joint_metrics = evaluate_joint(test, ai_test)

    print()
    print("Wide pair research (2025 untouched test)")
    for k, v in joint_metrics.items():
        print(k, "=", v)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True,
        "productionModelsModified": False,
        "data2026Used": False,
        "protocol": {
            "train": "<=2023-12-31",
            "earlyStopping": "2024-01-01..2024-12-31",
            "untouchedTest": "2025-01-01..2025-12-31",
        },
        "featureCount": len(ALLF),
        "features": list(ALLF),
        "bestIteration": best_iteration,
        "horseTop3": {
            "marketLogloss": market_top3_ll,
            "aiLogloss": ai_top3_ll,
            "loglossImprovement": market_top3_ll - ai_top3_ll,
            "marketBrier": market_top3_br,
            "aiBrier": ai_top3_br,
            "brierImprovement": market_top3_br - ai_top3_br,
            "meanAiProbabilitySumPerRace": float(
                pd.Series(ai_test, index=test.index).groupby(test["レースID"]).sum().mean()
            ),
        },
        "wideJoint": joint_metrics,
        "jointModel": (
            "Fixed-size Top3 set exponential family. Probabilities are assigned to every "
            "unordered 3-horse set; horse marginals are fitted to the AI Top3 probabilities. "
            "Wide probability is the exact pair-inclusion marginal."
        ),
        "roiNotEvaluated": True,
        "roiReason": "Historical 10-minute wide odds are not present in production_history.parquet.",
        "parameters": PARAMS,
    }

    (ARTIFACT_DIR / "wide_joint_research_2025.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        "# KeibaAI Wide Joint Probability Research",
        "",
        "既存の過去スクレイピングデータだけを使った研究です。2026年データは使用していません。",
        "",
        "## Split",
        "",
        "- 学習: 2023年まで",
        "- early stopping: 2024年",
        "- 完全に触れていないテスト: 2025年",
        "",
        "## 2025 results",
        "",
        "| 指標 | 市場 | AI | 改善 |",
        "|---|---:|---:|---:|",
        f"| Top3 horse logloss | {market_top3_ll:.8f} | {ai_top3_ll:.8f} | {market_top3_ll-ai_top3_ll:.8f} |",
        f"| Top3 horse Brier | {market_top3_br:.8f} | {ai_top3_br:.8f} | {market_top3_br-ai_top3_br:.8f} |",
        f"| Wide pair logloss | {joint_metrics['marketPairLogloss']:.8f} | {joint_metrics['aiPairLogloss']:.8f} | {joint_metrics['pairLoglossImprovement']:.8f} |",
        f"| Wide pair Brier | {joint_metrics['marketPairBrier']:.8f} | {joint_metrics['aiPairBrier']:.8f} | {joint_metrics['pairBrierImprovement']:.8f} |",
        f"| Top3 pair capture | {joint_metrics['marketTop3PairCapture']:.4%} | {joint_metrics['aiTop3PairCapture']:.4%} | {joint_metrics['aiTop3PairCapture']-joint_metrics['marketTop3PairCapture']:.4%} |",
        f"| Top5 pair capture | {joint_metrics['marketTop5PairCapture']:.4%} | {joint_metrics['aiTop5PairCapture']:.4%} | {joint_metrics['aiTop5PairCapture']-joint_metrics['marketTop5PairCapture']:.4%} |",
        "",
        "各レースのワイド組み合わせ確率の合計は理論上3です。",
        f"- 市場モデル平均: {joint_metrics['marketMeanPairProbabilitySum']:.12f}",
        f"- AIモデル平均: {joint_metrics['aiMeanPairProbabilitySum']:.12f}",
        "",
        "現段階では10分前ワイドオッズが過去データにないため、回収率は評価していません。",
        "次段階でライブの全券種オッズを保存し、期待値・予算配分を検証します。",
    ]
    (ARTIFACT_DIR / "wide_joint_research_2025.md").write_text(
        "\n".join(md) + "\n",
        encoding="utf-8",
    )

    model.save_model(str(ARTIFACT_DIR / "top3_research_train_to_2023_valid_2024.txt"))

    if joint_metrics["aiMaxMarginalFitError"] > 1e-6:
        fail("AI Top3 joint fitting error exceeded 1e-6")
    if abs(joint_metrics["aiMeanPairProbabilitySum"] - 3.0) > 1e-6:
        fail("AI wide pair probability sum is not 3")

    print()
    print("RESEARCH SUCCESS")
    print("artifact:", ARTIFACT_DIR)


if __name__ == "__main__":
    main()
