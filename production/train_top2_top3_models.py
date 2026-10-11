# ============================================================
# KeibaAI - Top2 / Top3 probability model research trainer
#
# Purpose
# - Train separate probabilities for finishing 2nd-or-better and
#   3rd-or-better while keeping the existing fixed win model untouched.
# - Use only 2014-2025 data. 2026 is never used for model fitting,
#   early stopping, threshold selection, or model selection.
# - Reuse the same 37 pre-race features as the fixed win model.
# - Mirror the current fixed-model protocol:
#     train 2014-2024 -> validate 2025 -> choose boosting rounds
#     final train 2014-2025 with the chosen rounds.
#
# Market baseline
# - Historical win-market probabilities are converted to exact marginal
#   Top2 / Top3 probabilities under a Plackett-Luce ranking model.
# - Their logits are used as LightGBM init_score, so the trees learn a
#   correction to the market ranking baseline rather than starting from 0.
#
# Safety
# - Default is research-only. Candidate models are written to a temporary
#   artifact directory and do not replace the existing win model.
# - KEIBAAI_TOPK_APPLY=true copies the validated Top2 / Top3 models and
#   reports into production/. It still does NOT touch the win model.
# ============================================================

from __future__ import annotations

import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
ARTIFACT_DIR = REPO / "topk_model_artifacts"
REPORT_DIR = REPO / "reports" / "model_research"

APPLY = os.environ.get("KEIBAAI_TOPK_APPLY", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

TRAIN_END = pd.Timestamp("2024-12-31")
VALID_START = pd.Timestamp("2025-01-01")
VALID_END = pd.Timestamp("2025-12-31")
FINAL_END = pd.Timestamp("2025-12-31")

MAX_BOOST_ROUNDS = 300
EARLY_STOPPING_ROUNDS = 30
SEED = 20261009

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


# ============================================================
# Helpers
# ============================================================

def fail(message: str) -> None:
    print()
    print("=" * 110)
    print("KEIBAAI TOP2/TOP3 MODEL RESEARCH: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(q / (1.0 - q))


def binary_logloss(y: np.ndarray, p: np.ndarray) -> float:
    q = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    yy = np.asarray(y, dtype=float)
    return float(-np.mean(yy * np.log(q) + (1.0 - yy) * np.log(1.0 - q)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    yy = np.asarray(y, dtype=float)
    pp = np.asarray(p, dtype=float)
    return float(np.mean((yy - pp) ** 2))


def plackett_luce_top2_top3(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Exact Top2 / Top3 marginal probabilities from race win strengths.

    The input market probability is already normalized to sum to 1 within
    each race.  For each race we use the Plackett-Luce sequential ranking
    model and sum the probabilities of finishing in positions 1..2 / 1..3.
    """

    p_all = pd.to_numeric(df["市場確率"], errors="coerce").to_numpy(dtype=float)
    out2 = np.full(len(df), np.nan, dtype=float)
    out3 = np.full(len(df), np.nan, dtype=float)

    for _, pos in df.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        q = p_all[pos].astype(float)
        if len(q) < 3 or not np.isfinite(q).all() or q.sum() <= 0:
            continue

        q = q / q.sum()
        n = len(q)

        # Probability that target i finishes second:
        # sum_j P(j first) * P(i second | j first)
        first = q[:, None]                  # j x 1
        target = q[None, :]                 # 1 x i
        den2 = 1.0 - first
        term2 = np.divide(
            first * target,
            den2,
            out=np.zeros((n, n), dtype=float),
            where=den2 > 1e-15,
        )
        np.fill_diagonal(term2, 0.0)
        rank2 = term2.sum(axis=0)

        # Probability that target i finishes third:
        # sum_{j,k distinct from i} P(j first) * P(k second | j)
        #                            * P(i third | j,k)
        qj = q[:, None, None]
        qk = q[None, :, None]
        qi = q[None, None, :]
        den_after_j = 1.0 - qj
        den_after_jk = 1.0 - qj - qk

        term3 = np.divide(
            qj * qk * qi,
            den_after_j * den_after_jk,
            out=np.zeros((n, n, n), dtype=float),
            where=(den_after_j > 1e-15) & (den_after_jk > 1e-15),
        )

        idx = np.arange(n)
        j = idx[:, None, None]
        k = idx[None, :, None]
        i = idx[None, None, :]
        distinct = (j != k) & (j != i) & (k != i)
        term3 = np.where(distinct, term3, 0.0)
        rank3 = term3.sum(axis=(0, 1))

        out2[pos] = np.clip(q + rank2, 1e-6, 1.0 - 1e-6)
        out3[pos] = np.clip(q + rank2 + rank3, 1e-6, 1.0 - 1e-6)

    return out2, out3


def normalize_race_total(
    df: pd.DataFrame,
    raw_prob: np.ndarray,
    target_total: int,
) -> np.ndarray:
    """Shift logits per race so marginal probabilities sum to Top-K count."""

    p = np.clip(np.asarray(raw_prob, dtype=float), 1e-9, 1.0 - 1e-9)
    z = logit(p)
    out = np.full(len(df), np.nan, dtype=float)

    for _, pos in df.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        zz = z[pos]
        n = len(pos)
        total = min(int(target_total), n - 1 if n > 1 else 1)

        lo, hi = -30.0, 30.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            s = float(sigmoid(zz + mid).sum())
            if s < total:
                lo = mid
            else:
                hi = mid
        out[pos] = sigmoid(zz + (lo + hi) / 2.0)

    return out


def topk_capture(df: pd.DataFrame, prob: np.ndarray, k: int) -> float:
    """Share of actual Top-K finishers captured by the K highest predictions."""

    temp = df[["レースID", "着順"]].copy()
    temp["p"] = np.asarray(prob, dtype=float)
    hit = 0
    den = 0

    for _, g in temp.groupby("レースID", sort=False):
        actual = set(g.loc[g["着順"] <= k].index.tolist())
        if not actual:
            continue
        predicted = set(g.nlargest(min(k, len(g)), "p").index.tolist())
        hit += len(actual & predicted)
        den += len(actual)

    return float(hit / den) if den else float("nan")


def evaluate(
    frame: pd.DataFrame,
    y: np.ndarray,
    baseline: np.ndarray,
    corrected: np.ndarray,
    k: int,
) -> dict:
    return {
        "rows": int(len(frame)),
        "races": int(frame["レースID"].nunique()),
        "actualRate": float(np.mean(y)),
        "baselineMean": float(np.mean(baseline)),
        "modelMean": float(np.mean(corrected)),
        "baselineLogloss": binary_logloss(y, baseline),
        "modelLogloss": binary_logloss(y, corrected),
        "loglossImprovement": binary_logloss(y, baseline) - binary_logloss(y, corrected),
        "baselineBrier": brier(y, baseline),
        "modelBrier": brier(y, corrected),
        "brierImprovement": brier(y, baseline) - brier(y, corrected),
        "baselineTopKCapture": topk_capture(frame, baseline, k),
        "modelTopKCapture": topk_capture(frame, corrected, k),
        "meanRaceProbabilitySum": float(
            pd.Series(corrected, index=frame.index)
            .groupby(frame["レースID"])
            .sum()
            .mean()
        ),
    }


def train_one(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    all_final: pd.DataFrame,
    target_col: str,
    baseline_col: str,
    k: int,
    model_name: str,
) -> tuple[lgb.Booster, dict]:
    print()
    print("=" * 110)
    print(f"TRAIN {model_name}")
    print("=" * 110)

    X_train = train[ALLF].astype(np.float32)
    X_valid = valid[ALLF].astype(np.float32)
    X_final = all_final[ALLF].astype(np.float32)

    y_train = train[target_col].astype(int).to_numpy()
    y_valid = valid[target_col].astype(int).to_numpy()
    y_final = all_final[target_col].astype(int).to_numpy()

    base_train = train[baseline_col].to_numpy(dtype=float)
    base_valid = valid[baseline_col].to_numpy(dtype=float)
    base_final = all_final[baseline_col].to_numpy(dtype=float)

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

    model_es = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=MAX_BOOST_ROUNDS,
        valid_sets=[dvalid],
        valid_names=["2025"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True),
            lgb.log_evaluation(50),
        ],
    )

    best_iteration = int(model_es.best_iteration or MAX_BOOST_ROUNDS)
    raw_valid = model_es.predict(
        X_valid,
        raw_score=True,
        num_iteration=best_iteration,
        validate_features=True,
    )
    p_valid_pre = sigmoid(logit(base_valid) + raw_valid)
    p_valid = normalize_race_total(valid, p_valid_pre, k)

    metrics = evaluate(
        valid,
        y_valid,
        base_valid,
        p_valid,
        k,
    )
    metrics["bestIteration"] = best_iteration
    metrics["preNormalizationMean"] = float(np.mean(p_valid_pre))

    print("best iteration:", best_iteration)
    print("2025 baseline logloss:", metrics["baselineLogloss"])
    print("2025 model logloss   :", metrics["modelLogloss"])
    print("2025 logloss improve :", metrics["loglossImprovement"])
    print("2025 baseline Brier  :", metrics["baselineBrier"])
    print("2025 model Brier     :", metrics["modelBrier"])
    print("2025 Brier improve   :", metrics["brierImprovement"])
    print("2025 baseline capture:", metrics["baselineTopKCapture"])
    print("2025 model capture   :", metrics["modelTopKCapture"])
    print("2025 race prob sum   :", metrics["meanRaceProbabilitySum"])

    dfinal = lgb.Dataset(
        X_final,
        label=y_final,
        init_score=logit(base_final),
        feature_name=list(ALLF),
        free_raw_data=False,
    )

    final_model = lgb.train(
        PARAMS,
        dfinal,
        num_boost_round=best_iteration,
    )

    return final_model, metrics


def main() -> None:
    print("=" * 110)
    print("KeibaAI Top2 / Top3 probability model research")
    print("JST:", datetime.now(JST).isoformat())
    print("2026 DATA USED FOR TRAINING: NO")
    print("existing win model modified: NO")
    print("apply candidate models:", APPLY)
    print("=" * 110)

    if not HISTORY_PATH.exists():
        fail(f"history not found: {HISTORY_PATH}")

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")

    if list(hist.columns) != [
        "レースID", "日付", "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
        "着順", "人気", "単勝オッズ", "出典", "芝ダ", "距離", "クラス", "障害", "馬体重",
        "増減", "タイム秒", "上がり3F", "初角", "頭数",
    ]:
        fail("production_history.parquet is not the expected 23-column schema")

    source_latest = hist["日付"].max()
    work = hist.loc[hist["日付"] <= FINAL_END].copy()

    if (work["日付"].dt.year >= 2026).any():
        fail("2026 row entered the model-training frame")

    print("source history rows:", f"{len(hist):,}", "latest:", source_latest)
    print("model research rows through 2025:", f"{len(work):,}")
    print("add_rating...")
    work = add_rating(work)
    print("add_features...")
    work = add_features(work)

    eligible = (
        work["対象"].fillna(False).astype(bool)
        & work["着順"].notna()
        & work["市場確率"].notna()
        & (~work["障害"].fillna(False).astype(bool))
        & (work["頭数"] >= 3)
    )
    d = work.loc[eligible].copy()

    if d.empty:
        fail("no eligible flat historical rows")
    if d[ALLF].isna().any().any():
        fail("37 features contain missing values")
    if not np.isfinite(d[ALLF].astype(float).to_numpy()).all():
        fail("37 features contain inf/-inf")

    d["target_top2"] = (d["着順"] <= 2).astype(int)
    d["target_top3"] = (d["着順"] <= 3).astype(int)

    print("computing Plackett-Luce market Top2/Top3 baselines...")
    base2, base3 = plackett_luce_top2_top3(d)
    d["market_top2"] = base2
    d["market_top3"] = base3

    if d[["market_top2", "market_top3"]].isna().any().any():
        fail("market Top2/Top3 baseline contains missing values")

    train = d.loc[d["日付"] <= TRAIN_END].copy()
    valid = d.loc[(d["日付"] >= VALID_START) & (d["日付"] <= VALID_END)].copy()
    all_final = d.loc[d["日付"] <= FINAL_END].copy()

    print("train 2014-2024:", f"{len(train):,}", "rows /", train["レースID"].nunique(), "races")
    print("valid 2025     :", f"{len(valid):,}", "rows /", valid["レースID"].nunique(), "races")
    print("final <=2025   :", f"{len(all_final):,}", "rows /", all_final["レースID"].nunique(), "races")

    if train.empty or valid.empty:
        fail("train or 2025 validation split is empty")

    model2, metrics2 = train_one(
        train, valid, all_final,
        "target_top2", "market_top2", 2,
        "Top2 (2着以内)",
    )
    model3, metrics3 = train_one(
        train, valid, all_final,
        "target_top3", "market_top3", 3,
        "Top3 (3着以内)",
    )

    # Cross-model monotonicity on the untouched 2025 validation split.
    X_valid = valid[ALLF].astype(np.float32)
    raw2 = model2.predict(X_valid, raw_score=True, validate_features=True)
    raw3 = model3.predict(X_valid, raw_score=True, validate_features=True)
    p2 = normalize_race_total(valid, sigmoid(logit(valid["market_top2"].to_numpy()) + raw2), 2)
    p3 = normalize_race_total(valid, sigmoid(logit(valid["market_top3"].to_numpy()) + raw3), 3)
    monotonic_violations = int(np.sum(p2 > p3 + 1e-12))
    monotonic_rate = float(monotonic_violations / len(valid))

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    model2_path = ARTIFACT_DIR / "lightgbm_top2_fixed_2025.txt"
    model3_path = ARTIFACT_DIR / "lightgbm_top3_fixed_2025.txt"
    model2.save_model(str(model2_path))
    model3.save_model(str(model3_path))

    report = {
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True,
        "trainingData": "2014-01-01 through 2025-12-31",
        "data2026Used": False,
        "protocol": "2014-2024 train, 2025 early stopping, final 2014-2025",
        "features": list(ALLF),
        "featureCount": len(ALLF),
        "marketBaseline": "Plackett-Luce marginal Top-K probability from normalized win market",
        "raceTotalNormalization": "per-race common logit intercept; sums forced to K",
        "top2": metrics2,
        "top3": metrics3,
        "top2GreaterThanTop3Violations2025": monotonic_violations,
        "top2GreaterThanTop3ViolationRate2025": monotonic_rate,
        "parameters": PARAMS,
        "existingWinModelModified": False,
        "applied": APPLY,
    }

    report_json = ARTIFACT_DIR / "top2_top3_model_report.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# KeibaAI Top2 / Top3 Model Research",
        "",
        "2026年データは学習・early stopping・モデル選択に使用していません。",
        "",
        "| 指標 | Top2 | Top3 |",
        "|---|---:|---:|",
        f"| best iteration | {metrics2['bestIteration']} | {metrics3['bestIteration']} |",
        f"| 2025 baseline logloss | {metrics2['baselineLogloss']:.8f} | {metrics3['baselineLogloss']:.8f} |",
        f"| 2025 model logloss | {metrics2['modelLogloss']:.8f} | {metrics3['modelLogloss']:.8f} |",
        f"| logloss improvement | {metrics2['loglossImprovement']:.8f} | {metrics3['loglossImprovement']:.8f} |",
        f"| 2025 baseline Brier | {metrics2['baselineBrier']:.8f} | {metrics3['baselineBrier']:.8f} |",
        f"| 2025 model Brier | {metrics2['modelBrier']:.8f} | {metrics3['modelBrier']:.8f} |",
        f"| Brier improvement | {metrics2['brierImprovement']:.8f} | {metrics3['brierImprovement']:.8f} |",
        f"| baseline Top-K capture | {metrics2['baselineTopKCapture']:.4%} | {metrics3['baselineTopKCapture']:.4%} |",
        f"| model Top-K capture | {metrics2['modelTopKCapture']:.4%} | {metrics3['modelTopKCapture']:.4%} |",
        f"| mean race probability sum | {metrics2['meanRaceProbabilitySum']:.6f} | {metrics3['meanRaceProbabilitySum']:.6f} |",
        "",
        f"Top2 > Top3 violations on 2025 validation: {monotonic_violations:,} / {len(valid):,} ({monotonic_rate:.4%})",
        "",
        "Existing win model: unchanged.",
    ]
    report_md = ARTIFACT_DIR / "top2_top3_model_report.md"
    report_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    # Minimal metadata for future prediction integration.
    for k, metrics, model_path in [
        (2, metrics2, model2_path),
        (3, metrics3, model3_path),
    ]:
        info = {
            "modelName": f"LightGBM_Top{k}_market_correction_fixed_2025",
            "meaning": f"P(finish <= {k})",
            "trainingData": "2014-01-01 through 2025-12-31",
            "data2026Used": False,
            "features": list(ALLF),
            "featureCount": len(ALLF),
            "marketBaseline": "Plackett-Luce Top-K marginal probability",
            "bestIteration": metrics["bestIteration"],
            "lightgbmVersion": lgb.__version__,
            "parameters": PARAMS,
            "postprocess": f"per-race common-logit shift so probabilities sum to {k}",
            "researchOnly": True,
        }
        (ARTIFACT_DIR / f"lightgbm_top{k}_fixed_2025_info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print()
    print("=" * 110)
    print("MODEL RESEARCH SUMMARY")
    print("=" * 110)
    print("Top2 best iteration:", metrics2["bestIteration"])
    print("Top2 logloss improvement:", metrics2["loglossImprovement"])
    print("Top2 Brier improvement:", metrics2["brierImprovement"])
    print("Top3 best iteration:", metrics3["bestIteration"])
    print("Top3 logloss improvement:", metrics3["loglossImprovement"])
    print("Top3 Brier improvement:", metrics3["brierImprovement"])
    print("Top2 > Top3 violations:", monotonic_violations, "/", len(valid))

    if APPLY:
        targets = [
            model2_path,
            model3_path,
            ARTIFACT_DIR / "lightgbm_top2_fixed_2025_info.json",
            ARTIFACT_DIR / "lightgbm_top3_fixed_2025_info.json",
        ]
        for src in targets:
            shutil.copy2(src, HERE / src.name)

        stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(report_json, REPORT_DIR / f"top2_top3_{stamp}.json")
        shutil.copy2(report_md, REPORT_DIR / f"top2_top3_{stamp}.md")

        print()
        print("★★★★★ TOP2/TOP3 MODELS APPLIED TO PRODUCTION DIRECTORY")
        print("Existing win model was NOT modified.")
    else:
        print()
        print("★★★★★ TOP2/TOP3 MODEL RESEARCH: DRY RUN SUCCESS")
        print("Candidate models were NOT installed into production/.")


if __name__ == "__main__":
    main()
