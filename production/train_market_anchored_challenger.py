#!/usr/bin/env python3
"""Historical market-anchored candidate: select ONLY using 2024 validation.

2025 uses provisional no-odds model trained <=2023; 2026 uses independently
frozen model fitted through 2025. Market benchmark is FINAL odds, never T-10.
Never change production forecasts/bets or tune on 2025/2026 outcomes.
"""
from __future__ import annotations
from datetime import datetime
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from evaluate_no_current_odds_2026_history import prepare_2026, verified_candidate
from train_no_current_odds_challenger import (
    HISTORY, JST, MODEL_PATH, REPORT_PATH, ROOT, FEATURES,
    ensure, fit_candidate, normalized_win, prepare_history,
    probability_triplet, split_years,
)
from train_top2_top3_models import BASE_COLS, plackett_luce_top2_top3

VERSION = "market-anchored-history-v1-20261122"
TARGET = ROOT / "research" / "model_candidates" / "market_anchored_v1"
POWERS = (0.90, 0.95, 1.00, 1.05, 1.10, 1.15)
WEIGHTS = (0.02, 0.05, 0.10, 0.15, 0.25)
METHODS = ("linear", "geometric")


def renorm(values, race_ids):
    values = np.asarray(values, dtype=float)
    ensure(len(values) > 0 and len(values) == len(race_ids) and
           np.isfinite(values).all() and (values > 0).all(),
           "invalid race strengths")
    x = pd.DataFrame({"rid": np.asarray(race_ids, dtype=str), "strength": values})
    total = x.groupby("rid", sort=False)["strength"].transform("sum").to_numpy()
    ensure(np.isfinite(total).all() and (total > 0).all(), "invalid race total")
    return values / total


def combine(market, history, race_ids, *, market_power, historical_weight, method):
    ensure(market_power in POWERS and
           historical_weight in (0.0, *WEIGHTS) and method in METHODS,
           "unregistered challenger parameters")
    p = renorm(np.maximum(np.asarray(market, dtype=float), 1e-12), race_ids)
    q = renorm(np.maximum(np.asarray(history, dtype=float), 1e-12), race_ids)
    m = renorm(np.power(p, market_power), race_ids)
    if historical_weight == 0:
        return m
    if method == "linear":
        x = (1.0 - historical_weight)*m + historical_weight*q
    else:
        x = np.power(m, 1.0 - historical_weight)*np.power(q, historical_weight)
    return renorm(x, race_ids)


def win_market(data):
    return normalized_win(data["市場確率"].to_numpy(float),
                          data["レースID"].astype(str))


def brier(y, p):
    return float(np.mean((np.asarray(y, dtype=float) - np.asarray(p, dtype=float)) ** 2))


def choose_2024(data, independent):
    ensure((data["日付"].dt.year == 2024).all(), "2024 only may choose parameters")
    market = win_market(data)
    races = data["レースID"].astype(str).to_numpy()
    outcomes = (data["着順"] == 1).to_numpy(float)
    variants = []
    for method in METHODS:
        for temp in POWERS:
            for weight in (0.0, *WEIGHTS):
                if weight == 0 and method != METHODS[0]:
                    continue
                pred = combine(market, independent, races, market_power=temp,
                               historical_weight=weight, method=method)
                variants.append({
                    "marketPower": temp, "historicalWeight": weight,
                    "method": method, "validationWinBrier": brier(outcomes, pred),
                })
    ensure(len(variants) == len(POWERS)*(1+len(METHODS)*len(WEIGHTS)),
           "unexpected registered experiment count")
    sort_key = lambda v: (v["validationWinBrier"], v["historicalWeight"],
                          abs(v["marketPower"]-1), v["method"])
    fixed = {
        "marketCalibrated": min((v for v in variants
                                 if v["historicalWeight"] == 0), key=sort_key),
        "historyAugmented": min((v for v in variants
                                 if v["historicalWeight"] > 0), key=sort_key),
    }
    return fixed, {
        "2024Races": int(data["レースID"].nunique()),
        "2024RawClosingMarketWinBrier": brier(outcomes, market),
        "2024NoOddsWinBrier": brier(outcomes, independent),
        "trials": len(variants),
    }


def score_all(data, independent, parameters, period):
    races = data["レースID"].astype(str).to_numpy()
    market = win_market(data)
    win = {"finalMarket": market, "historicalOnly": independent}
    for name, param in parameters.items():
        win[name] = combine(market, independent, races,
                            market_power=param["marketPower"],
                            historical_weight=param["historicalWeight"],
                            method=param["method"])
    preds = {}
    for name, prob in win.items():
        tmp = pd.DataFrame({"レースID": races, "市場確率": prob})
        a, b = plackett_luce_top2_top3(tmp)
        preds[name] = {"win": prob, "top2": a, "top3": b}
    out = {}
    for event, finish in (("win", 1), ("top2", 2), ("top3", 3)):
        actual = (data["着順"] <= finish).to_numpy(dtype=float)
        scores = {name: brier(actual, p[event]) for name, p in preds.items()}
        out[event] = {
            "pooledHorseBrier": scores,
            "horsePooledDifferenceVsFinalMarket": {
                name: scores[name] - scores["finalMarket"]
                for name in ("marketCalibrated", "historyAugmented")
            },
        }
    return {
        "period": period, "races": int(data["レースID"].nunique()),
        "horses": len(data),
        "firstDate": data["日付"].min().date().isoformat(),
        "lastDate": data["日付"].max().date().isoformat(),
        "events": out,
    }


def main():
    ensure(HISTORY.is_file() and MODEL_PATH.is_file() and REPORT_PATH.is_file(),
           "existing frozen historical model and source required")
    history_source_sha = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    meta = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    verified_candidate(meta, MODEL_PATH.read_bytes())
    hist = pd.read_parquet(HISTORY)
    ensure(list(hist.columns) == BASE_COLS, "historical source schema changed")

    older = prepare_history(hist)
    tr, val, holdout = split_years(older)
    provisional, rounds = fit_candidate(tr, val)
    ensure(rounds == meta["bestIterationChosenWithout2025"],
           "independent provisional model tree count changed")
    val_history = probability_triplet(val, provisional)["win"]
    chosen, selection = choose_2024(val, val_history)
    score2025 = score_all(holdout, probability_triplet(holdout, provisional)["win"],
                           chosen, "2025-independent-of-2025-training")

    # The 2025-refitted model is used ONLY for 2026, never 2025 validation.
    model = lgb.Booster(model_file=str(MODEL_PATH))
    ensure(list(model.feature_name()) == list(FEATURES), "frozen feature mismatch")
    recent = prepare_2026(hist)
    score2026 = score_all(recent, probability_triplet(recent, model)["win"],
                           chosen, "2026-independent-of-2026-training")

    strategy = {
        "schemaVersion": 1, "version": VERSION,
        "status": "fixed_on_2024_research_only",
        "researchOnly": True, "candidatePromotionApproved": False,
        "formalPredictionAIChanged": False,
        "formalRecommendationsEnabled": False, "autoBetEnabled": False,
        "independentModelVersion": meta["version"],
        "independentModelSHA256": meta["modelSha256"],
        "parametersSelectedFrom": "2024-only",
        "strategies": {
            name: {key: value for key, value in param.items()
                   if key != "validationWinBrier"}
            for name, param in chosen.items()
        },
    }
    report = {
        "schemaVersion": 1, "version": VERSION,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "status": "retrospective_research_only",
        "researchOnly": True, "candidatePromotionApproved": False,
        "formalPredictionAIChanged": False,
        "formalRecommendationsEnabled": False, "autoBetEnabled": False,
        "historicalSourceSHA256": history_source_sha,
        "independentModelSHA256": meta["modelSha256"],
        "trainingEndFor2025Assessment": "2023-12-31",
        "modelRefitEndFor2026Assessment": "2025-12-31",
        "selectionPeriod": "2024-01-01..2024-12-31",
        "selection": chosen, "selectionEvidence": selection,
        "test2025": score2025, "test2026": score2026,
        "limitations": [
            "The market inputs are closing odds in retrospective years, not T10.",
            "2024 used for early stopping AND experiment-selection, never a holdout.",
            "2025 holdout uses model trained through 2023, NOT refitted through 2025.",
            "2026 uses a separately frozen model trained through 2025.",
            "2025 and 2026 were explored in this broader project, not fully pristine.",
            "The experiment grid and weights are fixed before looking at 2025/2026 results.",
            "Prospective matched first-frozen T10 races required to compare against production AI.",
            "No model adoption, betting, ticket recommendation or ROI claim.",
        ],
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "frozen_strategy.json").write_text(json.dumps(
        strategy, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (TARGET / "research.json").write_text(json.dumps(
        report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    lines = [
        "# Market-anchored AI candidate — retrospective research", "",
        "Pre-registered coefficient selection: 2024 only.",
        "Selection: " + json.dumps(strategy["strategies"], ensure_ascii=False),
        "2025 scored on 2023-trained model; 2026 scored on 2025-refitted model.",
        "", "| Period | Outcome | Final-market Brier | Calibrated-market Brier | Market+history Brier | Market+history minus market |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for yr, period in (("2025", score2025), ("2026", score2026)):
        for event in ("win", "top2", "top3"):
            r = period["events"][event]
            scores = r["pooledHorseBrier"]
            lines.append(f"| {yr} | {event} | {scores['finalMarket']:.6f} | "
                         f"{scores['marketCalibrated']:.6f} | "
                         f"{scores['historyAugmented']:.6f} | "
                         f"{r['horsePooledDifferenceVsFinalMarket']['historyAugmented']:+.6f} |")
    lines += [
        "", "Market benchmark uses *closing odds*, which are unavailable at T-10.",
        "No direct production AI T-10 superiority is established.",
        "Prospective results must remain a separate frozen cohort.", "",
    ]
    (TARGET / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print("MARKET_ANCHORED_HISTORICAL_RESEARCH",
          json.dumps({"params": strategy["strategies"],
                      "2025Win": score2025["events"]["win"]["pooledHorseBrier"],
                      "2026Win": score2026["events"]["win"]["pooledHorseBrier"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
