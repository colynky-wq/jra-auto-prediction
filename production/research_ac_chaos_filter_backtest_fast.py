from __future__ import annotations

import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF
from research_ac_betting_backtest import prepare_frame, settle
from research_ac_chaos_filter_backtest import CHAOS_CUTS, chaos_level, normalized_entropy, strategies_for, slim, summarize
from topk_runtime import market_top2_top3, logit, sigmoid, normalize_probability_total

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TOP3_MODEL_PATH = HERE / "lightgbm_top3_fixed_2025.txt"


def exact_chaos_for_race(g: pd.DataFrame) -> dict:
    market = pd.to_numeric(g["市場確率"], errors="coerce").to_numpy(dtype=float)
    market = np.maximum(market, 1e-12)
    market = market / market.sum()
    ai = pd.to_numeric(g["A勝率"], errors="coerce").to_numpy(dtype=float)
    ai = np.maximum(ai, 1e-12)
    ai = ai / ai.sum()
    _, market3 = market_top2_top3(market)
    correction3 = g["_top3Correction"].to_numpy(dtype=float)
    ai3 = normalize_probability_total(sigmoid(logit(market3) + correction3), 3)

    order = np.argsort(-market, kind="stable")
    market_rank = np.empty(len(g), dtype=int)
    for rank, idx in enumerate(order, start=1):
        market_rank[idx] = rank
    p1, p2 = float(market[order[0]]), float(market[order[1]])
    uncertainty = max(0.0, min(1.0, (1.0 - p1) / 0.85))
    entropy = normalized_entropy(market)
    closeness = max(0.0, min(1.0, 1.0 - (p1 - p2) / 0.25))
    disagreement = max(0.0, min(1.0, float(np.abs(ai - market).sum() / 2.0) / 0.15))
    longshot_edge = max([max(float(ai3[i] - market3[i]), 0.0) for i in range(len(g)) if market_rank[i] >= 6] or [0.0])
    longshot_signal = max(0.0, min(1.0, longshot_edge / 0.20))
    field_factor = max(0.0, min(1.0, (len(g) - 8.0) / 10.0))
    score = 0.30*uncertainty + 0.25*entropy + 0.20*closeness + 0.15*disagreement + 0.07*longshot_signal + 0.03*field_factor
    return {"score": float(score), "level": chaos_level(float(score))}


def main():
    comp = prepare_frame()
    model = lgb.Booster(model_file=str(TOP3_MODEL_PATH))
    if list(model.feature_name()) != list(ALLF):
        raise RuntimeError("top3 model feature order mismatch")
    comp["_top3Correction"] = model.predict(comp[ALLF].astype(np.float32), raw_score=True, validate_features=True)

    race_meta = []
    for rid, g in comp.groupby("レースID", sort=False):
        c = exact_chaos_for_race(g)
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        outsider_count = int(len(g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]))
        race_meta.append({"raceID": str(rid), "date": str(pd.Timestamp(g["日付"].iloc[0]).date()), "chaosScore": c["score"], "chaosLevel": c["level"], "acAgreeTop1": bool(int(a1["馬番"]) == int(c1["馬番"])), "cDivergentOutsiderCount": outsider_count})
    meta = pd.DataFrame(race_meta)

    filters = {
        "all": set(meta["raceID"]),
        "exclude_chaos5": set(meta.loc[meta["chaosLevel"] <= 4, "raceID"]),
        "exclude_chaos4plus": set(meta.loc[meta["chaosLevel"] <= 3, "raceID"]),
        "exclude_chaos3plus": set(meta.loc[meta["chaosLevel"] <= 2, "raceID"]),
    }
    filter_results = {}
    for fname, keep in filters.items():
        sub = comp[comp["レースID"].astype(str).isin(keep)].copy()
        sums = summarize(sub)
        filter_results[fname] = {"raceCount": int(sub["レースID"].nunique()), "strategies": {name: slim(v) for name, v in sums.items()}}

    by_level = {}
    for level in range(1,6):
        ids = set(meta.loc[meta["chaosLevel"] == level, "raceID"])
        sub = comp[comp["レースID"].astype(str).isin(ids)].copy()
        sums = summarize(sub)
        by_level[str(level)] = {"raceCount": len(ids), "strategies": {name: slim(sums[name]) for name in ("A_top1_only","AC_union_top1","AC_disagree_buy_both","C_divergent_outsider")}}

    cross = {}
    for group, mask in {"chaos_1_3": meta["chaosLevel"] <= 3, "chaos_4_5": meta["chaosLevel"] >= 4, "chaos_5": meta["chaosLevel"] == 5}.items():
        ids = set(meta.loc[mask,"raceID"])
        sub = comp[comp["レースID"].astype(str).isin(ids)].copy()
        sums = summarize(sub)
        mm = meta[meta["raceID"].isin(ids)]
        cross[group] = {"raceCount": len(ids), "acDisagreeRaceCount": int((~mm["acAgreeTop1"]).sum()), "cDivergentOutsiderRaceCount": int((mm["cDivergentOutsiderCount"]>0).sum()), "acDisagreeBuyBoth": slim(sums["AC_disagree_buy_both"]), "cDivergentOutsider": slim(sums["C_divergent_outsider"])}

    result = {
        "protocol": {"period":"2026-01-01 through production_history max date","chaosDefinition":"exact build_signal_data.py formula; batched fixed top3 inference; frozen 2024 cuts","chaosCuts":CHAOS_CUTS,"odds":"historical final win odds; hindsight replay, not formal live 10-minute odds","warning":"filter comparison is exploratory; do not treat retrospective best as validated"},
        "raceCounts":{"comparisonRaces":int(comp["レースID"].nunique()),"byChaosLevel":{str(k):int(v) for k,v in meta["chaosLevel"].value_counts().sort_index().to_dict().items()}},
        "filterResults":filter_results,"byChaosLevel":by_level,"crossAnalysis":cross}
    out = OUT_DIR / "ac_chaos_filter_backtest_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_CHAOS_FILTER_BACKTEST_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_CHAOS_FILTER_BACKTEST_END")

if __name__ == "__main__": main()
