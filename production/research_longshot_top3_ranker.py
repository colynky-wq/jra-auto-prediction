#!/usr/bin/env python3
"""Follow-up hypothesis: directly rank Top3 finishers within longshots.

Proposed after inspecting the v2 retrospective report. Thus 2025/2026 are
explicitly exploratory, not untouched confirmatory tests. Only 2024 selects
tree count and ranker weight. A ranking score is NEVER called a probability.
"""
from datetime import datetime
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from research_longshot_top3_v2 import (
    FEATURE_SETS, HISTORY, JST, OUTPUT as V2_OUTPUT, PARAMS, ROOT, WEIGHTS,
    add_extras, bootstrap_mean, logit, matrix, place_history,
    prepare_history, prepare_2026, prepare_longshot, split_longshot,
    top_picks, training_targets,
)
from train_no_current_odds_challenger import ensure

VERSION = "longshot-top3-direct-ranker-v1-20261123"
OUTPUT = ROOT/"research/model_candidates/longshot_top3_ranker_v1"
RANK_FEATURES = FEATURE_SETS["place_and_market"]
RANK_PARAMS = {**PARAMS, "objective": "lambdarank", "metric": "ndcg",
               "ndcg_eval_at": [1], "lambdarank_truncation_level": 4,
               "label_gain": [0, 1]}


def grouped(data):
    d = data.sort_values(["レースID", "馬番"]).reset_index(drop=True)
    groups = d.groupby("レースID", sort=False).size().to_numpy()
    ensure(groups.sum() == len(d), "ranker race groups invalid")
    return d, groups


def fit_ranker(data, validation=None, rounds=None):
    d, groups = grouped(data)
    y, baseline = training_targets(d)
    ds = lgb.Dataset(matrix(d, RANK_FEATURES), label=y, group=groups,
                     init_score=logit(baseline), feature_name=list(RANK_FEATURES))
    if validation is not None:
        ensure(pd.to_datetime(validation["日付"]).dt.year.eq(2024).all(),
               "ranker early stopping must use 2024 only")
        v, groupv = grouped(validation)
        yv, bv = training_targets(v)
        vs = lgb.Dataset(matrix(v, RANK_FEATURES), label=yv, group=groupv,
                         init_score=logit(bv), reference=ds,
                         feature_name=list(RANK_FEATURES))
        model = lgb.train(RANK_PARAMS, ds, num_boost_round=300,
                          valid_sets=[vs], callbacks=[
                              lgb.early_stopping(45, verbose=False),
                              lgb.log_evaluation(0)])
        rounds = int(model.best_iteration or 300)
    else:
        ensure(type(rounds) is int and 1 <= rounds <= 300,
               "invalid frozen ranker rounds")
        model = lgb.train(RANK_PARAMS, ds, num_boost_round=rounds)
    return model, rounds


def rank_scores(data, model, weight):
    """Only frozen features + market baseline; no finish/result access."""
    ensure(weight in WEIGHTS and model.feature_name() == list(RANK_FEATURES),
           "ranker model identity/weight mismatch")
    base = np.asarray(data["市場3着内基準"], dtype=float)
    ensure(np.isfinite(base).all() and ((base > 0)&(base < 1)).all(),
           "invalid ranker market baseline")
    raw = np.asarray(model.predict(matrix(data, RANK_FEATURES),
                     raw_score=True, validate_features=True, num_threads=2), dtype=float)
    score = logit(base) + weight*raw
    ensure(np.isfinite(score).all(), "invalid ranking score")
    return score


def select_rank_weight(validation, model):
    ensure(pd.to_datetime(validation["日付"]).dt.year.eq(2024).all(),
           "ranker strength must use 2024 only")
    grid = []
    for weight in WEIGHTS:
        picked = top_picks(validation, rank_scores(validation, model, weight))
        grid.append({"weight": weight, "top3Hits2024": int((picked["着順"] <= 3).sum()),
                     "races2024": len(picked)})
    return min(grid, key=lambda r:(-r["top3Hits2024"], r["weight"])), grid


def paired_hits(picks, other_finish):
    hit = (picks["着順"].to_numpy() <= 3).astype(float)
    other = (np.asarray(other_finish) <= 3).astype(float)
    diff = hit-other
    d = pd.DataFrame({"delta": diff, "date": pd.to_datetime(picks["日付"])})
    daily = d.groupby("date")["delta"].agg(["sum", "count"])
    rng = np.random.default_rng(20261123)
    date_ci = None
    if len(daily) >= 20:
        sums, counts = daily["sum"].to_numpy(), daily["count"].to_numpy()
        boot = []
        for _ in range(1500):
            i = rng.integers(0, len(daily), len(daily))
            boot.append(sums[i].sum()/counts[i].sum())
        date_ci = [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]
    return {"hitRateDelta": float(diff.mean()),
            "rankerOnlyHit": int(((hit == 1)&(other == 0)).sum()),
            "otherOnlyHit": int(((hit == 0)&(other == 1)).sum()),
            "raceBootstrap95": bootstrap_mean(diff),
            "wholeRaceDateBootstrap95": date_ci}


def evaluate_ranker(data, score, prior_picks):
    picks = top_picks(data, score).rename(columns={"probability": "rankingScore"})
    previous = prior_picks.copy()
    ensure(picks["race"].tolist() == previous["race"].astype(str).tolist(),
           "ranker and v2 comparison race identities differ")
    def metric(finish):
        a = np.asarray(finish, dtype=float) <= 3
        return {"top3Hits": int(a.sum()), "top3HitRate": float(a.mean())}
    comparison = {
        "market": metric(previous["marketFinish"]),
        "control36": metric(previous["control36Finish"]),
        "probabilityV2": metric(previous["着順"]),
        "directRanker": metric(picks["着順"]),
    }
    return {"races": len(picks), "horseRows": len(data),
            "comparison": comparison,
            "versusMarket": paired_hits(picks, previous["marketFinish"]),
            "versusControl36": paired_hits(picks, previous["control36Finish"]),
            "versusProbabilityV2": paired_hits(picks, previous["着順"]),
            "rankingScoresAreProbabilities": False}, picks


def markdown(r):
    lines = ["# 穴馬の好走を直接並べ替えるモデルの追加検証", "",
             "6番人気以下・単勝10倍以上の中から、3着以内の馬を1頭選ぶ研究です。",
             "好走実績を加えた確率モデルの結果を見た後に提案した追加仮説です。",
             "2025・2026年の結果は探索的な比較であり、未知データの独立検証ではありません。",
             f"2024年だけで学習回数 {r['selection2024']['rounds']}、補正幅 {r['selection2024']['selected']['weight']} を選択。", "",
             "| 年 | レース数 | 人気順 | 従来36項目の対照 | 好走実績を加えた確率モデル | 今回の順位モデル |",
             "|---|---:|---:|---:|---:|---:|"]
    for year in ("2025", "2026"):
        s = r[year]
        cells = [f"{s['comparison'][name]['top3Hits']}（{s['comparison'][name]['top3HitRate']*100:.2f}%）"
                 for name in ("market", "control36", "probabilityV2", "directRanker")]
        lines.append(f"| {year} | {s['races']} | "+" | ".join(cells)+" |")
    lines += ["", "| 年 | 順位モデル−従来の3着内率の差 | 日付単位95%区間 |",
              "|---|---:|---|"]
    for year in ("2025", "2026"):
        p = r[year]["versusControl36"]
        lines.append(f"| {year} | {100*p['hitRateDelta']:+.2f}ポイント | {p['wholeRaceDateBootstrap95']} |")
    lines += ["", "順位用の点数は確率ではありません。勝率・3着内確率として表示しません。",
              "正式な予測、購入推奨、資金配分へは接続していません。", "",
              "最終オッズを利用した過去検証です。将来の発走前保存データによる確認が必要です。", ""]
    return "\n".join(lines)


def main():
    source_sha = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    v2 = json.loads((V2_OUTPUT/"research.json").read_text(encoding="utf-8"))
    ensure(v2["sourceHistorySha256"] == source_sha,
           "ranker must use identical v2 history cohort")
    hist = pd.read_parquet(HISTORY)
    print("RANKER_CAUSAL_FEATURES", len(hist), flush=True)
    priors = place_history(hist)
    hist["馬体重"] = pd.to_numeric(hist["馬体重"], errors="coerce").fillna(480.)
    older = add_extras(prepare_longshot(prepare_history(hist)), priors)
    train, validation, test25 = split_longshot(older)
    model, rounds = fit_ranker(train, validation)
    choice, grid = select_rank_weight(validation, model)
    selection = {"rounds": rounds, "selected": choice, "grid": grid,
                 "selectionTimestampJST": datetime.now(JST).isoformat(),
                 "criterion": "maximum Top3 hits of one longshot per race in 2024 only; tie smaller weight"}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/"selection2024.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2)+"\n")
    print("RANKER_2024_LOCKED", selection, flush=True)
    model.save_model(str(OUTPUT/"model_through2023.txt"))
    p25 = pd.read_csv(V2_OUTPUT/"picks_2025.csv", dtype={"race": str, "レースID": str})
    result25, picks25 = evaluate_ranker(test25, rank_scores(test25, model, choice["weight"]), p25)
    final, _ = fit_ranker(pd.concat([train, validation, test25], ignore_index=True), rounds=rounds)
    final.save_model(str(OUTPUT/"model_through2025.txt"))
    print("RANKER_2026_FEATURES", flush=True)
    recent = add_extras(prepare_longshot(prepare_2026(hist)), priors)
    p26 = pd.read_csv(V2_OUTPUT/"picks_2026.csv", dtype={"race": str, "レースID": str})
    result26, picks26 = evaluate_ranker(recent, rank_scores(recent, final, choice["weight"]), p26)
    r = {"schemaVersion": 1, "version": VERSION, "generatedAtJST": datetime.now(JST).isoformat(),
         "status": "exploratory_followup_hypothesis_not_independent_holdout",
         "researchOnly": True, "candidatePromotionApproved": False,
         "formalPredictionAIChanged": False, "formalRecommendationsEnabled": False,
         "autoBetEnabled": False, "sourceHistorySha256": source_sha,
         "hypothesisProposedAfter": "inspection of v2 retrospective results",
         "rankingScoresAreProbabilities": False, "features": list(RANK_FEATURES),
         "parameters": RANK_PARAMS, "selection2024": selection,
         "trainingThroughFor2025": "2023-12-31", "refitThroughFor2026": "2025-12-31",
         "2025": result25, "2026": result26,
         "modelHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in OUTPUT.glob("*.txt")},
         "limitations": v2["limitations"],
         "priorV2ReportSha256": hashlib.sha256((V2_OUTPUT/"research.json").read_bytes()).hexdigest()}
    (OUTPUT/"research.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    (OUTPUT/"README.md").write_text(markdown(r), encoding="utf-8")
    picks25.to_csv(OUTPUT/"picks_2025.csv", index=False)
    picks26.to_csv(OUTPUT/"picks_2026.csv", index=False)
    print("LONGSHOT_RANKER_RESEARCH_OK", json.dumps({"2024": selection,
          "2025": result25, "2026": result26}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
