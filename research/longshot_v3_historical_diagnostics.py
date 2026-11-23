#!/usr/bin/env python3
"""Retrospective diagnostic for fixed, frozen longshot V3 probability mixtures.

Historical closing odds are a later-time reference, NOT equivalent to formal T-10.
No fitting of new models, feature selection on evaluation years, or deployment.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb


def load_modules(root):
    sys.path.insert(0, str(root / "production"))
    from research_longshot_top3_v2 import (
        FEATURE_SETS, add_extras, place_history, predict, race_losses, top_picks)
    from train_longshot_top3 import prepare_longshot, split_longshot
    from train_no_current_odds_challenger import prepare_history, HISTORY
    from evaluate_no_current_odds_2026_history import prepare_2026
    from train_top2_top3_models import BASE_COLS, binary_logloss
    return (FEATURE_SETS, add_extras, place_history, predict, race_losses, top_picks,
            prepare_longshot, split_longshot, prepare_history, prepare_2026, BASE_COLS,
            binary_logloss)


def bootstrap_date_cluster(d, proposed, reference, race_losses, repeats=1200):
    tmp = pd.DataFrame({
        "race": d["レースID"].astype(str),
        "date": pd.to_datetime(d["日付"]).dt.strftime("%Y-%m-%d"),
        "lossdelta": (np.asarray(proposed)-np.asarray(d["着順"] <= 3, dtype=float))**2
                   - (np.asarray(reference)-np.asarray(d["着順"] <= 3, dtype=float))**2,
    })
    per_race = tmp.groupby("race", sort=False).agg(date=("date","first"), delta=("lossdelta","mean"))
    by_day = per_race.groupby("date", sort=False)["delta"].agg(["sum", "count"])
    if len(by_day) < 20:
        return None
    sums = by_day["sum"].to_numpy()
    counts = by_day["count"].to_numpy()
    rng = np.random.default_rng(20261123)
    draws = rng.integers(0, len(sums), (repeats, len(sums)))
    means = sums[draws].sum(axis=1)/counts[draws].sum(axis=1)
    return [float(np.percentile(means, 2.5)),float(np.percentile(means, 97.5))]


def evaluate(d, model, features, funcs):
    _, _, _, predict, race_losses, top_picks, *_rest = funcs
    y = np.asarray(d["着順"] <= 3, dtype=float)
    market = np.asarray(d["市場3着内基準"], dtype=float)
    candidate = predict(d, model, features, weight=1.0)
    series = {
        "closing_market": market, "specialist_v2": candidate,
        "market75_v2_25": .75*market+.25*candidate,
        "market50_v2_50": .50*market+.50*candidate,
    }
    outcomes = {}
    for name, p in series.items():
        picks = top_picks(d,p)
        outcomes[name] = {
            "brier_pooled": float(np.mean((p-y)**2)),
            "brier_equal_race": float(race_losses(d,p).mean()),
            "logloss_pooled": float(-np.mean(y*np.log(np.clip(p,1e-10,1))
                    +(1-y)*np.log(np.clip(1-p,1e-10,1)))),
            "one_per_race_top3": int((picks["着順"]<=3).sum()),
        }
    paired = {}
    for name in ("specialist_v2","market75_v2_25","market50_v2_50"):
        delta = race_losses(d,series[name])-race_losses(d,series["closing_market"])
        paired[name] = {
            "vs_market_equal_race_brier_delta":float(delta.mean()),
            "vs_market_date_cluster_95ci":bootstrap_date_cluster(
                d,series[name],series["closing_market"],race_losses),
        }
    return {"races":int(d["レースID"].nunique()),
            "horse_rows":len(d),
            "dates":int(pd.to_datetime(d["日付"]).dt.date.nunique()),
            "outcomes":outcomes,"paired":paired}


def run(root):
    report_path = root/"research/model_candidates/longshot_top3_v2/research.json"
    registry = json.loads(report_path.read_text(encoding="utf-8"))
    history_path = root/"production/production_history.parquet"
    source_sha = hashlib.sha256(history_path.read_bytes()).hexdigest()
    if source_sha != registry["sourceHistorySha256"]:
        raise ValueError("Historical source hash does not match frozen V2 training registry")
    feats = registry["selection2024"]["selected"]
    if feats["featureSet"]!="place_and_market" or feats["weight"]!=1.0:
        raise ValueError("Unexpected prior-year model selection")
    funcs = load_modules(root)
    feature_sets, add_extras, place_history, _predict, _losses, _picks, prepare_longshot, split_longshot, prepare_history, prepare_2026, base_cols, _ll = funcs
    data = pd.read_parquet(history_path)
    if list(data.columns)!=base_cols:
        raise ValueError("Historical 23-column feature schema changed")
    print("RETRO_V3_BUILD_PRIORS",len(data),flush=True)
    priors = place_history(data)
    safe = data.copy()
    safe["馬体重"] = pd.to_numeric(safe["馬体重"],errors="coerce").fillna(480)
    print("RETRO_V3_BUILD_2025",flush=True)
    older = add_extras(prepare_longshot(prepare_history(safe)),priors)
    _, _, test25 = split_longshot(older)
    print("RETRO_V3_BUILD_2026",flush=True)
    test26 = add_extras(prepare_longshot(prepare_2026(safe)),priors)
    model_dir = root/"research/model_candidates/longshot_top3_v2"
    models = {}
    for year, filename in (("2025","model_through2023.txt"),("2026","model_through2025.txt")):
        f=model_dir/filename
        if hashlib.sha256(f.read_bytes()).hexdigest()!=registry["modelHashes"][filename]:
            raise ValueError("Frozen model checksum mismatch for "+year)
        models[year]=lgb.Booster(model_file=str(f))
    result = {
        "status":"retrospective_not_pristine_holdout",
        "source_sha256":source_sha,
        "warning":"Historical CLOSING odds; not formal 10-min odds. 2025 and 2026 were previously studied.",
        "research_only":True,"approved_for_production":False,"auto_bet_enabled":False,
        "selection":"V2 from 2024 feature-selection protocol; mixing coefficients predefined without test-year optimization",
        "2025":evaluate(test25,models["2025"],feature_sets["place_and_market"],funcs),
        "2026":evaluate(test26,models["2026"],feature_sets["place_and_market"],funcs),
    }
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--output",type=Path,default=Path("longshot_v3_historical_result.json"))
    args=parser.parse_args()
    result=run(args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    for year in ("2025","2026"):
        y=result[year]
        print("LONGSHOT_V3_HISTORY",year,"RACES",y["races"],"ROWS",y["horse_rows"])
        print("METRICS",json.dumps(y["outcomes"],ensure_ascii=False))
        print("PAIRED",json.dumps(y["paired"],ensure_ascii=False))


if __name__=="__main__":
    main()
