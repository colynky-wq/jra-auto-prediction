#!/usr/bin/env python3
"""Research-only LightGBM winner challenger without CURRENT race win odds.

Training: <=2023; 2024 chooses tree count; 2025 is a chronological
retrospective holdout. A final separate model is refit <=2025 only after
the holdout has been scored. Production model files are NEVER touched.

The live candidate does not use current-market odds, market logit, or
current popularity. A *previous race's* popularity is allowed historical
information. Closing-market probabilities are used ONLY in retrospective
comparison, never as input/initial score/label to the challenger.

IMPORTANT: 2025 was used previously in the broader KeibaAI project. This
is not a never-seen project-wide test; only truly first-frozen future
forecasts can support live promotion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from train_top2_top3_models import (
    BASE_COLS, binary_logloss, brier, plackett_luce_top2_top3
)

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "production" / "production_history.parquet"
CANDIDATE = ROOT / "research" / "model_candidates" / "no_current_odds_v1"
MODEL_PATH = CANDIDATE / "win_lightgbm.txt"
REPORT_PATH = CANDIDATE / "metadata.json"
VERSION = "no-current-odds-challenger-v1-20261122"
JST = timezone(timedelta(hours=9))
SEED = 20261122
FEATURES = tuple(f for f in ALLF if f != "市場ロジット")
TRAIN_END = pd.Timestamp("2023-12-31")
VALID_START = pd.Timestamp("2024-01-01")
VALID_END = pd.Timestamp("2024-12-31")
HOLDOUT_START = pd.Timestamp("2025-01-01")
HOLDOUT_END = pd.Timestamp("2025-12-31")
PARAMS = {
    "objective": "binary", "metric": "binary_logloss",
    "learning_rate": 0.03, "num_leaves": 15,
    "min_data_in_leaf": 200, "feature_fraction": 0.9,
    "lambda_l2": 10.0, "verbosity": -1,
    "num_threads": 2, "seed": SEED,
    "feature_fraction_seed": SEED,
    "deterministic": True, "force_col_wise": True,
}


def ensure(value, message):
    if not value:
        raise ValueError("NO_CURRENT_ODDS_RESEARCH_BLOCKED: " + message)


def verify_features(frame: pd.DataFrame) -> pd.DataFrame:
    """The model has exactly 36 explicitly ordered, finite pre-race inputs."""
    ensure("市場ロジット" not in FEATURES, "current-market feature leakage")
    ensure(len(FEATURES) == 36 and len(set(FEATURES)) == len(FEATURES),
           "unrecognized historical feature schema")
    ensure(tuple(f for f in ALLF if f != "市場ロジット") == FEATURES,
           "feature order changed")
    ensure(all(f in frame.columns for f in FEATURES), "missing pre-race features")
    data = frame.loc[:, list(FEATURES)].astype(np.float32)
    ensure(np.isfinite(data.to_numpy()).all(), "invalid pre-race feature")
    return data


def normalized_win(raw, races) -> np.ndarray:
    """Race-local normalization, independent of any current race odds."""
    p = np.asarray(raw, dtype=np.float64).reshape(-1)
    ensure(len(p) == len(races) and len(p) > 0, "prediction size mismatch")
    ensure(np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all(),
           "invalid raw prediction")
    d = pd.DataFrame({"race": list(races), "p": np.maximum(p, 1e-12)})
    den = d.groupby("race", sort=False)["p"].transform("sum").to_numpy()
    ensure(np.isfinite(den).all() and (den > 0).all(), "invalid race normalization")
    return d["p"].to_numpy() / den


def probability_triplet(frame: pd.DataFrame, model: lgb.Booster) -> dict:
    X = verify_features(frame)
    ensure(list(model.feature_name()) == list(FEATURES),
           "candidate feature identity mismatch")
    raw = model.predict(X, validate_features=True)
    win = normalized_win(raw, frame["レースID"].astype(str))
    # Reuse the mathematically exact Plackett-Luce marginal calculation.
    # The column is named '市場確率' by the legacy pure helper; its VALUES
    # here are the no-current-odds model's win probabilities, not market data.
    rank_frame = pd.DataFrame({
        "レースID": frame["レースID"].astype(str).to_numpy(),
        "市場確率": win,
    })
    top2, top3 = plackett_luce_top2_top3(rank_frame)
    ensure(np.isfinite(top2).all() and np.isfinite(top3).all(),
           "incomplete PL marginal probabilities")
    ensure(((win <= top2 + 1e-8) & (top2 <= top3 + 1e-8)).all(),
           "win/top2/top3 probability monotonicity")
    return {"win": win, "top2": top2, "top3": top3}


def evaluate_event(frame, actual, market, candidate, label):
    model_brier = brier(actual, candidate)
    market_brier = brier(actual, market)
    paired = pd.DataFrame({
        "race": frame["レースID"].to_numpy(),
        "delta": (candidate - actual) ** 2 - (market - actual) ** 2,
    }).groupby("race", sort=False)["delta"].mean().to_numpy(dtype=float)
    rng = np.random.default_rng(SEED)
    interval = None
    if len(paired) >= 30:
        boot = np.asarray([
            np.mean(paired[rng.integers(0, len(paired), size=len(paired))])
            for _ in range(2000)
        ])
        interval = [float(np.quantile(boot, 0.025)),
                    float(np.quantile(boot, 0.975))]
    temp = frame[["レースID", "馬番"]].copy()
    temp["actual"] = actual
    temp["market"] = market
    temp["candidate"] = candidate
    market_hits = 0
    candidate_hits = 0
    for _, group in temp.groupby("レースID", sort=False):
        market_hits += int(group.loc[group["market"].idxmax(), "actual"] == 1)
        candidate_hits += int(group.loc[group["candidate"].idxmax(), "actual"] == 1)
    return {
        "event": label, "raceCount": len(paired), "horseRows": len(frame),
        "marketFinalOddsBrier": market_brier,
        "candidateNoCurrentOddsBrier": model_brier,
        "candidateMinusFinalMarketBrier": model_brier - market_brier,
        "pairedRaceBootstrap95": interval,
        "marketFinalOddsLogloss": binary_logloss(actual, market),
        "candidateNoCurrentOddsLogloss": binary_logloss(actual, candidate),
        "marketTopPickHitRaces": market_hits,
        "candidateTopPickHitRaces": candidate_hits,
    }


def prepare_history(hist):
    ensure(list(hist.columns) == BASE_COLS, "23-column source schema mismatch")
    hist = hist.copy()
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    ensure(hist["日付"].notna().all(), "invalid historical date")
    work = hist.loc[hist["日付"] <= HOLDOUT_END].copy()
    ensure(not work.empty and not (work["日付"].dt.year >= 2026).any(),
           "2026 results must not enter training/evaluation")
    work = work.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    work = add_rating(work)
    work = add_features(work)
    eligible = (work["対象"].fillna(False).astype(bool) &
                work["着順"].notna() &
                ~work["障害"].fillna(False).astype(bool) &
                (work["頭数"] >= 3) &
                work["市場確率"].notna())
    d = work.loc[eligible].copy().reset_index(drop=True)
    # Exclude ambiguous/missing podium outcomes, never count them as losses.
    def complete_podium(g):
        finish = pd.to_numeric(g["着順"], errors="coerce")
        return (len(g) >= 3 and len(g) == g["馬番"].nunique()
                and all((finish == k).sum() == 1 for k in (1, 2, 3)))
    mask = d.groupby("レースID", sort=False).filter(complete_podium)
    d = mask.reset_index(drop=True)
    ensure(not d.empty, "no eligible races")
    verify_features(d)
    ensure(np.isfinite(d["市場確率"].astype(float).to_numpy()).all(),
           "invalid reference final odds")
    return d


def split_years(d):
    training = d.loc[d["日付"] <= TRAIN_END].reset_index(drop=True)
    valid = d.loc[(d["日付"] >= VALID_START) &
                  (d["日付"] <= VALID_END)].reset_index(drop=True)
    holdout = d.loc[(d["日付"] >= HOLDOUT_START) &
                    (d["日付"] <= HOLDOUT_END)].reset_index(drop=True)
    ensure(all(len(x) > 5000 for x in (training, valid, holdout)),
           "insufficient independent year windows")
    ensure(training["日付"].max() < valid["日付"].min() and
           valid["日付"].max() < holdout["日付"].min(),
           "chronological split violation")
    return training, valid, holdout


def fit_candidate(dtrain, dvalid):
    train = lgb.Dataset(
        verify_features(dtrain),
        label=(dtrain["着順"] == 1).astype(int).to_numpy(),
        feature_name=list(FEATURES),
        free_raw_data=False)
    valid = lgb.Dataset(
        verify_features(dvalid),
        label=(dvalid["着順"] == 1).astype(int).to_numpy(),
        feature_name=list(FEATURES),
        reference=train,
        free_raw_data=False)
    model = lgb.train(
        PARAMS, train, num_boost_round=350,
        valid_sets=[valid], valid_names=["2024"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=35, verbose=False),
            lgb.log_evaluation(period=100),
        ])
    rounds = int(model.best_iteration or 350)
    ensure(1 <= rounds <= 350, "invalid selected tree count")
    return model, rounds


def main():
    ensure(HISTORY.is_file(), "missing existing historical parquet")
    source_bytes = HISTORY.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    hist = pd.read_parquet(HISTORY)
    d = prepare_history(hist)
    training, valid, holdout = split_years(d)
    provisional, rounds = fit_candidate(training, valid)
    shadow = probability_triplet(holdout, provisional)

    # Closing market is a retrospective, later-timestamp reference ONLY.
    # It is not part of the candidate's inputs and is not an equivalent
    # T-10 benchmark. The official frozen current AI may have trained on
    # 2025, so we never report an in-sample comparison with that model.
    close_win = normalized_win(
        holdout["市場確率"].to_numpy(dtype=float), holdout["レースID"])
    comparison = pd.DataFrame({
        "レースID": holdout["レースID"].to_numpy(),
        "市場確率": close_win,
    })
    close2, close3 = plackett_luce_top2_top3(comparison)
    market = {"win": close_win, "top2": close2, "top3": close3}
    outcomes = {
        "win": (holdout["着順"] == 1).astype(float).to_numpy(),
        "top2": (holdout["着順"] <= 2).astype(float).to_numpy(),
        "top3": (holdout["着順"] <= 3).astype(float).to_numpy(),
    }
    events = {key: evaluate_event(holdout, outcomes[key], market[key],
                                  shadow[key], key)
              for key in ("win", "top2", "top3")}

    # Refit AFTER reporting the 2025 holdout: never score this refitted
    # model on 2025 as if it had not seen those outcomes.
    final = lgb.train(
        PARAMS,
        lgb.Dataset(verify_features(d),
                    label=(d["着順"] == 1).astype(int).to_numpy(),
                    feature_name=list(FEATURES)),
        num_boost_round=rounds)
    CANDIDATE.mkdir(parents=True, exist_ok=True)
    final.save_model(str(MODEL_PATH))
    model_sha = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    report = {
        "schemaVersion": 1, "version": VERSION,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "sourceHistorySha256": source_sha256,
        "sourceHistoryRows": len(hist),
        "sourceLatestDate": hist["日付"].max().strftime("%Y-%m-%d"),
        "researchOnly": True,
        "autoBetEnabled": False,
        "formalPredictionAIChanged": False,
        "candidatePromotionApproved": False,
        "modelFile": "win_lightgbm.txt",
        "modelSha256": model_sha,
        "features": list(FEATURES),
        "featureCount": len(FEATURES),
        "currentMarketInputs": [],
        "historicalMarketSignalRetained": ["前走人気f"],
        "target": "P(win); Top2/3 derived from no-odds win using Plackett-Luce",
        "trainThrough": "2023-12-31",
        "earlyStopping": "2024-01-01..2024-12-31",
        "retrospectiveHoldout": "2025-01-01..2025-12-31",
        "finalRefitThrough": "2025-12-31",
        "bestIterationChosenWithout2025": rounds,
        "training": {
            "rows": len(training),
            "races": training["レースID"].nunique(),
            "validationRows": len(valid),
            "validationRaces": valid["レースID"].nunique(),
            "holdoutRows": len(holdout),
            "holdoutRaces": holdout["レースID"].nunique(),
            "finalRefitRows": len(d),
            "finalRefitRaces": d["レースID"].nunique(),
        },
        "holdoutComparison": events,
        "limitations": [
            "Only earlier historical features are model inputs; current market logit removed.",
            "Previous-race popularity remains an allowed, past market-related feature.",
            "Reference 2025 odds are post-race final odds, NOT T-10 odds.",
            "2025 is an internal chronological holdout, not an untouched project-wide dataset.",
            "Final model is fit through 2025 and must NEVER be re-scored on 2025 as out-of-sample.",
            "2026 strictly first-frozen T-10 shadow results required to assess live usefulness.",
            "No automatic model adoption, betting, threshold adjustment, or ROI claim.",
        ],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    md = [
        "# No-current-odds candidate (research only)", "",
        f"Training rows: {len(training):,}; validation 2024: {len(valid):,}; 2025 historical holdout: {len(holdout):,}.",
        f"Model refit through 2025, best rounds selected on 2024: {rounds}.",
        "The model uses 36 features and excludes current-race market logit.",
        "The market benchmark below uses *final* odds unavailable at T-10.", "",
        "| Event | Candidate Brier | Final-market Brier | Delta (candidate - final market) | Race bootstrap 95% |",
        "|---|---:|---:|---:|---|",
    ]
    for key in ("win", "top2", "top3"):
        e = events[key]
        md.append(f"| {key} | {e['candidateNoCurrentOddsBrier']:.6f} | "
                  f"{e['marketFinalOddsBrier']:.6f} | "
                  f"{e['candidateMinusFinalMarketBrier']:+.6f} | "
                  f"{e['pairedRaceBootstrap95']} |")
    md += ["", "No promotion; compare strictly first-frozen future forecasts.",
           "2025 has been examined by earlier project research and is not pristine evidence.", ""]
    (CANDIDATE / "README.md").write_text("\n".join(md), encoding="utf-8")
    print("NO_CURRENT_ODDS_HISTORICAL_RESEARCH_OK",
          json.dumps({"sourceRows": len(hist), "holdoutRaces": len(holdout),
                      "bestRounds": rounds,
                      "holdout": {key: e["candidateMinusFinalMarketBrier"]
                                  for key, e in events.items()},
                      "modelSha256": model_sha}, ensure_ascii=False))


if __name__ == "__main__":
    main()
