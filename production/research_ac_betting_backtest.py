from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import ALLF, add_features, add_rating
from research_ac_coverage import FEATURES_C, train_c, predict_a, predict_c

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_PATH = HERE / "production_history.parquet"
A_MODEL_PATH = HERE / "lightgbm_fixed_2025.txt"
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

STAKE_PER_BET = 100


def prepare_frame() -> pd.DataFrame:
    hist = pd.read_parquet(HISTORY_PATH)
    hist["日付"] = pd.to_datetime(hist["日付"], errors="coerce")
    hist = hist.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    feat = add_features(add_rating(hist.copy()))

    train = feat[(feat["日付"] <= pd.Timestamp("2025-12-31")) & feat["着順"].notna() & (~feat["障害"].fillna(False).astype(bool))].copy()
    train_num = train[FEATURES_C].apply(pd.to_numeric, errors="coerce")
    train = train[np.isfinite(train_num.to_numpy(dtype=float)).all(axis=1)].copy().reset_index(drop=True)
    c_model = train_c(train)
    a_model = lgb.Booster(model_file=str(A_MODEL_PATH))

    target_all = feat[(feat["日付"] >= pd.Timestamp("2026-01-01")) & feat["着順"].notna() & (~feat["障害"].fillna(False).astype(bool))].copy()
    nums = target_all[ALLF].apply(pd.to_numeric, errors="coerce")
    scoreable = target_all[np.isfinite(nums.to_numpy(dtype=float)).all(axis=1)].copy().reset_index(drop=True)
    raw_counts = target_all.groupby("レースID").size()
    score_counts = scoreable.groupby("レースID").size()
    complete_ids = [rid for rid, n in score_counts.items() if int(n) == int(raw_counts.get(rid, -1))]
    comp = scoreable[scoreable["レースID"].isin(complete_ids)].copy().reset_index(drop=True)
    comp["A勝率"] = predict_a(a_model, comp)
    comp["C勝率"] = predict_c(c_model, comp)
    comp["A順位"] = comp.groupby("レースID")["A勝率"].rank(ascending=False, method="min").astype(int)
    comp["C順位"] = comp.groupby("レースID")["C勝率"].rank(ascending=False, method="min").astype(int)
    return comp


def settle(strategy_name: str, rows: list[dict]) -> dict:
    bets = len(rows)
    stake = bets * STAKE_PER_BET
    ret = 0.0
    hits = 0
    races = set()
    hit_rows = []
    for r in rows:
        races.add(r["raceID"])
        if r["finish"] == 1:
            hits += 1
            payout = STAKE_PER_BET * r["odds"]
            ret += payout
            hit_rows.append({**r, "payoutYen": payout})
    return {
        "strategy": strategy_name,
        "racesBet": len(races),
        "bets": bets,
        "hits": hits,
        "hitRatePctPerBet": (hits / bets * 100.0) if bets else None,
        "stakeYen": int(stake),
        "returnYen": int(round(ret)),
        "profitYen": int(round(ret - stake)),
        "returnIndex": (ret / stake) if stake else None,
        "roiPct": ((ret - stake) / stake * 100.0) if stake else None,
        "topHits": sorted(hit_rows, key=lambda x: x["odds"], reverse=True)[:15],
    }


def row_record(r: pd.Series) -> dict:
    return {
        "raceID": str(r["レースID"]),
        "date": str(pd.Timestamp(r["日付"]).date()),
        "horseNo": int(r["馬番"]),
        "horseName": str(r["馬名"]),
        "finish": int(r["着順"]),
        "odds": float(r["単勝オッズ"]),
        "A_rank": int(r["A順位"]),
        "C_rank": int(r["C順位"]),
        "A_prob": float(r["A勝率"]),
        "C_prob": float(r["C勝率"]),
    }


def main():
    comp = prepare_frame()
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

    agree_races = 0
    disagree_races = 0
    total_races = 0

    for rid, g0 in comp.groupby("レースID", sort=False):
        g = g0.copy()
        if len(g[g["着順"].astype(float) == 1.0]) != 1:
            continue
        total_races += 1
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        ar = row_record(a1)
        cr = row_record(c1)
        strategies["A_top1_only"].append(ar)
        strategies["C_top1_only"].append(cr)

        if int(a1["馬番"]) == int(c1["馬番"]):
            agree_races += 1
            strategies["AC_union_top1"].append(ar)
            strategies["AC_agree_top1_only"].append(ar)
        else:
            disagree_races += 1
            strategies["AC_union_top1"].extend([ar, cr])
            strategies["AC_disagree_buy_both"].extend([ar, cr])

        if int(a1["C順位"]) <= 3:
            strategies["A_top1_confirmed_by_Ctop3"].append(ar)
        if int(c1["A順位"]) <= 3:
            strategies["C_top1_confirmed_by_Atop3"].append(cr)

        # Pre-specified exploratory divergence rule:
        # C top3, A rank 6 or worse, final win odds >= 10.0. Buy each qualifying horse for 100 yen.
        q = g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]
        for _, r in q.iterrows():
            strategies["C_divergent_outsider"].append(row_record(r))

    summaries = [settle(name, rows) for name, rows in strategies.items()]
    result = {
        "protocol": {
            "period": "2026-01-01 through production_history max date",
            "betType": "win",
            "stakePerSelectionYen": STAKE_PER_BET,
            "odds": "final historical win odds; hindsight replay, not live 10-minute odds",
            "A": "fixed2025 market-correction model",
            "C": "36 non-market features, trained through 2025 only",
            "primaryRules": {
                "AC_union_top1": "buy A top1 and C top1; if same horse, one 100-yen bet; if different, 100 yen each",
                "AC_agree_top1_only": "bet 100 yen only when A and C have the same top1",
                "AC_disagree_buy_both": "only when A/C top1 differ, buy both for 100 yen each",
                "C_divergent_outsider": "buy every horse with C rank<=3, A rank>=6, and final odds>=10.0 for 100 yen",
            },
            "warning": "The divergence rule is exploratory and evaluated on the same 2026 sample on which it was proposed; it must not be treated as prospective proof.",
        },
        "cohort": {
            "races": total_races,
            "agreeTop1Races": agree_races,
            "disagreeTop1Races": disagree_races,
        },
        "strategies": summaries,
    }
    out = OUT_DIR / "ac_betting_backtest_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_BETTING_BACKTEST_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_BETTING_BACKTEST_END")
    print("artifact", out)


if __name__ == "__main__":
    main()
