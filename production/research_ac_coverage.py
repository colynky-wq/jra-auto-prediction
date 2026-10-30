from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
A_MODEL_PATH = HERE / "lightgbm_fixed_2025.txt"
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FEATURES_C = [f for f in ALLF if f != "市場ロジット"]
FIXED_ROUNDS = 212
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


def sigmoid(x):
    x = np.asarray(x, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1.0 - 1e-9)
    return np.log(p / (1.0 - p))


def normalize_race_total(frame: pd.DataFrame, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    z = logit(p)
    out = np.full(len(frame), np.nan, dtype=float)
    for _, pos in frame.groupby("レースID", sort=False).indices.items():
        pos = np.asarray(pos, dtype=int)
        zz = z[pos]
        lo, hi = -30.0, 30.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if float(sigmoid(zz + mid).sum()) < 1.0:
                lo = mid
            else:
                hi = mid
        out[pos] = sigmoid(zz + (lo + hi) / 2.0)
    return out


def train_c(train: pd.DataFrame) -> lgb.Booster:
    X = train[FEATURES_C].astype(np.float32)
    y = (train["着順"].astype(float) == 1.0).astype(int).to_numpy()
    ds = lgb.Dataset(X, label=y, feature_name=FEATURES_C, free_raw_data=False)
    return lgb.train(PARAMS, ds, num_boost_round=FIXED_ROUNDS)


def predict_a(model: lgb.Booster, frame: pd.DataFrame) -> np.ndarray:
    raw = model.predict(frame[ALLF].astype(np.float32), raw_score=True, validate_features=True)
    p0 = sigmoid(frame["市場ロジット"].to_numpy(dtype=float) + raw)
    return normalize_race_total(frame, p0)


def predict_c(model: lgb.Booster, frame: pd.DataFrame) -> np.ndarray:
    raw = model.predict(frame[FEATURES_C].astype(np.float32), raw_score=True, validate_features=True)
    return normalize_race_total(frame, sigmoid(raw))


def race_level_comparison(frame: pd.DataFrame) -> tuple[dict, list[dict], list[dict]]:
    totals = {
        "races": 0,
        "top1Different": 0,
        "top3SetDifferent": 0,
        "top3SetIdentical": 0,
        "winnerHigherByC": 0,
        "winnerSameRank": 0,
        "winnerLowerByC": 0,
        "cTop1Correct_aWrong": 0,
        "aTop1Correct_cWrong": 0,
        "bothTop1Correct": 0,
        "bothTop1Wrong": 0,
        "cTop3Find_aOutside3": 0,
        "cTop5Find_aOutside5": 0,
        "aTop3Find_cOutside3": 0,
        "aTop5Find_cOutside5": 0,
    }
    winner_rescues = []
    largest_disagreements = []

    for rid, g0 in frame.groupby("レースID", sort=False):
        g = g0.copy()
        winners = g[g["着順"].astype(float) == 1.0]
        if len(winners) != 1:
            continue
        g["A順位"] = g["A勝率"].rank(ascending=False, method="min").astype(int)
        g["C順位"] = g["C勝率"].rank(ascending=False, method="min").astype(int)
        g["順位差CminusA"] = g["A順位"] - g["C順位"]
        totals["races"] += 1

        a1 = int(g.loc[g["A順位"] == 1, "馬番"].iloc[0])
        c1 = int(g.loc[g["C順位"] == 1, "馬番"].iloc[0])
        if a1 != c1:
            totals["top1Different"] += 1

        a3 = set(g.nsmallest(min(3, len(g)), "A順位")["馬番"].astype(int))
        c3 = set(g.nsmallest(min(3, len(g)), "C順位")["馬番"].astype(int))
        if a3 != c3:
            totals["top3SetDifferent"] += 1
        else:
            totals["top3SetIdentical"] += 1

        w = winners.iloc[0]
        wi = w.name
        ar = int(g.loc[wi, "A順位"])
        cr = int(g.loc[wi, "C順位"])
        if cr < ar:
            totals["winnerHigherByC"] += 1
        elif cr == ar:
            totals["winnerSameRank"] += 1
        else:
            totals["winnerLowerByC"] += 1

        a_correct = ar == 1
        c_correct = cr == 1
        if c_correct and not a_correct:
            totals["cTop1Correct_aWrong"] += 1
        elif a_correct and not c_correct:
            totals["aTop1Correct_cWrong"] += 1
        elif a_correct and c_correct:
            totals["bothTop1Correct"] += 1
        else:
            totals["bothTop1Wrong"] += 1

        if cr <= 3 and ar > 3:
            totals["cTop3Find_aOutside3"] += 1
        if cr <= 5 and ar > 5:
            totals["cTop5Find_aOutside5"] += 1
        if ar <= 3 and cr > 3:
            totals["aTop3Find_cOutside3"] += 1
        if ar <= 5 and cr > 5:
            totals["aTop5Find_cOutside5"] += 1

        if cr <= 5 and ar > 5:
            winner_rescues.append({
                "raceID": str(rid),
                "date": str(pd.Timestamp(w["日付"]).date()),
                "horseNo": int(w["馬番"]),
                "horseName": str(w["馬名"]),
                "odds": float(w["単勝オッズ"]),
                "A_rank": ar,
                "C_rank": cr,
                "A_prob": float(g.loc[wi, "A勝率"]),
                "C_prob": float(g.loc[wi, "C勝率"]),
            })

        # race's single largest horse-level A/C rank disagreement
        gg = g.reindex(g["順位差CminusA"].abs().sort_values(ascending=False).index)
        r = gg.iloc[0]
        largest_disagreements.append({
            "raceID": str(rid),
            "date": str(pd.Timestamp(r["日付"]).date()),
            "horseNo": int(r["馬番"]),
            "horseName": str(r["馬名"]),
            "finish": int(r["着順"]),
            "odds": float(r["単勝オッズ"]),
            "A_rank": int(r["A順位"]),
            "C_rank": int(r["C順位"]),
            "rankShiftTowardC": int(r["順位差CminusA"]),
        })

    n = totals["races"] or 1
    pct = {
        "top1DifferentPct": totals["top1Different"] / n * 100,
        "top3SetDifferentPct": totals["top3SetDifferent"] / n * 100,
        "winnerHigherByCPct": totals["winnerHigherByC"] / n * 100,
        "cTop1Correct_aWrongPct": totals["cTop1Correct_aWrong"] / n * 100,
        "cTop5Find_aOutside5Pct": totals["cTop5Find_aOutside5"] / n * 100,
    }
    totals.update(pct)
    winner_rescues.sort(key=lambda x: (x["A_rank"] - x["C_rank"], x["odds"]), reverse=True)
    largest_disagreements.sort(key=lambda x: abs(x["rankShiftTowardC"]), reverse=True)
    return totals, winner_rescues[:50], largest_disagreements[:50]


def structural_coverage(work_2026: pd.DataFrame) -> dict:
    # Strict race-level availability: every runner in the result frame must have all required inputs.
    c_num = work_2026[FEATURES_C].apply(pd.to_numeric, errors="coerce")
    a_num = work_2026[ALLF].apply(pd.to_numeric, errors="coerce")
    temp = work_2026[["レースID", "日付", "障害", "着順", "単勝オッズ"]].copy()
    temp["cReadyRow"] = np.isfinite(c_num.to_numpy(dtype=float)).all(axis=1)
    temp["aReadyRow"] = np.isfinite(a_num.to_numpy(dtype=float)).all(axis=1)
    temp = temp[(~temp["障害"].fillna(False).astype(bool)) & temp["着順"].notna()].copy()

    recs = []
    for rid, g in temp.groupby("レースID", sort=False):
        c_ready = bool(g["cReadyRow"].all())
        a_ready = bool(g["aReadyRow"].all())
        recs.append({
            "raceID": str(rid),
            "date": str(pd.Timestamp(g["日付"].iloc[0]).date()),
            "runners": int(len(g)),
            "cReady": c_ready,
            "aReady": a_ready,
            "cOnly": bool(c_ready and not a_ready),
            "rowsMissingMarketOdds": int(pd.to_numeric(g["単勝オッズ"], errors="coerce").isna().sum()),
        })
    rf = pd.DataFrame(recs)
    if rf.empty:
        return {"races": 0, "details": []}
    c_only = rf[rf["cOnly"]]
    return {
        "races": int(len(rf)),
        "aReadyRaces": int(rf["aReady"].sum()),
        "cReadyRaces": int(rf["cReady"].sum()),
        "cOnlyRaces": int(rf["cOnly"].sum()),
        "cOnlyPctOfAll": float(rf["cOnly"].mean() * 100),
        "details": c_only.head(100).to_dict(orient="records"),
        "interpretation": "This is structural replay coverage using completed-race historical fields, not proof that live A actually failed at the time.",
    }


def main():
    print("=" * 110)
    print("KeibaAI A/C disagreement and coverage audit")
    print("2026 target: completed races through production_history max date")
    print("A: fixed2025 market-correction model")
    print("C: 36 non-market features, trained only through 2025")
    print("=" * 110)

    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    hist = hist.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    print("history rows", len(hist), "max", hist["日付"].max())
    print("add_rating...")
    feat = add_rating(hist.copy())
    print("add_features...")
    feat = add_features(feat)

    # C training cohort exactly mirrors prior research definition: common clean rows through 2025.
    train = feat[(feat["日付"] <= pd.Timestamp("2025-12-31")) & feat["着順"].notna() & (~feat["障害"].fillna(False).astype(bool))].copy()
    train_num = train[FEATURES_C].apply(pd.to_numeric, errors="coerce")
    train = train[np.isfinite(train_num.to_numpy(dtype=float)).all(axis=1)].copy().reset_index(drop=True)
    print("C train rows", len(train), "races", train["レースID"].nunique())
    c_model = train_c(train)
    a_model = lgb.Booster(model_file=str(A_MODEL_PATH))

    target_all = feat[(feat["日付"] >= pd.Timestamp("2026-01-01")) & (feat["日付"] <= feat["日付"].max())].copy()
    coverage = structural_coverage(target_all)

    # Fair A/C result comparison requires rows on which both models can score and a known result.
    comp = target_all[target_all["着順"].notna() & (~target_all["障害"].fillna(False).astype(bool))].copy()
    nums = comp[ALLF].apply(pd.to_numeric, errors="coerce")
    comp = comp[np.isfinite(nums.to_numpy(dtype=float)).all(axis=1)].copy().reset_index(drop=True)
    # Strict complete race cohort: keep races where every non-obstacle result row was scoreable by A.
    raw_counts = target_all[target_all["着順"].notna() & (~target_all["障害"].fillna(False).astype(bool))].groupby("レースID").size()
    score_counts = comp.groupby("レースID").size()
    complete_ids = [rid for rid, n in score_counts.items() if int(n) == int(raw_counts.get(rid, -1))]
    comp = comp[comp["レースID"].isin(complete_ids)].copy().reset_index(drop=True)

    comp["A勝率"] = predict_a(a_model, comp)
    comp["C勝率"] = predict_c(c_model, comp)
    totals, rescues, disagreements = race_level_comparison(comp)

    result = {
        "protocol": {
            "historyMaxDate": str(hist["日付"].max()),
            "comparisonPeriod": "2026-01-01..historyMaxDate",
            "A": "production lightgbm_fixed_2025 market-correction model",
            "C": "36 non-market features, 212 rounds, fit through 2025 only",
            "note": "Completed-race replay. Final race odds are used only by A; C does not consume current-race odds.",
        },
        "comparison": totals,
        "winnerRescues_Ctop5_Aoutside5": rescues,
        "largestRaceDisagreements": disagreements,
        "structuralCoverage": coverage,
        "formalProspectiveEvidence": {
            "observedRacesAsOf20261030": 70,
            "formalAIncluded": 68,
            "formalSidecarMissing": 2,
            "missingRaceIDs": ["202604040301", "202608040801"],
            "importantCaveat": "Both missing-sidecar races had saved previous-evening A predictions, so these are not confirmed model-level A prediction failures.",
        },
    }

    out = OUT_DIR / "ac_coverage_audit_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_COVERAGE_AUDIT_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_COVERAGE_AUDIT_END")
    print("artifact", out)


if __name__ == "__main__":
    main()
