from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

import research_market_independence as r
from feature_engine import ALLF


def train_a_dev(train: pd.DataFrame) -> lgb.Booster:
    X = train[ALLF].astype(np.float32)
    y = (train["着順"].astype(float) == 1.0).astype(int).to_numpy()
    d = lgb.Dataset(
        X,
        label=y,
        init_score=train["市場ロジット"].to_numpy(dtype=float),
        feature_name=list(ALLF),
        free_raw_data=False,
    )
    return lgb.train(r.PARAMS, d, num_boost_round=r.FIXED_ROUNDS)


def race_ranks(frame: pd.DataFrame, probability: np.ndarray) -> np.ndarray:
    out = np.zeros(len(frame), dtype=int)
    temp = frame[["レースID"]].copy()
    temp["p"] = np.asarray(probability, dtype=float)
    for _, pos in temp.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        vals = temp.iloc[pos]["p"]
        ranks = vals.rank(ascending=False, method="min").astype(int).to_numpy()
        out[pos] = ranks
    return out


def rank_bucket_diagnostics(frame: pd.DataFrame, market_p: np.ndarray, c_p: np.ndarray) -> dict:
    d = frame[["レースID", "馬番", "馬名", "着順", "単勝オッズ"]].copy().reset_index(drop=True)
    d["marketRank"] = race_ranks(frame.reset_index(drop=True), market_p)
    d["cRank"] = race_ranks(frame.reset_index(drop=True), c_p)
    d["rankImprovement"] = d["marketRank"] - d["cRank"]
    d["win"] = (d["着順"].astype(float) == 1.0).astype(int)

    winners = d[d["win"] == 1].copy()
    buckets = {}
    for name, mask in {
        "market1to3": winners["marketRank"].between(1, 3),
        "market4to8": winners["marketRank"].between(4, 8),
        "market9plus": winners["marketRank"] >= 9,
    }.items():
        g = winners.loc[mask]
        buckets[name] = {
            "winnerCount": int(len(g)),
            "meanMarketRank": float(g["marketRank"].mean()) if len(g) else None,
            "meanCRank": float(g["cRank"].mean()) if len(g) else None,
            "cImprovedFraction": float((g["rankImprovement"] > 0).mean()) if len(g) else None,
            "cTop3Fraction": float((g["cRank"] <= 3).mean()) if len(g) else None,
            "cTop5Fraction": float((g["cRank"] <= 5).mean()) if len(g) else None,
        }

    selections = {}
    for cutoff in [3, 5]:
        sel = d[(d["marketRank"] >= 6) & (d["cRank"] <= cutoff)].copy()
        gross = float((sel["win"] * sel["単勝オッズ"].astype(float)).sum()) if len(sel) else 0.0
        selections[f"market6plus_Ctop{cutoff}"] = {
            "bets": int(len(sel)),
            "wins": int(sel["win"].sum()),
            "winRate": float(sel["win"].mean()) if len(sel) else None,
            "grossReturnPerUnit": float(gross / len(sel)) if len(sel) else None,
            "meanOdds": float(sel["単勝オッズ"].astype(float).mean()) if len(sel) else None,
        }

    for uplift in [3, 5]:
        sel = d[(d["marketRank"] >= 6) & (d["rankImprovement"] >= uplift)].copy()
        gross = float((sel["win"] * sel["単勝オッズ"].astype(float)).sum()) if len(sel) else 0.0
        selections[f"market6plus_Cuplift{uplift}plus"] = {
            "bets": int(len(sel)),
            "wins": int(sel["win"].sum()),
            "winRate": float(sel["win"].mean()) if len(sel) else None,
            "grossReturnPerUnit": float(gross / len(sel)) if len(sel) else None,
            "meanOdds": float(sel["単勝オッズ"].astype(float).mean()) if len(sel) else None,
        }

    return {"winnerBuckets": buckets, "exploratorySelections": selections}


def sprinters_table(target: pd.DataFrame, a_final, b_final, c_final, hist: pd.DataFrame):
    market = target["市場確率"].to_numpy(dtype=float)
    pa = r.predict_a(a_final, target)
    pb = r.predict_candidate(b_final, target, kind="B")
    pc = r.predict_candidate(c_final, target, kind="C")

    result_rows = hist.loc[
        hist["レースID"].astype(str) == r.TARGET_RID, ["馬番", "着順"]
    ].copy().drop_duplicates("馬番", keep="last")
    finish_map = {
        int(x["馬番"]): int(x["着順"])
        for _, x in result_rows.dropna(subset=["着順"]).iterrows()
    }

    comp = target[["馬番", "馬名", "単勝オッズ", "人気"]].copy().reset_index(drop=True)
    comp["実着順"] = comp["馬番"].astype(int).map(finish_map)
    comp["市場勝率"] = market
    comp["A勝率"] = pa
    comp["B勝率"] = pb
    comp["C勝率"] = pc
    comp["市場順位"] = race_ranks(target.reset_index(drop=True), market)
    comp["A順位"] = race_ranks(target.reset_index(drop=True), pa)
    comp["B順位"] = race_ranks(target.reset_index(drop=True), pb)
    comp["C順位"] = race_ranks(target.reset_index(drop=True), pc)
    comp["C市場順位差"] = comp["市場順位"] - comp["C順位"]
    return comp


def main():
    print("=" * 120)
    print("KeibaAI fair A-dev/B/C holdout experiment")
    print("All development models: train 2014-2024, fixed 212 rounds")
    print("Common untouched comparison year for this experiment: 2025")
    print("2026 used for fitting/tuning: NO")
    print("=" * 120)

    hist = pd.read_parquet(r.HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    train, valid, final = r.prepare_training_frames(hist)

    a_dev = train_a_dev(train)
    b_dev, b_final = r.train_candidate(train, final, kind="B")
    c_dev, c_final = r.train_candidate(train, final, kind="C")
    a_final = lgb.Booster(model_file=str(r.A_MODEL_PATH))

    market = valid["市場確率"].to_numpy(dtype=float)
    pa = r.predict_a(a_dev, valid)
    pb = r.predict_candidate(b_dev, valid, kind="B")
    pc = r.predict_candidate(c_dev, valid, kind="C")

    fair = {
        "protocol": {
            "developmentTrain": "2014-01-01..2024-12-31",
            "commonHoldout": "2025-01-01..2025-12-31",
            "fixedRounds": r.FIXED_ROUNDS,
            "Adev": "37 features including market_logit + market init_score",
            "B": "36 non-market features + market init_score",
            "C": "36 non-market features, no market init_score",
            "note": "No model or threshold is selected for production from this experiment. Exploratory selection diagnostics are descriptive and need new forward validation.",
        },
        "2025FairHoldout": {
            "market": r.evaluate(valid, market),
            "Adev": r.evaluate(valid, pa),
            "B": r.evaluate(valid, pb),
            "C": r.evaluate(valid, pc),
            "CIndependentSignal": rank_bucket_diagnostics(valid, market, pc),
        },
    }

    target = r.prepare_sprinters(hist)
    comp = sprinters_table(target, a_final, b_final, c_final, hist)
    fair["sprinters"] = {
        "A_SpearmanVsMarket": float(comp["A順位"].corr(comp["市場順位"], method="spearman")),
        "B_SpearmanVsMarket": float(comp["B順位"].corr(comp["市場順位"], method="spearman")),
        "C_SpearmanVsMarket": float(comp["C順位"].corr(comp["市場順位"], method="spearman")),
        "actualTop3": comp.loc[
            comp["実着順"].isin([1, 2, 3]),
            ["実着順", "馬番", "馬名", "市場順位", "A順位", "B順位", "C順位", "市場勝率", "A勝率", "B勝率", "C勝率", "C市場順位差"],
        ].sort_values("実着順").to_dict(orient="records"),
    }

    out_json = r.OUT_DIR / "market_independence_fair_metrics.json"
    out_csv = r.OUT_DIR / "sprinters_abc_fair.csv"
    out_json.write_text(json.dumps(fair, ensure_ascii=False, indent=2), encoding="utf-8")
    comp.sort_values("C順位").to_csv(out_csv, index=False)

    print("FAIR_METRICS_BEGIN")
    print(json.dumps(fair, ensure_ascii=False, indent=2))
    print("FAIR_METRICS_END")
    print("SPRINTERS_FAIR_TABLE_BEGIN")
    print(comp.sort_values("C順位").to_json(orient="records", force_ascii=False))
    print("SPRINTERS_FAIR_TABLE_END")


if __name__ == "__main__":
    main()
