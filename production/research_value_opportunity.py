# ============================================================
# KeibaAI - value-oriented opportunity score research
#
# Purpose: refine 勝負度 so it measures AI-vs-market value rather than merely
# strong-favorite confidence. Research only; production remains untouched.
#
# Protocol:
#   train <= 2023
#   early stopping + grade cutoffs: 2024
#   untouched test: 2025
#   2026 excluded
#
# Historical odds are not guaranteed T-10 odds. Return figures are exploratory.
# ============================================================

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from research_race_signals import (
    EXPECTED_BASE_COLS,
    normalize_simple,
    train_residual_model,
    predict_with_base,
    grade_from_reference,
)


JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
ARTIFACT_DIR = REPO / "value_opportunity_artifacts"
TRAIN_END = pd.Timestamp("2023-12-31")
VALID_START = pd.Timestamp("2024-01-01")
VALID_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2025-01-01")
TEST_END = pd.Timestamp("2025-12-31")
SEED = 20261012
BOOTSTRAP_N = 2000


def build_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for race_id, g0 in frame.groupby("レースID", sort=False):
        g = g0.sort_values("馬番").copy()
        market = g["market_win"].to_numpy(dtype=float)
        ai = g["ai_win"].to_numpy(dtype=float)
        odds = pd.to_numeric(g["単勝オッズ"], errors="coerce").to_numpy(dtype=float)
        ranks = pd.to_numeric(g["着順"], errors="coerce").to_numpy(dtype=float)
        pops = pd.to_numeric(g["人気"], errors="coerce").to_numpy(dtype=float)
        nums = pd.to_numeric(g["馬番"], errors="coerce").to_numpy(dtype=float)
        names = g["馬名"].astype(str).to_numpy()

        if not (np.isfinite(market).all() and np.isfinite(ai).all()):
            continue

        ratio = ai / np.clip(market, 1e-9, None)
        edge = ai - market

        # Value-oriented score: mostly ratio/absolute edge, with only a small
        # probability-support term so tiny-probability noise does not dominate.
        ratio_component = np.clip((ratio - 1.0) / 0.40, 0.0, 1.0)
        edge_component = np.clip(edge / 0.06, 0.0, 1.0)
        support_component = np.clip(ai / 0.10, 0.0, 1.0)
        horse_score = 0.55 * ratio_component + 0.30 * edge_component + 0.15 * support_component

        # Only positive-value candidates are meaningful; if none exist, the
        # race receives score 0 and the least-bad AI horse is retained for audit.
        positive = (ratio > 1.0) & (edge > 0.0) & (ai >= 0.01)
        if positive.any():
            masked = np.where(positive, horse_score, -np.inf)
            i = int(np.argmax(masked))
        else:
            i = int(np.argmax(horse_score))

        won = bool(ranks[i] == 1)
        top3 = bool(ranks[i] <= 3)
        final_odds = float(odds[i]) if np.isfinite(odds[i]) else np.nan
        ret = final_odds if won and np.isfinite(final_odds) else 0.0

        rows.append({
            "raceID": str(race_id),
            "date": pd.Timestamp(g["日付"].iloc[0]).strftime("%Y-%m-%d"),
            "horseNumber": int(nums[i]) if np.isfinite(nums[i]) else -1,
            "horseName": str(names[i]),
            "popularity": float(pops[i]) if np.isfinite(pops[i]) else np.nan,
            "finalOdds": final_odds,
            "marketProbability": float(market[i]),
            "aiProbability": float(ai[i]),
            "aiMarketRatio": float(ratio[i]),
            "aiMarketEdge": float(edge[i]),
            "valueOpportunityScore": float(max(horse_score[i], 0.0)),
            "hasPositiveValueCandidate": bool(positive.any()),
            "won": won,
            "top3": top3,
            "returnIndex": float(ret),
        })

    return pd.DataFrame(rows)


def daily_block_bootstrap(group: pd.DataFrame, n: int = BOOTSTRAP_N) -> dict:
    if group.empty:
        return {"mean": np.nan, "ciLow": np.nan, "ciHigh": np.nan, "days": 0}
    daily = group.groupby("date", as_index=False).agg(
        stakes=("returnIndex", "size"),
        returns=("returnIndex", "sum"),
    )
    rng = np.random.default_rng(SEED)
    values = np.empty(n, dtype=float)
    m = len(daily)
    stakes = daily["stakes"].to_numpy(dtype=float)
    returns = daily["returns"].to_numpy(dtype=float)
    for b in range(n):
        idx = rng.integers(0, m, size=m)
        denom = float(stakes[idx].sum())
        values[b] = float(returns[idx].sum() / denom) if denom > 0 else np.nan
    values = values[np.isfinite(values)]
    return {
        "mean": float(group["returnIndex"].mean()),
        "ciLow": float(np.quantile(values, 0.025)),
        "ciHigh": float(np.quantile(values, 0.975)),
        "days": int(m),
    }


def summarize(test_races: pd.DataFrame) -> list[dict]:
    out = []
    for grade in ["見送り", "C", "B", "A", "S"]:
        g = test_races.loc[test_races["valueGrade"] == grade]
        if g.empty:
            continue
        boot = daily_block_bootstrap(g)
        out.append({
            "grade": grade,
            "races": int(len(g)),
            "positiveCandidateRate": float(g["hasPositiveValueCandidate"].mean()),
            "winRate": float(g["won"].mean()),
            "top3Rate": float(g["top3"].mean()),
            "meanFinalOdds": float(g["finalOdds"].mean()),
            "meanAiProbability": float(g["aiProbability"].mean()),
            "meanAiMarketRatio": float(g["aiMarketRatio"].mean()),
            "meanAiMarketEdge": float(g["aiMarketEdge"].mean()),
            "flatWinReturnIndex": float(g["returnIndex"].mean()),
            "return95CILow": boot["ciLow"],
            "return95CIHigh": boot["ciHigh"],
            "days": boot["days"],
            "meanScore": float(g["valueOpportunityScore"].mean()),
        })
    return out


def main() -> None:
    print("=" * 105)
    print("KeibaAI Value Opportunity Research")
    print("JST:", datetime.now(JST).isoformat())
    print("train <=2023 / validation+cutoffs 2024 / untouched test 2025")
    print("2026 DATA USED: NO / production modified: NO")
    print("=" * 105)

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    if list(hist.columns) != EXPECTED_BASE_COLS:
        raise RuntimeError("unexpected production_history schema")

    work = hist.loc[hist["日付"] <= TEST_END].copy()
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    if (work["日付"].dt.year >= 2026).any():
        raise RuntimeError("2026 data entered research frame")

    work = add_rating(work)
    work = add_features(work)
    eligible = (
        work["対象"].fillna(False).astype(bool)
        & work["着順"].notna()
        & work["市場確率"].notna()
        & (~work["障害"].fillna(False).astype(bool))
        & (work["頭数"] >= 4)
    )
    d = work.loc[eligible].copy().reset_index(drop=True)
    if d[ALLF].isna().any().any() or not np.isfinite(d[ALLF].astype(float).to_numpy()).all():
        raise RuntimeError("feature matrix contains missing/non-finite values")

    d["target_win"] = (pd.to_numeric(d["着順"], errors="coerce") == 1).astype(int)
    d["market_win"] = normalize_simple(
        d,
        pd.to_numeric(d["市場確率"], errors="coerce").to_numpy(dtype=float),
        1.0,
    )

    train = d.loc[d["日付"] <= TRAIN_END].copy().reset_index(drop=True)
    valid = d.loc[(d["日付"] >= VALID_START) & (d["日付"] <= VALID_END)].copy().reset_index(drop=True)
    test = d.loc[(d["日付"] >= TEST_START) & (d["日付"] <= TEST_END)].copy().reset_index(drop=True)

    print("train:", len(train), "valid:", len(valid), "test:", len(test))
    model = train_residual_model(train, valid, "target_win", "market_win")
    valid["ai_win"] = predict_with_base(model, valid, "market_win", 1)
    test["ai_win"] = predict_with_base(model, test, "market_win", 1)

    valid_races = build_candidates(valid)
    test_races = build_candidates(test)
    test_races["valueGrade"], cuts = grade_from_reference(
        test_races["valueOpportunityScore"],
        valid_races["valueOpportunityScore"],
        ["見送り", "C", "B", "A", "S"],
    )

    summary = summarize(test_races)

    # Transparent threshold views for robustness checks.
    threshold_rows = []
    for min_ratio in [1.0, 1.1, 1.2, 1.3, 1.4]:
        for min_ai in [0.02, 0.03, 0.05]:
            g = test_races.loc[
                (test_races["aiMarketRatio"] >= min_ratio)
                & (test_races["aiProbability"] >= min_ai)
            ]
            if len(g) < 10:
                continue
            boot = daily_block_bootstrap(g)
            threshold_rows.append({
                "minRatio": min_ratio,
                "minAiProbability": min_ai,
                "races": int(len(g)),
                "winRate": float(g["won"].mean()),
                "meanFinalOdds": float(g["finalOdds"].mean()),
                "flatWinReturnIndex": float(g["returnIndex"].mean()),
                "return95CILow": boot["ciLow"],
                "return95CIHigh": boot["ciHigh"],
            })

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    test_races.to_csv(ARTIFACT_DIR / "value_opportunity_picks_2025.csv", index=False, encoding="utf-8-sig")

    result = {
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True,
        "productionModelsModified": False,
        "data2026Used": False,
        "protocol": {
            "train": "<=2023-12-31",
            "earlyStoppingAndGradeCutoffs": "2024",
            "untouchedTest": "2025",
        },
        "scoreFormula": {
            "aiMarketRatio": 0.55,
            "absoluteProbabilityEdge": 0.30,
            "aiProbabilitySupport": 0.15,
        },
        "cutoffsFrom2024": cuts,
        "2025ByGrade": summary,
        "2025ThresholdChecks": threshold_rows,
        "bootstrap": "2000 day-block resamples",
        "limitations": [
            "Historical final/recorded win odds are not guaranteed T-10 live odds.",
            "A return CI above 1 in this retrospective test still requires forward validation before any real-money use.",
            "Only one value candidate per race is evaluated here.",
        ],
    }
    with (ARTIFACT_DIR / "value_opportunity_research_2025.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines = [
        "# KeibaAI 勝負度（Value版）研究",
        "",
        "強い1番人気を高くするのではなく、AIと市場の差を中心にした勝負度です。",
        "2025年は完全テスト、区切りは2024年だけで決めています。",
        "",
        "## 2025 勝負度別",
        "",
        "| 勝負度 | レース | 勝率 | 3着内率 | 平均AI÷市場 | 平均単勝 | 参考回収指数 | 95%CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary:
        lines.append(
            f"| {r['grade']} | {r['races']} | {100*r['winRate']:.2f}% | {100*r['top3Rate']:.2f}% | "
            f"{r['meanAiMarketRatio']:.3f} | {r['meanFinalOdds']:.2f} | {r['flatWinReturnIndex']:.3f} | "
            f"[{r['return95CILow']:.3f}, {r['return95CIHigh']:.3f}] |"
        )
    lines += [
        "",
        "※参考回収指数は履歴オッズによる研究値で、T-10ライブ回収率ではありません。",
        "",
        "## 固定しきい値の確認",
        "",
        "| AI÷市場以上 | AI勝率以上 | レース | 勝率 | 平均単勝 | 参考回収指数 | 95%CI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in threshold_rows:
        lines.append(
            f"| {r['minRatio']:.1f} | {100*r['minAiProbability']:.0f}% | {r['races']} | "
            f"{100*r['winRate']:.2f}% | {r['meanFinalOdds']:.2f} | {r['flatWinReturnIndex']:.3f} | "
            f"[{r['return95CILow']:.3f}, {r['return95CIHigh']:.3f}] |"
        )
    (ARTIFACT_DIR / "value_opportunity_research_2025.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n2025 VALUE OPPORTUNITY SUMMARY")
    for row in summary:
        print(row)
    print("\nTHRESHOLD CHECKS")
    for row in threshold_rows:
        print(row)
    print("\nRESEARCH SUCCESS")
    print("artifact:", ARTIFACT_DIR)


if __name__ == "__main__":
    main()
