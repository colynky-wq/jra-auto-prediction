#!/usr/bin/env python3
"""Isolated Top3 longshot research, with causal horse-place history.

Four feature sets, tree counts, residual weights and abstention thresholds
are selected on 2024 only. Fit <=2023 -> describe 2025; then refit <=2025
-> describe 2026. These years were studied before in the wider project and
are NOT pristine holdouts. Historical final odds are NOT live T10 inputs.
No writes to production models, live prediction evidence or ticket rules.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from train_longshot_top3 import (
    FEATURES, HISTORY, JST, PARAMS, ROOT, WEIGHTS,
    logit, sigmoid, prepare_longshot, split_longshot, training_targets,
)
from train_no_current_odds_challenger import ensure, prepare_history
from evaluate_no_current_odds_2026_history import prepare_2026
from train_top2_top3_models import BASE_COLS, binary_logloss

VERSION = "longshot-top3-place-history-v2-20261123"
OUTPUT = ROOT / "research/model_candidates/longshot_top3_v2"
SEED = 20261123
PLACE_FEATURES = (
    "placeLast3V2", "placeLast5V2", "placeSurfaceV2", "placeDistanceV2",
    "finishLast3V2", "bestFinishLast5V2", "placeHistoryLogV2",
    "placeClassV2",
)
MARKET_FEATURES = (
    "marketTop3LogitV2", "logWinOddsV2", "popularityFractionV2",
)
FEATURE_SETS = {
    "control36": FEATURES,
    "place_history": FEATURES + PLACE_FEATURES,
    "market_context": FEATURES + MARKET_FEATURES,
    "place_and_market": FEATURES + PLACE_FEATURES + MARKET_FEATURES,
}
POLICY_COVERAGES = (1.0, .5, .25)


def place_history(history: pd.DataFrame) -> pd.DataFrame:
    """Emit features first, then update ALL horses after that calendar day.

    Current results, future rows, and every same-day result are unavailable
    to a prediction. Missing/invalid finishes and jumps never count as losses.
    No fitted prior or whole-table imputation uses future outcomes.
    """
    required = ("レースID", "馬番", "馬キー", "日付", "芝ダ", "距離",
                "クラス", "着順", "頭数", "障害")
    ensure(all(c in history for c in required), "place history schema missing")
    d = history.loc[:, list(required)].copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce").dt.normalize()
    ensure(d["日付"].notna().all() and d["馬キー"].notna().all(),
           "invalid horse/date in causal place history")
    ensure(not d.duplicated(["レースID", "馬番"]).any(),
           "duplicate race/horse identity")
    d = d.sort_values(["日付", "レースID", "馬番"]).reset_index(drop=True)
    out = np.empty((len(d), len(PLACE_FEATURES)), dtype=np.float64)
    states = {}
    for _, day in d.groupby("日付", sort=False):
        pending = []
        for row in day.itertuples(index=True, name=None):
            i, _, _, key, _, surface, distance, cls, rank, field, jump = row
            state = states.setdefault(key, (deque(maxlen=5), {}, {}, {}, [0]))
            recent, surfaces, distances, classes, count = state
            surface = str(surface)
            band = (surface, int(distance)//200) if pd.notna(distance) else None
            cls = str(cls)
            last = list(recent)
            last3 = last[-3:]
            def rate(items):
                return (sum(x[0] for x in items) + .42)/(len(items)+2)
            def grouped_rate(mapping, group):
                hits, starts = mapping.get(group, (0, 0))
                return (hits + .42)/(starts+2)
            out[i] = (
                rate(last3), rate(last), grouped_rate(surfaces, surface),
                grouped_rate(distances, band),
                (sum(x[1] for x in last3)+1.0)/(len(last3)+2),
                min((x[1] for x in last), default=.5),
                np.log1p(min(count[0], 30)), grouped_rate(classes, cls),
            )
            if (not bool(jump) and pd.notna(rank) and pd.notna(field)
                    and np.isfinite(float(rank)) and np.isfinite(float(field))
                    and float(field) >= 3 and 1 <= float(rank) <= float(field)
                    and float(rank).is_integer()):
                pending.append((state, surface, band, cls, int(rank <= 3),
                                (float(rank)-1)/(float(field)-1)))
        for state, surface, band, cls, hit, frac in pending:
            recent, surfaces, distances, classes, count = state
            recent.append((hit, frac))
            count[0] += 1
            for mapping, group in ((surfaces, surface), (distances, band),
                                   (classes, cls)):
                n_hit, n = mapping.get(group, (0, 0))
                mapping[group] = (n_hit+hit, n+1)
    result = d[["レースID", "馬番"]].copy()
    result[list(PLACE_FEATURES)] = out
    ensure(np.isfinite(out).all(), "nonfinite causal place feature")
    return result


def add_extras(data: pd.DataFrame, prior: pd.DataFrame) -> pd.DataFrame:
    d = data.merge(prior, on=["レースID", "馬番"], how="left",
                   sort=False, validate="one_to_one")
    ensure(len(d) == len(data), "place feature join changed cohort")
    d["marketTop3LogitV2"] = logit(d["市場3着内基準"])
    d["logWinOddsV2"] = np.log(d["単勝オッズ"].astype(float))
    d["popularityFractionV2"] = d["人気"].astype(float)/d["頭数"].astype(float)
    ensure(np.isfinite(d[list(PLACE_FEATURES+MARKET_FEATURES)]).all().all(),
           "missing causal place/market features")
    return d


def matrix(data, features):
    ensure(tuple(features) in tuple(FEATURE_SETS.values()),
           "unregistered feature set")
    ensure(all(f in data for f in features), "missing v2 features")
    x = data.loc[:, list(features)].astype(np.float32)
    ensure(np.isfinite(x).all().all(), "invalid v2 pre-race inputs")
    return x


def train(data, features, valid=None, rounds=None):
    y, baseline = training_targets(data)
    ds = lgb.Dataset(matrix(data, features), label=y, init_score=logit(baseline),
                     feature_name=list(features), free_raw_data=False)
    if valid is not None:
        ensure(pd.to_datetime(valid["日付"]).dt.year.eq(2024).all(),
               "v2 early stopping must use 2024 only")
        yv, bv = training_targets(valid)
        vs = lgb.Dataset(matrix(valid, features), label=yv, init_score=logit(bv),
                         reference=ds, feature_name=list(features))
        model = lgb.train(PARAMS, ds, num_boost_round=300, valid_sets=[vs],
                          callbacks=[lgb.early_stopping(45, verbose=False),
                                     lgb.log_evaluation(0)])
        rounds = int(model.best_iteration or 300)
    else:
        ensure(type(rounds) is int and 1 <= rounds <= 300,
               "invalid preregistered refit rounds")
        model = lgb.train(PARAMS, ds, num_boost_round=rounds)
    return model, rounds


def predict(data, model, features, weight):
    """Needs only features + same-timestamp market baseline, no results."""
    ensure(weight in WEIGHTS and model.feature_name() == list(features),
           "v2 model identity/strength changed")
    b = np.asarray(data["市場3着内基準"], dtype=float)
    ensure(np.isfinite(b).all() and ((b > 0)&(b < 1)).all(),
           "invalid pre-race market Top3 baseline")
    raw = model.predict(matrix(data, features), raw_score=True,
                         validate_features=True, num_threads=2)
    p = sigmoid(logit(b)+weight*np.asarray(raw, dtype=float))
    ensure(np.isfinite(p).all(), "nonfinite longshot v2 prediction")
    return p


def race_losses(data, probability):
    y, _ = training_targets(data)
    return pd.DataFrame({"race": data["レースID"].astype(str).to_numpy(),
                         "loss": (probability-y)**2}).groupby(
                             "race", sort=False)["loss"].mean()


def select_validation(data, candidates):
    """One predeclared selection rule, with no test-year outcomes."""
    ensure(pd.to_datetime(data["日付"]).dt.year.eq(2024).all(),
           "feature/weight selection must use 2024 only")
    grid = []
    for name, model, rounds in candidates:
        for weight in WEIGHTS:
            p = predict(data, model, FEATURE_SETS[name], weight)
            grid.append({"featureSet": name, "featureCount": len(FEATURE_SETS[name]),
                         "rounds": rounds, "weight": weight,
                         "equalRaceBrier2024": float(race_losses(data, p).mean())})
    selected = min(grid, key=lambda r:(r["equalRaceBrier2024"],
                                      r["featureCount"], r["weight"], r["featureSet"]))
    return selected, grid


def top_picks(data, p):
    d = data[["レースID", "日付", "馬番", "馬名", "人気", "単勝オッズ", "着順"]].copy()
    d["probability"] = np.asarray(p, dtype=float)
    d["race"] = d["レースID"].astype(str)
    # Deterministic ties: lowest horse number, never finishing position.
    d = d.sort_values(["race", "probability", "馬番"],
                       ascending=[True, False, True])
    return d.groupby("race", sort=False).head(1).reset_index(drop=True)


def freeze_thresholds(validation, probability):
    ensure(pd.to_datetime(validation["日付"]).dt.year.eq(2024).all(),
           "abstention thresholds must use 2024 only")
    picks = top_picks(validation, probability)
    return {str(coverage): (0.0 if coverage == 1 else float(
        np.quantile(picks["probability"], 1-coverage)))
        for coverage in POLICY_COVERAGES}


def bootstrap_mean(values):
    a = np.asarray(values, dtype=float)
    if len(a) < 30:
        return None
    rng = np.random.default_rng(SEED)
    # Bounded memory for either race or date clustering.
    boot = np.array([a[rng.integers(0, len(a), len(a))].mean()
                     for _ in range(1500)])
    return [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]


def paired(data, candidate, other):
    diff = race_losses(data, candidate)-race_losses(data, other)
    dates = data.groupby(data["レースID"].astype(str), sort=False)["日付"].first()
    by_date = pd.DataFrame({"delta": diff, "date": dates}).groupby("date")["delta"]
    # Resample full dates, preserving all races on the sampled date. The
    # estimand remains the equal-race mean, not the equal-date mean.
    daily = by_date.agg(["sum", "count"])
    rng = np.random.default_rng(SEED)
    date_ci = None
    if len(daily) >= 20:
        sums, counts = daily["sum"].to_numpy(), daily["count"].to_numpy()
        boot = []
        for _ in range(1500):
            i = rng.integers(0, len(daily), len(daily))
            boot.append(sums[i].sum()/counts[i].sum())
        date_ci = [float(np.quantile(boot, .025)), float(np.quantile(boot, .975))]
    return {"equalRaceBrierDelta": float(diff.mean()),
            "raceBootstrap95": bootstrap_mean(diff),
            "wholeRaceDateBootstrap95": date_ci,
            "races": len(diff), "raceDates": len(daily)}


def evaluate(data, probability, control, thresholds):
    y, market = training_targets(data)
    methods = {"market": market, "control36": control, "v2": probability}
    picks = {name: top_picks(data, p) for name, p in methods.items()}
    policies = {}
    for coverage, threshold in thresholds.items():
        mask = picks["v2"]["probability"].to_numpy() >= threshold
        n = int(mask.sum())
        policies[coverage] = {"thresholdFrozenOn2024": threshold,
                              "selectedRaces": n,
                              "coverage": n/len(mask), "methods": {}}
        for name, picked in picks.items():
            ensure(picked["race"].tolist() == picks["v2"]["race"].tolist(),
                   "paired pick cohorts differ")
            actual = picked["着順"].to_numpy()[mask] <= 3
            policies[coverage]["methods"][name] = {
                "top3Hits": int(actual.sum()),
                "top3HitRate": float(actual.mean()) if n else None,
                "meanPredictedTop3": float(picked["probability"].to_numpy()[mask].mean()) if n else None,
                "meanSelectedWinOdds": float(picked["単勝オッズ"].to_numpy()[mask].mean()) if n else None,
            }
        hit_delta = (picks["v2"]["着順"].to_numpy()[mask] <= 3).astype(float)-(
            picks["market"]["着順"].to_numpy()[mask] <= 3).astype(float)
        policies[coverage]["v2MinusMarketHitRate"] = float(hit_delta.mean()) if n else None
        policies[coverage]["pairedHitDeltaRaceBootstrap95"] = bootstrap_mean(hit_delta)
    return {
        "races": int(data["レースID"].nunique()), "horseRows": len(data),
        "start": data["日付"].min().date().isoformat(),
        "end": data["日付"].max().date().isoformat(),
        "actualTop3RateAllLongshots": float(y.mean()),
        "brier": {name: float(np.mean((p-y)**2)) for name, p in methods.items()},
        "logloss": {name: binary_logloss(y, p) for name, p in methods.items()},
        "pairedV2MinusControl": paired(data, probability, control),
        "pairedV2MinusMarket": paired(data, probability, market),
        "abstentionPolicies": policies,
    }, picks


def text_report(report):
    choice = report["selection2024"]["selected"]
    lines = ["# 穴馬の3着以内予測：過去の好走実績を加えた追加研究", "",
             "対象：6番人気以下、単勝10倍以上。上位＝3着以内。",
             "2014〜2023年で学習し、2024年だけで候補・学習回数・補正幅・見送り基準を選択。",
             "2025年を検証後、2025年までで再学習して2026年を検証。",
             "2025・2026年はプロジェクト内で既に調べた期間で、完全に未知の試験ではありません。", "",
             f"2024年に選択した候補：{choice['featureSet']}（{choice['featureCount']}項目）。", "",
             "| 年 | 対象レース | 人気順で選ぶ3着内率 | 36項目の対照モデル | 今回候補 |",
             "|---|---:|---:|---:|---:|"]
    for year in ("2025", "2026"):
        r = report[year]; p = r["abstentionPolicies"]["1.0"]["methods"]
        def hit(name):
            m = p[name]
            return f"{m['top3Hits']}/{r['races']}（{100*m['top3HitRate']:.2f}%）"
        lines.append(f"| {year} | {r['races']} | {hit('market')} | {hit('control36')} | {hit('v2')} |")
    lines += ["", "見送り基準は2024年で固定。人気順と対照モデルも同じ選択レースで比較。", "",
              "| 年 | 2024年での目標参加率 | 実際の参加率 | 評価レース | 人気順の3着内率 | 今回候補の3着内率 |",
              "|---|---:|---:|---:|---:|---:|"]
    for year in ("2025", "2026"):
        for coverage, p in report[year]["abstentionPolicies"].items():
            m = p["methods"]
            market, v2 = m['market']['top3HitRate'], m['v2']['top3HitRate']
            lines.append(f"| {year} | {float(coverage)*100:.0f}% | {p['coverage']*100:.1f}% | "
                         f"{p['selectedRaces']} | {market*100:.2f}% | {v2*100:.2f}% |")
    lines += ["", "確率誤差（Brier、低いほど良い）：", "",
              "| 年 | 市場基準 | 対照モデル | 今回候補 | 候補−対照・レース平均 | 日付単位95%区間 |",
              "|---|---:|---:|---:|---:|---|"]
    for year in ("2025", "2026"):
        r = report[year]; b = r["brier"]; delta = r["pairedV2MinusControl"]
        lines.append(f"| {year} | {b['market']:.6f} | {b['control36']:.6f} | {b['v2']:.6f} | "
                     f"{delta['equalRaceBrierDelta']:+.6f} | {delta['wholeRaceDateBootstrap95']} |")
    lines += ["", "## 読み方", "",
              "人気順で選ぶ方法も同じ穴馬条件の中から1頭を選びます。通常の1番人気ではありません。",
              "見送りによる好走率上昇と、AIによる順位改善は別に判断します。",
              "同じレースに絞った市場との比較、対象数、参加率を合わせて示しています。",
              "信頼区間が0をまたぐ差は、改善したと断定できません。", "",
              "## 制約", ""]
    lines += ["- "+s for s in report["limitations"]]
    return "\n".join(lines)+"\n"


def main():
    ensure(HISTORY.is_file(), "missing source history")
    source_sha = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    hist = pd.read_parquet(HISTORY)
    ensure(list(hist.columns) == BASE_COLS, "historical schema changed")
    print("V2_BUILD_CAUSAL_PLACE_HISTORY", len(hist), flush=True)
    priors = place_history(hist)
    # Avoid legacy add_features' whole-table median looking into the future.
    # 480kg is a fixed research imputation, also used by control36 here.
    safe_hist = hist.copy()
    safe_hist["馬体重"] = pd.to_numeric(safe_hist["馬体重"], errors="coerce").fillna(480.)
    print("V2_REBUILD_OLDER_FEATURES", flush=True)
    older = add_extras(prepare_longshot(prepare_history(safe_hist)), priors)
    train_data, validation, test25 = split_longshot(older)
    print("V2_CHRONOLOGICAL_PARTITIONS", len(train_data), len(validation), len(test25), flush=True)
    candidates = []
    for name, features in FEATURE_SETS.items():
        model, rounds = train(train_data, features, validation)
        candidates.append((name, model, rounds))
        print("V2_FIT_2024", name, rounds, flush=True)
    choice, grid = select_validation(validation, candidates)
    selected_model = next(model for name, model, _ in candidates if name == choice["featureSet"])
    control_model = next(model for name, model, _ in candidates if name == "control36")
    control_choice = min((r for r in grid if r["featureSet"] == "control36"),
                         key=lambda r:(r["equalRaceBrier2024"], r["weight"]))
    features = FEATURE_SETS[choice["featureSet"]]
    thresholds = freeze_thresholds(validation, predict(validation, selected_model, features, choice["weight"]))
    # All choices are locked before either test-year outcome is scored.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    protocol = {"version": VERSION, "selected": choice, "control": control_choice,
                "grid2024": grid, "thresholds2024": thresholds,
                "sourceHistorySha256": source_sha,
                "selectionTimestampJST": datetime.now(JST).isoformat(),
                "selectionRule": "minimum equal-race 2024 Brier, then fewer features, then smaller residual weight"}
    (OUTPUT/"selection2024.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print("V2_SELECTION_LOCKED", json.dumps(choice), flush=True)
    pred25 = predict(test25, selected_model, features, choice["weight"])
    control25 = predict(test25, control_model, FEATURES, control_choice["weight"])
    score25, picks25 = evaluate(test25, pred25, control25, thresholds)
    # Preserve the <=2023 model so 2025 can be reproduced without retraining.
    selected_model.save_model(str(OUTPUT/"model_through2023.txt"))
    full = pd.concat([train_data, validation, test25], ignore_index=True)
    final, _ = train(full, features, rounds=choice["rounds"])
    control_final, _ = train(full, FEATURES, rounds=control_choice["rounds"])
    final.save_model(str(OUTPUT/"model_through2025.txt"))
    control_model.save_model(str(OUTPUT/"control_through2023.txt"))
    control_final.save_model(str(OUTPUT/"control_through2025.txt"))
    print("V2_REBUILD_2026_FEATURES", flush=True)
    recent = add_extras(prepare_longshot(prepare_2026(safe_hist)), priors)
    pred26 = predict(recent, final, features, choice["weight"])
    control26 = predict(recent, control_final, FEATURES, control_choice["weight"])
    score26, picks26 = evaluate(recent, pred26, control26, thresholds)
    flags = {"researchOnly": True, "candidatePromotionApproved": False,
             "formalPredictionAIChanged": False, "autoBetEnabled": False,
             "formalRecommendationsEnabled": False}
    report = {
        "schemaVersion": 1, "version": VERSION,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "status": "retrospective_additional_candidate_only", **flags,
        "sourceHistorySha256": source_sha, "sourceRows": len(hist),
        "sourceLastRaceDate": pd.to_datetime(hist["日付"]).max().date().isoformat(),
        "longshotDefinition": {"popularityMinimum": 6, "winOddsMinimum": 10.0},
        "target": "finish <= 3", "selectedFeatures": list(features),
        "selection2024": protocol, "2025": score25, "2026": score26,
        "modelHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in OUTPUT.glob("*.txt")},
        "causalHistory": {"sameDayExcluded": True, "missingFinishesAreLosses": False,
                           "jumpsExcluded": True, "fixedBodyWeightImputationKg": 480.},
        "limitations": [
            "最終オッズ・最終人気を使う過去検証です。発走10分前の実測成績ではありません。",
            "2025・2026年は過去の研究で既に参照済みです。完全に未知の独立検証とは扱いません。",
            "過去の好走実績の追加が、既存候補より役立つかを調べる研究です。正式モデルへの採用は未承認です。",
            "36項目の対照モデルも欠損馬体重を固定480kgで補完し直しています。旧候補の公表値と完全に同一ではありません。",
            "見送り後の参加率は検証期間の実測です。毎年必ず50%・25%になる設定ではありません。",
            "予測の好走率と馬券の利益は別です。今回、複勝払戻や回収率は検証していません。",
            "入力の寄与は好走原因の証明ではありません。未来の発走前保存データで再検証が必要です。",
        ],
    }
    (OUTPUT/"research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (OUTPUT/"README.md").write_text(text_report(report), encoding="utf-8")
    for year, methods in (("2025", picks25), ("2026", picks26)):
        detail = methods["v2"].copy()
        for name in ("market", "control36"):
            detail[name+"HorseNumber"] = methods[name]["馬番"]
            detail[name+"Finish"] = methods[name]["着順"]
        detail.to_csv(OUTPUT/("picks_"+year+".csv"), index=False)
    print("LONGSHOT_V2_RESEARCH_OK", json.dumps({"choice2024": choice,
          "2025": score25["abstentionPolicies"], "2026": score26["abstentionPolicies"]},
          ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
