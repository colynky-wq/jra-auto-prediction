#!/usr/bin/env python3
"""Research a 3rd-place-or-better *longshot* model conditional on pre-race odds.

Scope: sixth favourite or worse AND win odds >= 10.0. Use existing historical
horse/jockey/trainer features to learn a REGULARIZED CORRECTION to market-
implied Top-3 probability, not an independent story about one surprising horse.

2014-23 fitting -> 2024 early stopping AND multiplier selection -> 2025
chronological holdout. Refit through 2025 only AFTER scoring holdout, then
score 2026 with the frozen refit. 2026 labels never enter model fitting/tuning.
Historical markets are *closing odds*, not same-timestamp T-10 predictions.
No formal forecasts, tickets, wagers, model promotion or beta activation.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from evaluate_no_current_odds_2026_history import prepare_2026
from train_no_current_odds_challenger import (
    ROOT, HISTORY, FEATURES, JST, ensure, prepare_history, split_years,
    verify_features,
)
from train_top2_top3_models import BASE_COLS, binary_logloss, plackett_luce_top2_top3

VERSION = "longshot-top3-market-residual-v1-20261123"
TARGET = ROOT / "research" / "model_candidates" / "longshot_top3_v1"
MODEL_PATH = TARGET / "longshot_top3_residual.txt"
METADATA = TARGET / "metadata.json"
REPORT = TARGET / "research.json"
WEIGHTS = (0.0, 0.25, 0.5, 1.0)
POPRANK_MIN = 6
WINODDS_MIN = 10.0
TRAIN_END = "2023-12-31"
VALID_YEAR = 2024
HOLDOUT_YEAR = 2025
SEED = 20261123
PARAMS = {
    "objective": "binary", "metric": "binary_logloss",
    "learning_rate": 0.035, "num_leaves": 9,
    "min_data_in_leaf": 450, "lambda_l2": 25.0,
    "feature_fraction": 0.85, "seed": SEED,
    "feature_fraction_seed": SEED, "verbosity": -1,
    "num_threads": 2, "deterministic": True, "force_col_wise": True,
}


def sigmoid(raw):
    return 1.0/(1.0+np.exp(-np.clip(np.asarray(raw, dtype=float), -35., 35.)))


def logit(p):
    x = np.clip(np.asarray(p, dtype=float), 1e-6, 1-1e-6)
    return np.log(x/(1-x))


def odds_underdog(popularity, odds):
    """Historical popularity and odds must be from the SAME race."""
    pop = np.asarray(popularity, dtype=float)
    winodds = np.asarray(odds, dtype=float)
    ensure(pop.shape == winodds.shape and
           np.isfinite(pop).all() and np.isfinite(winodds).all(),
           "invalid underdog identification")
    return (pop >= POPRANK_MIN) & (winodds >= WINODDS_MIN)


def prepare_longshot(full: pd.DataFrame):
    """Calculate market Top-3 using the WHOLE field, THEN subset underdogs."""
    ensure(len(full) > 0 and
           all(c in full.columns for c in ("レースID", "馬番", "市場確率",
                                             "人気", "単勝オッズ", "着順", "頭数", "日付")),
           "incomplete historical race data")
    # If an entrant was dropped for missing odds, race-local Top3 inferred
    # from remaining horses would be biased. Drop the entire incomplete race.
    race_counts = full.groupby("レースID", sort=False)["馬番"].transform("size")
    complete = race_counts == full["頭数"].astype(int)
    d = full.loc[complete].copy().reset_index(drop=True)
    ensure(len(d) > 0, "no complete-entrant races for odds-based Top3")
    ensure((pd.to_numeric(d["市場確率"], errors="coerce") > 0).all(),
           "invalid full-field market probabilities")
    _, market3 = plackett_luce_top2_top3(d)
    ensure(np.isfinite(market3).all(), "cannot derive complete-field Top3 odds")
    d["市場3着内基準"] = market3
    d = d.loc[odds_underdog(d["人気"], d["単勝オッズ"])].copy()
    d = d.reset_index(drop=True)
    ensure(len(d) > 0, "no qualifying unpopular runners")
    verify_features(d)
    return d


def split_longshot(d):
    dates = pd.to_datetime(d["日付"], errors="coerce")
    train = d.loc[dates <= pd.Timestamp(TRAIN_END)].reset_index(drop=True)
    valid = d.loc[dates.dt.year == VALID_YEAR].reset_index(drop=True)
    hold = d.loc[dates.dt.year == HOLDOUT_YEAR].reset_index(drop=True)
    ensure(all(len(z) >= 1500 for z in (train,valid,hold)),
           "not enough underdog entries in independent year windows")
    ensure(train["日付"].max() < valid["日付"].min() <
           hold["日付"].min(), "leaking chronological partitions")
    return train, valid, hold


def training_targets(data):
    y = (data["着順"] <= 3).astype(int).to_numpy()
    baseline = data["市場3着内基準"].astype(float).to_numpy()
    ensure(len(y) > 0 and y.sum() > 0 and y.sum() < len(y) and
           np.isfinite(baseline).all() and
           ((baseline > 0)&(baseline < 1)).all(),
           "degenerate underdog Top3 training targets")
    return y, baseline


def fit(train, valid=None, num_boost_round=None):
    y, base = training_targets(train)
    x = verify_features(train)
    ds = lgb.Dataset(x, label=y, init_score=logit(base),
                     feature_name=list(FEATURES),free_raw_data=False)
    if valid is not None:
        yval, baseval = training_targets(valid)
        dval = lgb.Dataset(verify_features(valid), label=yval,
                           init_score=logit(baseval),
                           feature_name=list(FEATURES),reference=ds,
                           free_raw_data=False)
        model = lgb.train(
            PARAMS, ds, num_boost_round=300,
            valid_sets=[dval],valid_names=["2024"],
            callbacks=[lgb.early_stopping(45, verbose=False),
                       lgb.log_evaluation(100)])
        rounds = int(model.best_iteration or 300)
    else:
        ensure(type(num_boost_round) is int and 1 <= num_boost_round <= 300,
               "unregistered final tree count")
        rounds = num_boost_round
        model = lgb.train(PARAMS,ds,num_boost_round=rounds)
    ensure(1 <= rounds <= 300 and model.feature_name() == list(FEATURES),
           "model identity or early-stopping round count invalid")
    return model, rounds


def infer_without_results(frame, baseline_top3, model, strength):
    """Infer from only frozen pre-race inputs, with zero result/label access."""
    ensure(strength in WEIGHTS, "unregistered prevalidated residual strength")
    ensure(model.feature_name() == list(FEATURES),
           "feature mismatch for underdog candidate")
    x = verify_features(frame)
    baseline = np.asarray(baseline_top3, dtype=float)
    ensure(len(baseline) == len(x) and
           np.isfinite(baseline).all() and
           ((baseline > 0) & (baseline < 1)).all(),
           "incomplete T10 market Top3 baseline")
    raw = np.asarray(model.predict(
        x, raw_score=True, validate_features=True), dtype=float)
    ensure(len(raw) == len(x) and np.isfinite(raw).all(),
           "invalid model residual")
    calibrated = sigmoid(logit(baseline) + strength*raw)
    ensure(np.isfinite(calibrated).all() and
           ((calibrated > 0) & (calibrated < 1)).all(),
           "invalid Top3 probability output")
    return calibrated


def probability(data, model, strength):
    _, baseline = training_targets(data)
    return infer_without_results(data, baseline, model, strength)


def choose_multiplier(validation, provisional):
    ensure(pd.to_datetime(validation["日付"]).dt.year.eq(VALID_YEAR).all(),
           "only 2024 outcomes may choose the residual weight")
    y, market = training_targets(validation)
    options = [{
        "residualMultiplier":weight,
        "brier":float(np.mean((probability(validation,provisional,weight)-y)**2))
    } for weight in WEIGHTS]
    # If a residual is not useful, pick 0 (plain market) on validation.
    selected = min(options, key=lambda v:(v["brier"],v["residualMultiplier"]))
    return selected,options


def score(data, model, weight, label):
    y, market = training_targets(data)
    actual = probability(data,model,weight)
    before = probability(data,model,1.0)
    mid = market
    ids = data["レースID"].astype(str).to_numpy()
    deltas = pd.DataFrame({"race":ids,"delta":(actual-y)**2-(market-y)**2}).groupby(
        "race",sort=False)["delta"].mean().to_numpy()
    bootstrap = None
    if len(deltas) >= 30:
        rng = np.random.default_rng(SEED)
        # Vectorized whole-race resampling, not treating horses as independent.
        sampled=rng.integers(0,len(deltas),size=(1000,len(deltas)))
        distribution = deltas[sampled].mean(axis=1)
        bootstrap=[float(np.quantile(distribution,.025)),
                   float(np.quantile(distribution,.975))]
    picks={"market":0,"candidate":0}
    picked_underdog_races=0
    for _,indexes in data.groupby("レースID",sort=False).indices.items():
        p = np.asarray(indexes,dtype=int)
        picked_underdog_races+=int(y[p].any())
        picks["market"]+=int(y[p[np.argmax(mid[p])]]==1)
        picks["candidate"]+=int(y[p[np.argmax(actual[p])]]==1)
    return {
        "period":label, "races":int(data["レースID"].nunique()),
        "underdogHorseRows":len(data),
        "underdogPodiumRows":int(y.sum()),
        "underdogTop3BaseRate":float(y.mean()),
        "chosenResidualMultiplier":weight,
        "brier":{"closingMarketTop3":float(np.mean((mid-y)**2)),
                 "residualUnshrunk":float(np.mean((before-y)**2)),
                 "selectedCandidate":float(np.mean((actual-y)**2))},
        "logloss":{"closingMarketTop3":binary_logloss(y,mid),
                   "selectedCandidate":binary_logloss(y,actual)},
        "candidateMinusMarketHorseBrier":
            float(np.mean((actual-y)**2)-np.mean((mid-y)**2)),
        "candidateMinusMarketEqualRaceBrier":float(deltas.mean()),
        "pairedRaceBootstrap95":bootstrap,
        "raceCountWithAtLeastOneLongshotTop3":picked_underdog_races,
        "topRankedLongshotPodiumHits":picks,
        "qualifyingStartsPerRace":float(len(data)/data["レースID"].nunique()),
    }


def main():
    ensure(HISTORY.is_file(),"missing production historical parquet")
    source_sha=hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    hist=pd.read_parquet(HISTORY)
    ensure(list(hist.columns)==BASE_COLS,"historical schema differs")
    older=prepare_history(hist)
    train,valid,hold=split_longshot(prepare_longshot(older))
    provisional,rounds=fit(train,valid)
    selected,grid=choose_multiplier(valid,provisional)
    score25=score(hold,provisional,selected["residualMultiplier"],
                  "2025-historical-holdout-trained-through-2023")
    # Separate forward-out-of-training-period research, no 2026 fitting.
    newest=prepare_2026(hist)
    recent=prepare_longshot(newest)
    ensure(pd.to_datetime(recent["日付"]).dt.year.eq(2026).all(),
           "2026 retrospective cohort identity lost")
    # Final candidate is refitted on <=2025 only; never score this model
    # on 2025 as though its results were independent.
    full=pd.concat([train,valid,hold],ignore_index=True)
    final,_=fit(full,num_boost_round=rounds)
    TARGET.mkdir(parents=True,exist_ok=True)
    final.save_model(str(MODEL_PATH))
    model_sha=hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    score26=score(recent,final,selected["residualMultiplier"],
                  "2026-out-of-model-training-retrospective")
    gain=final.feature_importance(importance_type="gain")
    important=sorted(zip(FEATURES,gain),key=lambda x:-x[1])
    metadata={
        "schemaVersion":1,"version":VERSION,
        "generatedAtJST":datetime.now(JST).isoformat(),
        "status":"historical_research_only",
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,
        "formalRecommendationsEnabled":False,"autoBetEnabled":False,
        "modelName":"longshot_top3_residual.txt",
        "modelSha256":model_sha,
        "sourceHistorySha256":source_sha,
        "features":list(FEATURES),
        "featureCount":len(FEATURES),
        "popularityMinimum":POPRANK_MIN,
        "winOddsMinimum":WINODDS_MIN,
        "longshotFilter":"popularity >= 6 AND win odds >= 10.0",
        "target":"P(finish in first three)",
        "baseline":"Plackett-Luce Top3 implied by whole-field market win odds",
        "modelFormula":"sigmoid(logit(full-field market Top3)+alpha*LightGBM raw residual)",
        "trainThroughFor2025Test":TRAIN_END,
        "yearUsedForEarlyStoppingAndMultiplier":2024,
        "refitThroughFor2026Test":"2025-12-31",
        "bestIterationsChosenWithout2025":rounds,
        "selectedResidualMultiplier":selected["residualMultiplier"],
        "training":{"rows":len(train),"races":int(train["レースID"].nunique()),
                    "validationRows":len(valid),
                    "validationRaces":int(valid["レースID"].nunique()),
                    "holdoutRows":len(hold),
                    "holdoutRaces":int(hold["レースID"].nunique()),
                    "finalRefitRows":len(full)},
    }
    report={
        "schemaVersion":1,"version":VERSION,
        "generatedAtJST":metadata["generatedAtJST"],
        "status":"retrospective_research_only",
        "researchOnly":True,"candidatePromotionApproved":False,
        "formalPredictionAIChanged":False,"autoBetEnabled":False,
        "sourceHistorySha256":source_sha,"candidateModelSha256":model_sha,
        "selection2024":{"selected":selected,"grid":grid,
                         "underdogs":len(valid),
                         "races":int(valid["レースID"].nunique())},
        "holdout2025":score25,"retrospective2026":score26,
        "featureImportanceGainTop15":[
            {"feature":str(f),"gain":float(v)} for f,v in important[:15]],
        "limitations":[
            "The historical odds are closing odds, not frozen T10 values.",
            "Horse popularity and qualifying odds are from closing odds in retrospective data.",
            "Only 2024 outcomes choose rounds and residual weight; no 2025/26 tuning.",
            "2025 scoring uses <=2023 model; refit <=2025 is scored ONLY on 2026.",
            "2025/26 already studied elsewhere in this project, not pristine project-wide holdouts.",
            "Feature contributions and gain show ASSOCIATION, not a causal explanation for an upset.",
            "The market Top3 baseline is the approximate Plackett-Luce win-odds implied probability.",
            "Selection among unpopular horses is NOT a real-money profit or payout claim.",
            "Only separately first-frozen future T10 data can test genuine operational usefulness.",
            "Never auto-replace formal AI, beta policy, buying signals or model thresholds.",
        ],
    }
    METADATA.write_text(json.dumps(metadata,ensure_ascii=False,indent=2,
                                    allow_nan=False)+"\n",encoding="utf-8")
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                  allow_nan=False)+"\n",encoding="utf-8")
    lines=[
        "# Popularity-6+ and win-odds-10+ longshot Top3 research", "",
        f"Model {VERSION}; first-frozen future deployment: NOT approved",
        f"Target: >=6th favourite AND win odds >=10; Top3 finish.",
        f"2024 selected multiplier {selected['residualMultiplier']}, model rounds {rounds}.",
        "",
        "| Period | Races | Underdog starts | Top3 rate | Closing market Brier | AI candidate Brier | Difference | Paired 95% |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for year,result in (("2025",score25),("2026",score26)):
        lines.append(
            f"| {year} | {result['races']} | {result['underdogHorseRows']} | "
            f"{result['underdogTop3BaseRate']:.4f} | "
            f"{result['brier']['closingMarketTop3']:.6f} | "
            f"{result['brier']['selectedCandidate']:.6f} | "
            f"{result['candidateMinusMarketHorseBrier']:+.6f} | "
            f"{result['pairedRaceBootstrap95']} |")
    lines+=["","## Important descriptive associations", ""]
    lines +=[f"- {r['feature']}: gain={r['gain']:.1f}"
             for r in report["featureImportanceGainTop15"]]
    lines+=["", "Association is not causation. Do not claim an upset can be known in advance.",
             "Market reference uses *closing* odds, NOT frozen pre-race T10.",
             "No ROI conclusion, no formal model promotion and no automatic betting.", ""]
    (TARGET/"README.md").write_text("\n".join(lines),encoding="utf-8")
    print("LONGSHOT_TOP3_RESEARCH_OK",json.dumps({
        "multiplier2024":selected,"2025":score25,
        "2026":score26,"topFeatures":report["featureImportanceGainTop15"][:6],
    },ensure_ascii=False,allow_nan=False))


if __name__=="__main__":
    main()
