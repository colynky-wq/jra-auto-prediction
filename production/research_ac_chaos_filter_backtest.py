from __future__ import annotations

import json
import math
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF
from research_ac_betting_backtest import prepare_frame, settle, row_record
from topk_runtime import market_top2_top3, logit, sigmoid, normalize_probability_total

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TOP3_MODEL_PATH = HERE / "lightgbm_top3_fixed_2025.txt"

# Frozen from 2024 reference sample in build_signal_data.py.
CHAOS_CUTS = [
    0.44909320762324856,
    0.5675705643046633,
    0.6491365420737711,
    0.7019851917215711,
]


def normalized_entropy(p: np.ndarray) -> float:
    q = np.asarray(p, dtype=float)
    q = np.maximum(q, 1e-12)
    q = q / q.sum()
    n = len(q)
    if n <= 1:
        return 0.0
    return float(-np.sum(q * np.log(q)) / math.log(n))


def chaos_level(score: float) -> int:
    if score <= CHAOS_CUTS[0]: return 1
    if score <= CHAOS_CUTS[1]: return 2
    if score <= CHAOS_CUTS[2]: return 3
    if score <= CHAOS_CUTS[3]: return 4
    return 5


def exact_chaos_for_race(g: pd.DataFrame, top3_model: lgb.Booster) -> dict:
    # Market win is the historical race-level normalized market probability in the same feature frame.
    market = pd.to_numeric(g["市場確率"], errors="coerce").to_numpy(dtype=float)
    market = np.maximum(market, 1e-12)
    market = market / market.sum()

    ai = pd.to_numeric(g["A勝率"], errors="coerce").to_numpy(dtype=float)
    ai = np.maximum(ai, 1e-12)
    ai = ai / ai.sum()

    X = g[ALLF].astype(np.float32)
    market2, market3 = market_top2_top3(market)
    correction3 = top3_model.predict(X, raw_score=True, validate_features=True)
    pre3 = sigmoid(logit(market3) + correction3)
    ai3 = normalize_probability_total(pre3, 3)

    order = np.argsort(-market, kind="stable")
    market_rank = np.empty(len(g), dtype=int)
    for rank, idx in enumerate(order, start=1):
        market_rank[idx] = rank
    p1, p2 = float(market[order[0]]), float(market[order[1]])

    uncertainty = max(0.0, min(1.0, (1.0 - p1) / 0.85))
    entropy = normalized_entropy(market)
    closeness = max(0.0, min(1.0, 1.0 - (p1 - p2) / 0.25))
    disagreement_l1 = float(np.abs(ai - market).sum() / 2.0)
    disagreement = max(0.0, min(1.0, disagreement_l1 / 0.15))

    longshot_edge = 0.0
    for i in range(len(g)):
        if market_rank[i] >= 6:
            longshot_edge = max(longshot_edge, max(float(ai3[i] - market3[i]), 0.0))
    longshot_signal = max(0.0, min(1.0, longshot_edge / 0.20))
    field_factor = max(0.0, min(1.0, (len(g) - 8.0) / 10.0))

    score = (
        0.30 * uncertainty
        + 0.25 * entropy
        + 0.20 * closeness
        + 0.15 * disagreement
        + 0.07 * longshot_signal
        + 0.03 * field_factor
    )
    return {
        "score": float(score),
        "level": chaos_level(float(score)),
        "uncertainty": uncertainty,
        "entropy": entropy,
        "closeness": closeness,
        "disagreement": disagreement,
        "longshotSignal": longshot_signal,
        "fieldFactor": field_factor,
    }


def strategies_for(comp: pd.DataFrame) -> dict[str, list[dict]]:
    strategies: dict[str, list[dict]] = {
        "A_top1_only": [],
        "C_top1_only": [],
        "AC_union_top1": [],
        "AC_agree_top1_only": [],
        "AC_disagree_buy_both": [],
        "C_divergent_outsider": [],
        "A_top1_confirmed_by_Ctop3": [],
        "C_top1_confirmed_by_Atop3": [],
    }
    for _, g0 in comp.groupby("レースID", sort=False):
        g = g0.copy()
        if len(g[g["着順"].astype(float) == 1.0]) != 1:
            continue
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        ar, cr = row_record(a1), row_record(c1)
        strategies["A_top1_only"].append(ar)
        strategies["C_top1_only"].append(cr)
        if int(a1["馬番"]) == int(c1["馬番"]):
            strategies["AC_union_top1"].append(ar)
            strategies["AC_agree_top1_only"].append(ar)
        else:
            strategies["AC_union_top1"].extend([ar, cr])
            strategies["AC_disagree_buy_both"].extend([ar, cr])
        if int(a1["C順位"]) <= 3:
            strategies["A_top1_confirmed_by_Ctop3"].append(ar)
        if int(c1["A順位"]) <= 3:
            strategies["C_top1_confirmed_by_Atop3"].append(cr)
        q = g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]
        for _, r in q.iterrows():
            strategies["C_divergent_outsider"].append(row_record(r))
    return strategies


def summarize(comp: pd.DataFrame) -> dict[str, dict]:
    s = strategies_for(comp)
    return {name: settle(name, rows) for name, rows in s.items()}


def slim(x: dict) -> dict:
    return {k: x[k] for k in ("racesBet", "bets", "hits", "stakeYen", "returnYen", "profitYen", "returnIndex", "roiPct")}


def main():
    comp = prepare_frame()
    model = lgb.Booster(model_file=str(TOP3_MODEL_PATH))
    if list(model.feature_name()) != list(ALLF):
        raise RuntimeError("top3 model feature order mismatch")

    race_meta = []
    for rid, g in comp.groupby("レースID", sort=False):
        c = exact_chaos_for_race(g, model)
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        outsider_count = int(len(g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]))
        winner = g[g["着順"].astype(float) == 1.0]
        race_meta.append({
            "raceID": str(rid),
            "date": str(pd.Timestamp(g["日付"].iloc[0]).date()),
            "chaosScore": c["score"],
            "chaosLevel": c["level"],
            "acAgreeTop1": bool(int(a1["馬番"]) == int(c1["馬番"])),
            "cDivergentOutsiderCount": outsider_count,
            "winnerNo": int(winner.iloc[0]["馬番"]) if len(winner) == 1 else None,
        })

    meta = pd.DataFrame(race_meta)
    base = summarize(comp)
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
        filter_results[fname] = {
            "raceCount": int(sub["レースID"].nunique()),
            "strategies": {name: slim(v) for name, v in sums.items()},
        }

    by_level = {}
    for level in range(1, 6):
        ids = set(meta.loc[meta["chaosLevel"] == level, "raceID"])
        sub = comp[comp["レースID"].astype(str).isin(ids)].copy()
        sums = summarize(sub)
        by_level[str(level)] = {
            "raceCount": len(ids),
            "strategies": {name: slim(sums[name]) for name in ("A_top1_only", "AC_union_top1", "AC_disagree_buy_both", "C_divergent_outsider")},
        }

    cross = {}
    for level_group, mask in {
        "chaos_1_3": meta["chaosLevel"] <= 3,
        "chaos_4_5": meta["chaosLevel"] >= 4,
        "chaos_5": meta["chaosLevel"] == 5,
    }.items():
        ids = set(meta.loc[mask, "raceID"])
        sub = comp[comp["レースID"].astype(str).isin(ids)].copy()
        sums = summarize(sub)
        mm = meta[meta["raceID"].isin(ids)]
        cross[level_group] = {
            "raceCount": len(ids),
            "acDisagreeRaceCount": int((~mm["acAgreeTop1"]).sum()),
            "cDivergentOutsiderRaceCount": int((mm["cDivergentOutsiderCount"] > 0).sum()),
            "acDisagreeBuyBoth": slim(sums["AC_disagree_buy_both"]),
            "cDivergentOutsider": slim(sums["C_divergent_outsider"]),
        }

    level_counts = meta["chaosLevel"].value_counts().sort_index().to_dict()
    result = {
        "protocol": {
            "period": "2026-01-01 through production_history max date",
            "chaosDefinition": "exact build_signal_data.py formula reconstructed with fixed win A model, fixed top3 model, Plackett-Luce market Top3, and frozen 2024 CHAOS_CUTS",
            "chaosCuts": CHAOS_CUTS,
            "filters": {
                "exclude_chaos5": "keep Chaos levels 1-4",
                "exclude_chaos4plus": "keep Chaos levels 1-3",
                "exclude_chaos3plus": "sensitivity only; keep Chaos levels 1-2",
            },
            "betType": "win; 100 yen per selection",
            "odds": "historical final win odds; hindsight replay, not formal live 10-minute odds",
            "warning": "Do not treat best retrospective filter as validated. Thresholds are frozen, but selecting among filters after seeing 2026 results is exploratory.",
        },
        "raceCounts": {
            "comparisonRaces": int(comp["レースID"].nunique()),
            "byChaosLevel": {str(k): int(v) for k, v in level_counts.items()},
        },
        "filterResults": filter_results,
        "byChaosLevel": by_level,
        "crossAnalysis": cross,
    }
    out = OUT_DIR / "ac_chaos_filter_backtest_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_CHAOS_FILTER_BACKTEST_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_CHAOS_FILTER_BACKTEST_END")
    print("artifact", out)


if __name__ == "__main__":
    main()
