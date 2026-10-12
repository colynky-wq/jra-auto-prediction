# ============================================================
# KeibaAI - race-level research for Chaos / Bet Opportunity / Longshots
#
# Research only. Production models and live publishing are never modified.
# Clean protocol:
#   train <= 2023
#   early stopping + score cutoffs: 2024
#   untouched evaluation: 2025
#   2026 is excluded completely
#
# Goals
# - Build an interpretable first version of 荒れ度 (chaos score).
# - Build an interpretable first version of 勝負度 (opportunity score).
# - Check whether AI-vs-market disagreement helps identify longshots.
# - Produce 2025 tables that can later guide app labels.
#
# Important
# - Historical odds here are the historical market odds in production_history,
#   not guaranteed T-10 odds. ROI-like figures are exploratory only.
# ============================================================

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from topk_runtime import market_top2_top3


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
ARTIFACT_DIR = REPO / "race_signal_artifacts"

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

EXPECTED_BASE_COLS = [
    "レースID", "日付", "馬番", "馬名", "馬キー", "年齢", "斤量", "騎手", "調教師",
    "着順", "人気", "単勝オッズ", "出典", "芝ダ", "距離", "クラス", "障害", "馬体重",
    "増減", "タイム秒", "上がり3F", "初角", "頭数",
]


def fail(message: str) -> None:
    print("\n" + "=" * 110)
    print("KEIBAAI RACE SIGNAL RESEARCH: FAILED")
    print("=" * 110)
    print(message)
    sys.exit(1)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(x, dtype=float), -50.0, 50.0)))


def logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(p, dtype=float), 1e-9, 1.0 - 1e-9)
    return np.log(q / (1.0 - q))


def normalize_simple(frame: pd.DataFrame, values: np.ndarray, total: float) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        q = np.clip(values[pos], 1e-12, None)
        s = float(q.sum())
        if not np.isfinite(s) or s <= 0:
            continue
        out[pos] = q / s * float(total)
    return out


def normalize_logit_total(frame: pd.DataFrame, raw_prob: np.ndarray, target_total: int) -> np.ndarray:
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
        for _ in range(80):
            mid = (lo + hi) / 2.0
            s = float(sigmoid(zz + mid).sum())
            if s < float(target_total):
                lo = mid
            else:
                hi = mid
        out[pos] = sigmoid(zz + (lo + hi) / 2.0)

    return out


def compute_market_top3(frame: pd.DataFrame, market_win: np.ndarray) -> np.ndarray:
    out = np.full(len(frame), np.nan, dtype=float)
    p_all = np.asarray(market_win, dtype=float)
    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        q = p_all[pos]
        if len(q) < 3 or not np.isfinite(q).all() or (q <= 0).any():
            continue
        q = q / q.sum()
        _, top3 = market_top2_top3(q)
        out[pos] = top3
    return out


def train_residual_model(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    target_col: str,
    base_col: str,
) -> lgb.Booster:
    X_train = train[ALLF].astype(np.float32)
    X_valid = valid[ALLF].astype(np.float32)
    y_train = train[target_col].astype(int).to_numpy()
    y_valid = valid[target_col].astype(int).to_numpy()
    base_train = train[base_col].to_numpy(dtype=float)
    base_valid = valid[base_col].to_numpy(dtype=float)

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

    booster = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=MAX_BOOST_ROUNDS,
        valid_sets=[dvalid],
        valid_names=["2024"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=True),
            lgb.log_evaluation(100),
        ],
    )
    return booster


def predict_with_base(
    booster: lgb.Booster,
    frame: pd.DataFrame,
    base_col: str,
    target_total: int,
) -> np.ndarray:
    X = frame[ALLF].astype(np.float32)
    base = frame[base_col].to_numpy(dtype=float)
    corr = booster.predict(
        X,
        raw_score=True,
        num_iteration=booster.best_iteration or MAX_BOOST_ROUNDS,
    )
    raw = sigmoid(logit(base) + np.asarray(corr, dtype=float))
    return normalize_logit_total(frame, raw, target_total)


def normalized_entropy(p: np.ndarray) -> float:
    q = np.clip(np.asarray(p, dtype=float), 1e-12, None)
    q = q / q.sum()
    n = len(q)
    if n <= 1:
        return 0.0
    return float(-(q * np.log(q)).sum() / math.log(n))


def grade_from_reference(
    values: pd.Series,
    reference: pd.Series,
    labels: list[str],
) -> tuple[pd.Series, list[float]]:
    ref = pd.to_numeric(reference, errors="coerce").dropna().to_numpy(dtype=float)
    if len(ref) < 10:
        raise ValueError("reference score is too small")
    cuts = [float(x) for x in np.quantile(ref, [0.2, 0.4, 0.6, 0.8])]

    def one(v: float) -> str:
        if not np.isfinite(v):
            return labels[0]
        if v <= cuts[0]:
            return labels[0]
        if v <= cuts[1]:
            return labels[1]
        if v <= cuts[2]:
            return labels[2]
        if v <= cuts[3]:
            return labels[3]
        return labels[4]

    return values.astype(float).map(one), cuts


def build_race_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    for race_id, g0 in frame.groupby("レースID", sort=False):
        g = g0.sort_values("馬番").copy()
        if len(g) < 4:
            continue

        market = g["market_win"].to_numpy(dtype=float)
        ai = g["ai_win"].to_numpy(dtype=float)
        market_t3 = g["market_top3"].to_numpy(dtype=float)
        ai_t3 = g["ai_top3"].to_numpy(dtype=float)
        ranks = pd.to_numeric(g["着順"], errors="coerce").to_numpy(dtype=float)
        pops = pd.to_numeric(g["人気"], errors="coerce").to_numpy(dtype=float)
        odds = pd.to_numeric(g["単勝オッズ"], errors="coerce").to_numpy(dtype=float)

        if not (
            np.isfinite(market).all()
            and np.isfinite(ai).all()
            and np.isfinite(market_t3).all()
            and np.isfinite(ai_t3).all()
        ):
            continue

        winner_pos = np.where(ranks == 1)[0]
        if len(winner_pos) != 1:
            continue
        winner_i = int(winner_pos[0])

        market_order = np.argsort(-market)
        ai_order = np.argsort(-ai)
        market_i = int(market_order[0])
        ai_i = int(ai_order[0])

        market_sorted = market[market_order]
        ai_sorted = ai[ai_order]

        market_p1 = float(market_sorted[0])
        market_p2 = float(market_sorted[1])
        ai_p1 = float(ai_sorted[0])
        ai_p2 = float(ai_sorted[1])

        uncertainty = float(np.clip((1.0 - market_p1) / 0.85, 0.0, 1.0))
        entropy = normalized_entropy(market)
        closeness = float(np.clip(1.0 - (market_p1 - market_p2) / 0.25, 0.0, 1.0))
        disagreement_l1 = float(np.abs(ai - market).sum() / 2.0)
        disagreement = float(np.clip(disagreement_l1 / 0.15, 0.0, 1.0))

        longshot_mask = np.isfinite(pops) & (pops >= 6)
        if longshot_mask.any():
            longshot_edge = float(np.max(np.maximum(ai_t3[longshot_mask] - market_t3[longshot_mask], 0.0)))
        else:
            longshot_edge = 0.0
        longshot_signal = float(np.clip(longshot_edge / 0.20, 0.0, 1.0))
        field_factor = float(np.clip((len(g) - 8.0) / 10.0, 0.0, 1.0))

        chaos_score = (
            0.30 * uncertainty
            + 0.25 * entropy
            + 0.20 * closeness
            + 0.15 * disagreement
            + 0.07 * longshot_signal
            + 0.03 * field_factor
        )

        top_edge = float(ai[ai_i] - market[ai_i])
        ratio = float(ai[ai_i] / max(market[ai_i], 1e-9))
        confidence_gap = float(max(ai_p1 - ai_p2, 0.0))

        edge_component = float(np.clip(max(top_edge, 0.0) / 0.12, 0.0, 1.0))
        gap_component = float(np.clip(confidence_gap / 0.15, 0.0, 1.0))
        prob_component = float(np.clip(ai_p1 / 0.50, 0.0, 1.0))
        ratio_component = float(np.clip((ratio - 1.0) / 0.50, 0.0, 1.0))

        opportunity_score = (
            0.40 * edge_component
            + 0.25 * gap_component
            + 0.20 * prob_component
            + 0.15 * ratio_component
        )

        top3_mask = ranks <= 3
        winner_pop = float(pops[winner_i]) if np.isfinite(pops[winner_i]) else np.nan
        top3_pops = pops[top3_mask & np.isfinite(pops)]

        market_nll = float(-math.log(max(market[winner_i], 1e-12)))
        ai_nll = float(-math.log(max(ai[winner_i], 1e-12)))

        rows.append({
            "raceID": str(race_id),
            "date": pd.Timestamp(g["日付"].iloc[0]).strftime("%Y-%m-%d"),
            "fieldSize": int(len(g)),
            "marketFavoriteProbability": market_p1,
            "marketSecondProbability": market_p2,
            "aiTopProbability": ai_p1,
            "aiSecondProbability": ai_p2,
            "chaosScore": float(chaos_score),
            "chaosUncertainty": uncertainty,
            "chaosEntropy": entropy,
            "chaosCloseness": closeness,
            "chaosDisagreement": disagreement,
            "chaosLongshotSignal": longshot_signal,
            "opportunityScore": float(opportunity_score),
            "aiTopEdge": top_edge,
            "aiTopMarketRatio": ratio,
            "aiConfidenceGap": confidence_gap,
            "sameTopHorseAsMarket": bool(ai_i == market_i),
            "aiTopWon": bool(ranks[ai_i] == 1),
            "marketFavoriteWon": bool(ranks[market_i] == 1),
            "aiTopFinalOdds": float(odds[ai_i]) if np.isfinite(odds[ai_i]) else np.nan,
            "aiTopFlatReturnIndex": float(odds[ai_i]) if ranks[ai_i] == 1 and np.isfinite(odds[ai_i]) else 0.0,
            "winnerPopularity": winner_pop,
            "winnerPopularity6Plus": bool(np.isfinite(winner_pop) and winner_pop >= 6),
            "top3HasPopularity8Plus": bool(np.any(top3_mask & np.isfinite(pops) & (pops >= 8))),
            "top3HasPopularity10Plus": bool(np.any(top3_mask & np.isfinite(pops) & (pops >= 10))),
            "meanTop3Popularity": float(np.mean(top3_pops)) if len(top3_pops) else np.nan,
            "marketWinnerNLL": market_nll,
            "aiWinnerNLL": ai_nll,
            "winnerNLLImprovement": market_nll - ai_nll,
        })

    return pd.DataFrame(rows)


def summarize_chaos(test_races: pd.DataFrame) -> list[dict]:
    order = ["★☆☆☆☆", "★★☆☆☆", "★★★☆☆", "★★★★☆", "★★★★★"]
    out: list[dict] = []
    for label in order:
        g = test_races.loc[test_races["chaosGrade"] == label]
        if g.empty:
            continue
        out.append({
            "grade": label,
            "races": int(len(g)),
            "favoriteWinRate": float(g["marketFavoriteWon"].mean()),
            "winnerPopularity6PlusRate": float(g["winnerPopularity6Plus"].mean()),
            "top3HasPopularity8PlusRate": float(g["top3HasPopularity8Plus"].mean()),
            "top3HasPopularity10PlusRate": float(g["top3HasPopularity10Plus"].mean()),
            "meanWinnerPopularity": float(g["winnerPopularity"].mean()),
            "meanTop3Popularity": float(g["meanTop3Popularity"].mean()),
            "meanChaosScore": float(g["chaosScore"].mean()),
        })
    return out


def summarize_opportunity(test_races: pd.DataFrame) -> list[dict]:
    order = ["見送り", "C", "B", "A", "S"]
    out: list[dict] = []
    for label in order:
        g = test_races.loc[test_races["opportunityGrade"] == label]
        if g.empty:
            continue
        out.append({
            "grade": label,
            "races": int(len(g)),
            "aiTopWinRate": float(g["aiTopWon"].mean()),
            "marketFavoriteWinRate": float(g["marketFavoriteWon"].mean()),
            "aiTopVsMarketFavoriteWinRateDiff": float(g["aiTopWon"].mean() - g["marketFavoriteWon"].mean()),
            "meanAiTopFinalOdds": float(g["aiTopFinalOdds"].mean()),
            "meanAiTopEdge": float(g["aiTopEdge"].mean()),
            "meanAiTopMarketRatio": float(g["aiTopMarketRatio"].mean()),
            "sameTopHorseAsMarketRate": float(g["sameTopHorseAsMarket"].mean()),
            "meanWinnerNLLImprovement": float(g["winnerNLLImprovement"].mean()),
            "exploratoryFlatWinReturnIndex": float(g["aiTopFlatReturnIndex"].mean()),
            "meanOpportunityScore": float(g["opportunityScore"].mean()),
        })
    return out


def summarize_longshots(test: pd.DataFrame) -> list[dict]:
    work = test.copy()
    work["aiMarketRatioWin"] = work["ai_win"] / np.clip(work["market_win"], 1e-9, None)
    work["aiTop3Edge"] = work["ai_top3"] - work["market_top3"]

    odds = pd.to_numeric(work["単勝オッズ"], errors="coerce")
    pop = pd.to_numeric(work["人気"], errors="coerce")
    rank = pd.to_numeric(work["着順"], errors="coerce")

    work = work.loc[
        (pop >= 6)
        & (odds >= 8.0)
        & (work["ai_win"] >= 0.01)
        & np.isfinite(work["aiMarketRatioWin"])
    ].copy()
    work["oddsNum"] = pd.to_numeric(work["単勝オッズ"], errors="coerce")
    work["rankNum"] = pd.to_numeric(work["着順"], errors="coerce")

    bins = [
        ("<1.0", -np.inf, 1.0),
        ("1.0-1.1", 1.0, 1.1),
        ("1.1-1.2", 1.1, 1.2),
        ("1.2-1.4", 1.2, 1.4),
        (">=1.4", 1.4, np.inf),
    ]
    out: list[dict] = []
    for label, lo, hi in bins:
        if np.isneginf(lo):
            g = work.loc[work["aiMarketRatioWin"] < hi]
        elif np.isposinf(hi):
            g = work.loc[work["aiMarketRatioWin"] >= lo]
        else:
            g = work.loc[(work["aiMarketRatioWin"] >= lo) & (work["aiMarketRatioWin"] < hi)]
        if g.empty:
            continue
        win = g["rankNum"] == 1
        top3 = g["rankNum"] <= 3
        returns = np.where(win, g["oddsNum"].to_numpy(dtype=float), 0.0)
        out.append({
            "aiMarketRatioBucket": label,
            "horses": int(len(g)),
            "winRate": float(win.mean()),
            "top3Rate": float(top3.mean()),
            "meanFinalOdds": float(g["oddsNum"].mean()),
            "meanAiWinProbability": float(g["ai_win"].mean()),
            "meanAiTop3Edge": float(g["aiTop3Edge"].mean()),
            "exploratoryFlatWinReturnIndex": float(np.mean(returns)),
        })

    return out


def pct(x: float) -> str:
    return f"{100.0 * x:.2f}%"


def main() -> None:
    print("=" * 110)
    print("KeibaAI Race Signal Research: Chaos / Opportunity / Longshots")
    print("JST:", datetime.now(JST).isoformat())
    print("train <= 2023 / validation+cutoffs 2024 / untouched test 2025")
    print("2026 DATA USED: NO")
    print("production models modified: NO")
    print("=" * 110)

    if not HISTORY_PATH.exists():
        fail(f"history not found: {HISTORY_PATH}")

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    if list(hist.columns) != EXPECTED_BASE_COLS:
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
        fail("no eligible rows")
    if d[ALLF].isna().any().any() or not np.isfinite(d[ALLF].astype(float).to_numpy()).all():
        fail("37 features contain missing or non-finite values")

    d["target_win"] = (pd.to_numeric(d["着順"], errors="coerce") == 1).astype(int)
    d["target_top3"] = (pd.to_numeric(d["着順"], errors="coerce") <= 3).astype(int)

    base_market = pd.to_numeric(d["市場確率"], errors="coerce").to_numpy(dtype=float)
    d["market_win"] = normalize_simple(d, base_market, 1.0)
    d["market_top3"] = compute_market_top3(d, d["market_win"].to_numpy(dtype=float))
    if d[["market_win", "market_top3"]].isna().any().any():
        fail("market baseline contains missing values")

    train = d.loc[d["日付"] <= TRAIN_END].copy().reset_index(drop=True)
    valid = d.loc[(d["日付"] >= VALID_START) & (d["日付"] <= VALID_END)].copy().reset_index(drop=True)
    test = d.loc[(d["日付"] >= TEST_START) & (d["日付"] <= TEST_END)].copy().reset_index(drop=True)

    print("train <=2023:", f"{len(train):,}", "rows /", train["レースID"].nunique(), "races")
    print("valid 2024 :", f"{len(valid):,}", "rows /", valid["レースID"].nunique(), "races")
    print("test 2025  :", f"{len(test):,}", "rows /", test["レースID"].nunique(), "races")
    if train.empty or valid.empty or test.empty:
        fail("split is empty")

    print("\nTraining leakage-safe win correction model...")
    win_model = train_residual_model(train, valid, "target_win", "market_win")
    print("Training leakage-safe Top3 correction model...")
    top3_model = train_residual_model(train, valid, "target_top3", "market_top3")

    valid["ai_win"] = predict_with_base(win_model, valid, "market_win", 1)
    valid["ai_top3"] = predict_with_base(top3_model, valid, "market_top3", 3)
    test["ai_win"] = predict_with_base(win_model, test, "market_win", 1)
    test["ai_top3"] = predict_with_base(top3_model, test, "market_top3", 3)

    for name, frame in [("2024", valid), ("2025", test)]:
        win_sum = frame.groupby("レースID")["ai_win"].sum()
        t3_sum = frame.groupby("レースID")["ai_top3"].sum()
        print(name, "max |AI win sum-1|:", float(np.max(np.abs(win_sum - 1.0))))
        print(name, "max |AI Top3 sum-3|:", float(np.max(np.abs(t3_sum - 3.0))))

    valid_races = build_race_table(valid)
    test_races = build_race_table(test)
    if valid_races.empty or test_races.empty:
        fail("race-level table is empty")

    test_races["chaosGrade"], chaos_cuts = grade_from_reference(
        test_races["chaosScore"], valid_races["chaosScore"],
        ["★☆☆☆☆", "★★☆☆☆", "★★★☆☆", "★★★★☆", "★★★★★"],
    )
    test_races["opportunityGrade"], opportunity_cuts = grade_from_reference(
        test_races["opportunityScore"], valid_races["opportunityScore"],
        ["見送り", "C", "B", "A", "S"],
    )

    chaos_summary = summarize_chaos(test_races)
    opportunity_summary = summarize_opportunity(test_races)
    longshot_summary = summarize_longshots(test)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    test_races.to_csv(ARTIFACT_DIR / "race_signal_scores_2025.csv", index=False, encoding="utf-8-sig")

    result = {
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True,
        "productionModelsModified": False,
        "data2026Used": False,
        "protocol": {
            "train": "<=2023-12-31",
            "earlyStoppingAndCutoffs": "2024-01-01..2024-12-31",
            "untouchedTest": "2025-01-01..2025-12-31",
        },
        "featureCount": len(ALLF),
        "races2025": int(len(test_races)),
        "chaosScore": {
            "cutoffsFrom2024": chaos_cuts,
            "weights": {
                "favoriteUncertainty": 0.30,
                "marketEntropy": 0.25,
                "favoriteCloseness": 0.20,
                "aiMarketDisagreement": 0.15,
                "longshotTop3Signal": 0.07,
                "fieldSize": 0.03,
            },
            "2025ByGrade": chaos_summary,
        },
        "opportunityScore": {
            "cutoffsFrom2024": opportunity_cuts,
            "weights": {
                "aiTopPositiveEdge": 0.40,
                "aiTopConfidenceGap": 0.25,
                "aiTopWinProbability": 0.20,
                "aiTopMarketRatio": 0.15,
            },
            "2025ByGrade": opportunity_summary,
        },
        "longshots": {
            "filter": "popularity>=6, finalWinOdds>=8, aiWin>=1%",
            "2025ByAiMarketRatio": longshot_summary,
        },
        "limitations": [
            "Historical market odds are not guaranteed to be the T-10 odds used in live operation.",
            "Exploratory flat return indices are not a live betting backtest.",
            "Chaos labels measure outcome upset tendencies, not payout size because all-ticket historical odds are not in production_history.parquet.",
            "Opportunity grade is a research score and must not be treated as a profit guarantee.",
        ],
    }

    with (ARTIFACT_DIR / "race_signal_research_2025.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    md: list[str] = []
    md.append("# KeibaAI Race Signal Research (2025 untouched test)")
    md.append("")
    md.append("- 学習: 2023年まで")
    md.append("- early stopping / スコア区切り: 2024年")
    md.append("- 完全テスト: 2025年")
    md.append("- 2026年データ: 不使用")
    md.append("")
    md.append("## 荒れ度")
    md.append("")
    md.append("| 荒れ度 | レース数 | 1番人気勝率 | 6番人気以下勝馬 | 3着内に8番人気以下 | 3着内に10番人気以下 |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for r in chaos_summary:
        md.append(
            f"| {r['grade']} | {r['races']} | {pct(r['favoriteWinRate'])} | "
            f"{pct(r['winnerPopularity6PlusRate'])} | {pct(r['top3HasPopularity8PlusRate'])} | "
            f"{pct(r['top3HasPopularity10PlusRate'])} |"
        )

    md.append("")
    md.append("## 勝負度")
    md.append("")
    md.append("| 勝負度 | レース数 | AI本命勝率 | 市場本命勝率 | AI-市場勝率差 | 平均AI÷市場 | 1着確率評価の改善 | 参考単勝回収指数* |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in opportunity_summary:
        md.append(
            f"| {r['grade']} | {r['races']} | {pct(r['aiTopWinRate'])} | {pct(r['marketFavoriteWinRate'])} | "
            f"{100.0*r['aiTopVsMarketFavoriteWinRateDiff']:+.2f}pt | {r['meanAiTopMarketRatio']:.3f} | "
            f"{r['meanWinnerNLLImprovement']:+.4f} | {r['exploratoryFlatWinReturnIndex']:.3f} |"
        )
    md.append("")
    md.append("*参考単勝回収指数は履歴中の単勝オッズを使った研究値で、T-10ライブオッズの回収率ではありません。")

    md.append("")
    md.append("## 人気薄候補（2025）")
    md.append("")
    md.append("条件: 6番人気以下・単勝8倍以上・AI勝率1%以上")
    md.append("")
    md.append("| AI÷市場 | 頭数 | 勝率 | 3着内率 | 平均単勝 | 平均AI勝率 | 平均Top3上乗せ | 参考単勝回収指数* |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in longshot_summary:
        md.append(
            f"| {r['aiMarketRatioBucket']} | {r['horses']} | {pct(r['winRate'])} | {pct(r['top3Rate'])} | "
            f"{r['meanFinalOdds']:.2f} | {pct(r['meanAiWinProbability'])} | "
            f"{100.0*r['meanAiTop3Edge']:+.2f}pt | {r['exploratoryFlatWinReturnIndex']:.3f} |"
        )

    md.append("")
    md.append("## 注意")
    md.append("")
    md.append("この結果は研究用です。馬券プランへ直接使用せず、今後のT-10全券種オッズ蓄積と前向き検証で確認します。")
    (ARTIFACT_DIR / "race_signal_research_2025.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("\n" + "=" * 110)
    print("2025 CHAOS SUMMARY")
    for r in chaos_summary:
        print(r)
    print("\n2025 OPPORTUNITY SUMMARY")
    for r in opportunity_summary:
        print(r)
    print("\n2025 LONGSHOT SUMMARY")
    for r in longshot_summary:
        print(r)
    print("\nRESEARCH SUCCESS")
    print("artifact:", ARTIFACT_DIR)


if __name__ == "__main__":
    main()
