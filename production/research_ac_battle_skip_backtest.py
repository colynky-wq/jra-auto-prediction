from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from research_ac_betting_backtest import prepare_frame, settle, row_record

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_DIR = REPO / "market_independence_artifacts"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BATTLE_SKIP_CUT = 0.29750323298090603


def sigmoid(x):
    x = np.asarray(x, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))


def battle_score_for_race(g: pd.DataFrame) -> tuple[float, int, str]:
    # Reconstruct build_signal_data.py Battle score exactly from market probability and A probability.
    # 市場ロジット is the pre-race market logit feature; invert and normalize race total to 1.
    market = sigmoid(pd.to_numeric(g["市場ロジット"], errors="coerce").to_numpy(dtype=float))
    market = market / market.sum()
    ai = pd.to_numeric(g["A勝率"], errors="coerce").to_numpy(dtype=float)
    ai = ai / ai.sum()

    scores = []
    for i in range(len(g)):
        ratio = ai[i] / max(market[i], 1e-9)
        edge = ai[i] - market[i]
        ratio_component = max(0.0, min(1.0, (ratio - 1.0) / 0.40))
        edge_component = max(0.0, min(1.0, edge / 0.06))
        support_component = max(0.0, min(1.0, ai[i] / 0.10))
        score = 0.55 * ratio_component + 0.30 * edge_component + 0.15 * support_component
        positive = ratio > 1.0 and edge > 0.0 and ai[i] >= 0.01
        scores.append((score, positive, ai[i], i))

    positive = [x for x in scores if x[1]]
    pool = positive if positive else scores
    best = max(pool, key=lambda x: (x[0], x[2]))
    score, _, _, pos = best
    horse_no = int(g.iloc[pos]["馬番"])
    horse_name = str(g.iloc[pos]["馬名"])
    return float(score), horse_no, horse_name


def strategies_for(comp: pd.DataFrame) -> tuple[dict[str, list[dict]], dict]:
    strategies = {
        "A_top1_only": [],
        "C_top1_only": [],
        "AC_union_top1": [],
        "AC_agree_top1_only": [],
        "AC_disagree_buy_both": [],
        "C_divergent_outsider": [],
        "A_top1_confirmed_by_Ctop3": [],
        "C_top1_confirmed_by_Atop3": [],
    }
    total = agree = disagree = 0
    for _, g0 in comp.groupby("レースID", sort=False):
        g = g0.copy()
        if len(g[g["着順"].astype(float) == 1.0]) != 1:
            continue
        total += 1
        a1 = g.sort_values(["A順位", "馬番"]).iloc[0]
        c1 = g.sort_values(["C順位", "馬番"]).iloc[0]
        ar, cr = row_record(a1), row_record(c1)
        strategies["A_top1_only"].append(ar)
        strategies["C_top1_only"].append(cr)
        if int(a1["馬番"]) == int(c1["馬番"]):
            agree += 1
            strategies["AC_union_top1"].append(ar)
            strategies["AC_agree_top1_only"].append(ar)
        else:
            disagree += 1
            strategies["AC_union_top1"].extend([ar, cr])
            strategies["AC_disagree_buy_both"].extend([ar, cr])
        if int(a1["C順位"]) <= 3:
            strategies["A_top1_confirmed_by_Ctop3"].append(ar)
        if int(c1["A順位"]) <= 3:
            strategies["C_top1_confirmed_by_Atop3"].append(cr)
        q = g[(g["C順位"] <= 3) & (g["A順位"] >= 6) & (pd.to_numeric(g["単勝オッズ"], errors="coerce") >= 10.0)]
        for _, r in q.iterrows():
            strategies["C_divergent_outsider"].append(row_record(r))
    return strategies, {"races": total, "agreeTop1Races": agree, "disagreeTop1Races": disagree}


def summaries(strats):
    return {name: settle(name, rows) for name, rows in strats.items()}


def main():
    comp = prepare_frame()
    skip_ids = set()
    race_signal = []
    for rid, g in comp.groupby("レースID", sort=False):
        score, no, name = battle_score_for_race(g)
        is_skip = score <= BATTLE_SKIP_CUT
        if is_skip:
            skip_ids.add(str(rid))
        winner = g[g["着順"].astype(float) == 1.0]
        race_signal.append({
            "raceID": str(rid),
            "date": str(pd.Timestamp(g["日付"].iloc[0]).date()),
            "battleScore": score,
            "battleGrade": "見送り" if is_skip else "C以上",
            "candidateNo": no,
            "candidateName": name,
            "winnerNo": int(winner.iloc[0]["馬番"]) if len(winner) == 1 else None,
        })

    before_strats, before_cohort = strategies_for(comp)
    filtered = comp[~comp["レースID"].astype(str).isin(skip_ids)].copy()
    after_strats, after_cohort = strategies_for(filtered)
    before, after = summaries(before_strats), summaries(after_strats)

    comparisons = []
    for name in before:
        b, a = before[name], after[name]
        comparisons.append({
            "strategy": name,
            "before": {k: b[k] for k in ("racesBet", "bets", "hits", "stakeYen", "returnYen", "profitYen", "returnIndex", "roiPct")},
            "afterExcludeBattleSkip": {k: a[k] for k in ("racesBet", "bets", "hits", "stakeYen", "returnYen", "profitYen", "returnIndex", "roiPct")},
            "deltaProfitYen": int(a["profitYen"] - b["profitYen"]),
            "deltaReturnRatePctPoints": float(((a["returnIndex"] or 0) - (b["returnIndex"] or 0)) * 100.0),
        })

    result = {
        "protocol": {
            "period": "2026-01-01 through production_history max date",
            "avoidDefinition": "exact build_signal_data Battle grade 見送り: battle_score <= first frozen BATTLE_CUT",
            "battleSkipCut": BATTLE_SKIP_CUT,
            "marketInput": "historical final market probability reconstructed from 市場ロジット; hindsight replay",
            "betType": "win; 100 yen per selection",
            "warning": "Not formal live 10-minute P/L. Uses completed-race final odds and reconstructed Battle grade.",
        },
        "raceCounts": {
            "allComparisonRaces": int(comp["レースID"].nunique()),
            "battleSkipRaces": len(skip_ids),
            "battleSkipPct": len(skip_ids) / comp["レースID"].nunique() * 100.0,
            "keptRaces": int(comp["レースID"].nunique()) - len(skip_ids),
        },
        "cohortBefore": before_cohort,
        "cohortAfter": after_cohort,
        "strategyComparison": comparisons,
        "skipExamples": [r for r in race_signal if r["battleGrade"] == "見送り"][:50],
    }
    out = OUT_DIR / "ac_betting_exclude_battle_skip_2026.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AC_BATTLE_SKIP_BACKTEST_BEGIN")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("AC_BATTLE_SKIP_BACKTEST_END")
    print("artifact", out)


if __name__ == "__main__":
    main()
