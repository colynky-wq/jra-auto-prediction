"""Test pre-race forecasts of early position and their value for longshot Top3."""
from __future__ import annotations

import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_engine import add_features, add_rating
from research_longshot_top3_v2 import (
    FEATURE_SETS, OUTPUT as SPECIALIST, add_extras, bootstrap_mean, place_history, predict,
)
from train_longshot_top3 import HISTORY, ROOT, logit, prepare_longshot, sigmoid
from train_top2_top3_models import binary_logloss

OUT = ROOT / "research/model_candidates/longshot_position_v1"
SEED = 20261123
FEATURES = ("lastPosition", "meanLast3", "last3FrontRate", "headroom", "knownLast3",
            "nearDistanceLast3", "sameSurfaceLast3", "fieldSize", "horseNumberFraction",
            "distance", "turf", "age", "carriedWeight", "priorFrontFieldPercentile", "strongRivals")
CONTEXT_FEATURES = FEATURES[-2:]
PARAMS = {"objective": "binary", "metric": "binary_logloss", "learning_rate": .05,
          "num_leaves": 15, "max_depth": 5, "min_data_in_leaf": 400, "lambda_l2": 20,
          "seed": SEED, "force_col_wise": True, "deterministic": True, "num_threads": 2,
          "verbosity": -1}
SLOPES = (0., .25, .5, 1., 2.)


def position_inputs(history):
    """Only earlier results affect the inputs; the current corner is the label."""
    known_surface = history["芝ダ"].isin(["芝", "ダ"])
    raw = history.loc[known_surface, ["レースID", "馬番", "馬キー", "日付", "芝ダ", "距離",
                                      "頭数", "初角", "年齢", "斤量", "単勝オッズ", "人気"]].copy()
    day = pd.to_datetime(raw["日付"], errors="raise")
    raw["日付"] = day.dt.normalize()
    if raw["馬キー"].isna().any() or raw.duplicated(["馬キー", "日付"]).any():
        raise ValueError("ambiguous horse or same-day prior histories")
    raw.sort_values(["馬キー", "日付", "レースID", "馬番"], inplace=True)
    raw.reset_index(drop=True, inplace=True)
    eligible = raw["単勝オッズ"].gt(0) & raw["人気"].ge(1)
    # The archived Niigata 1000 straight has no corner; its position came
    # from a different observation point and cannot train this target.
    straight = raw["レースID"].astype(str).str[4:6].eq("04") & raw["芝ダ"].eq("芝") & raw["距離"].eq(1000)
    valid = eligible & ~straight & raw["頭数"].ge(3) & raw["初角"].between(1, raw["頭数"])
    current = ((raw["初角"] - 1)/(raw["頭数"] - 1)).where(valid)
    label = raw["初角"].le(np.ceil(raw["頭数"]/3)).where(valid).astype(float)
    # Ignore archived non-runners when counting the last three starts.
    source = raw.loc[eligible, ["馬キー", "芝ダ", "距離"]].copy()
    source["position"], source["front"] = current.loc[eligible], label.loc[eligible]
    prior = source.groupby("馬キー", sort=False)
    last = [prior[["position", "front", "芝ダ", "距離"]].shift(n).reindex(raw.index) for n in (1, 2, 3)]
    pos = pd.concat([r["position"] for r in last], axis=1)
    front = pd.concat([r["front"] for r in last], axis=1)
    p0 = (front.sum(axis=1) + 2/3)/(front.count(axis=1) + 2)
    out = raw[["レースID", "馬番", "日付"]].copy()
    out["observedFront"] = label
    out["straight"] = straight
    out["lastPosition"] = pos.iloc[:, 0]
    out["meanLast3"] = pos.mean(axis=1)
    out["last3FrontRate"] = p0
    out["headroom"] = pos.min(axis=1)
    out["knownLast3"] = pos.count(axis=1)
    out["nearDistanceLast3"] = sum(raw["距離"].sub(r["距離"]).abs().le(200) for r in last)
    out["sameSurfaceLast3"] = sum(raw["芝ダ"].eq(r["芝ダ"]) for r in last)
    out["fieldSize"] = raw["頭数"]
    out["horseNumberFraction"] = raw["馬番"]/raw["頭数"]
    out["distance"] = raw["距離"]
    out["turf"] = raw["芝ダ"].eq("芝").astype(float)
    out["age"] = raw["年齢"]
    out["carriedWeight"] = raw["斤量"]
    # Active rivals' lagged histories only. Neither their current result
    # nor the current horse's result enters the relative comparisons.
    eligible_rate = p0.where(eligible)
    ids = raw["レースID"]
    out["priorFrontFieldPercentile"] = eligible_rate.groupby(ids).rank(pct=True)
    strong = ((p0 >= .6) & eligible).astype(int)
    out["strongRivals"] = strong.groupby(ids).transform("sum") - strong
    return out.loc[eligible].reset_index(drop=True)


def fit_position(frame, features):
    years = frame["日付"].dt.year
    older = frame.loc[(years <= 2023) & frame["observedFront"].notna()]
    validation = frame.loc[(years == 2024) & frame["observedFront"].notna()]
    train_data = lgb.Dataset(older[list(features)], label=older["observedFront"])
    val_data = lgb.Dataset(validation[list(features)], label=validation["observedFront"], reference=train_data)
    provisional = lgb.train(PARAMS, train_data, num_boost_round=180, valid_sets=[val_data],
                            callbacks=[lgb.early_stopping(25, verbose=False), lgb.log_evaluation(0)])
    combined = frame.loc[(years <= 2024) & frame["observedFront"].notna()]
    champion = lgb.train(PARAMS, lgb.Dataset(combined[list(features)], label=combined["observedFront"]),
                         num_boost_round=provisional.best_iteration)
    return champion, provisional.best_iteration


def ranks_auc(y, p):
    positives = y.sum()
    ranks = pd.Series(p).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - positives*(positives+1)/2)/(positives*(len(y)-positives)))


def top_score(frame, field):
    return (frame.sort_values([field, "馬番"], ascending=[False, True], kind="stable")
            .drop_duplicates("レースID").set_index("レースID").sort_index())


def position_metrics(frame):
    eligible = frame.loc[frame["observedFront"].notna()].copy()
    y = eligible["observedFront"].to_numpy()
    methods = {}
    for name in ("last3FrontRate", "horseOnly", "withRivals"):
        p = eligible[name].to_numpy()
        picks = top_score(eligible, name)
        methods[name] = {"brier": float(np.mean((p-y)**2)), "auc": ranks_auc(y, p),
                         "frontHits": int(picks["observedFront"].sum()), "racePicks": len(picks)}
    return {"horses": len(eligible), "methods": methods}


def add_top3_scores(history, positions, meta):
    safe = history.copy()
    safe["馬体重"] = safe["馬体重"].fillna(480.)
    safe.sort_values(["日付", "レースID", "馬番"], inplace=True)
    safe.reset_index(drop=True, inplace=True)
    engine = add_features(add_rating(safe))
    priors = place_history(history)
    features = FEATURE_SETS[meta["selection2024"]["selected"]["featureSet"]]
    weight = meta["selection2024"]["selected"]["weight"]
    dated = {}
    for year, trained in ((2025, 2023), (2026, 2025)):
        archived = pd.read_csv(SPECIALIST / f"picks_{year}.csv", dtype={"race": str}).set_index("race").sort_index()
        same_races = engine.loc[engine["レースID"].isin(archived.index) & engine["対象"] & engine["着順"].notna()]
        data = add_extras(prepare_longshot(same_races.reset_index(drop=True)), priors)
        model = lgb.Booster(model_file=str(SPECIALIST / f"model_through{trained}.txt"))
        data["v2Top3"] = predict(data, model, features, weight)
        replay = top_score(data, "v2Top3")
        if (not replay.index.equals(archived.index) or not (replay["馬番"].to_numpy() == archived["馬番"].to_numpy()).all()
                or not np.allclose(replay["v2Top3"], archived["probability"], atol=1e-8, rtol=0)):
            raise ValueError(f"{year} V2 replay and preserved predictions disagree")
        if positions is not None:
            joined = data.merge(positions[["レースID", "馬番", "observedFront", "straight", "horseOnly",
                                           "withRivals", "last3FrontRate"]],
                                on=["レースID", "馬番"], validate="one_to_one")
            if len(joined) != len(data):
                raise ValueError("position outputs dropped specialist runners")
            data = joined
        data["top3"] = data["着順"].le(3).astype(int)
        dated[year] = data
    return dated


def adjusted(frame, signal, center, slope):
    p = frame["v2Top3"].to_numpy()
    if slope == 0:
        return p.copy()
    return np.where(frame["straight"].to_numpy(), p, sigmoid(logit(p) + slope*(signal-center)))


def evaluate_addon(training, evaluation):
    if (training["日付"].max() >= evaluation["日付"].min()
            or not training["日付"].dt.year.eq(2025).all() or not evaluation["日付"].dt.year.eq(2026).all()):
        raise ValueError("addon tuning and evaluation must be chronological")
    # The contender correction is chosen only on 2025, conditional on the
    # already-frozen v2 probabilities. 2026 has no coefficient selection.
    trial = training.loc[~training["straight"]]
    center = float(trial["withRivals"].mean())
    costs = {str(s): binary_logloss(trial["top3"], adjusted(trial, trial["withRivals"], center, s)) for s in SLOPES}
    slope = min(SLOPES, key=lambda s: costs[str(s)])
    y = evaluation["top3"].to_numpy()
    baseline = top_score(evaluation, "v2Top3")
    p0 = evaluation["v2Top3"].to_numpy()

    def compare(setting):
        p1 = adjusted(evaluation, evaluation["withRivals"], center, setting)
        a = top_score(evaluation.assign(positionAdjusted=p1), "positionAdjusted")
        if not baseline.index.equals(a.index):
            raise ValueError("policy comparison uses different races")
        d = a["top3"].to_numpy() - baseline["top3"].to_numpy()
        return {
            "races": len(a), "horses": len(evaluation), "v2Hits": int(baseline["top3"].sum()),
            "adjustedHits": int(a["top3"].sum()), "changedSelections": int((a["馬番"] != baseline["馬番"]).sum()),
            "newOnlyHits": int((d == 1).sum()), "oldOnlyHits": int((d == -1).sum()),
            "pairedRaceDifference95": bootstrap_mean(d),
            "v2Brier": float(np.mean((p0-y)**2)), "adjustedBrier": float(np.mean((p1-y)**2)),
        }

    return {"center": center, "chosenSlope": slope, "2025LogLossGrid": costs, "2026": compare(slope),
            "2026ExploratoryForcedCorrections": {str(s): compare(s) for s in SLOPES if s}}


def markdown(report):
    lines = ["# 穴馬の先行位置を発走前に予測する検証", "",
             "先行は最初に記録されたコーナーの前方3分の1。新潟芝1000m直線は位置の実測比較から除外した。",
             "馬番号、距離、過去3走の位置などを使い、同じレースの相手の先行傾向も追加した。",
             "位置モデルは2024年末までのデータで固定し、2025・2026年の穴馬で検証。穴馬は6番人気以下・最終単勝10倍以上。", "",
             "| 年 | 方式 | 先行を予測した1頭が実際に前方 | Brier（小さい方が良い） | AUC |",
             "|---|---|---:|---:|---:|"]
    names = {"last3FrontRate":"過去3走の先行頻度", "horseOnly":"各馬の発走前情報", "withRivals":"相手馬の先行傾向も使う"}
    for year, outcome in report["position"].items():
        for name, value in outcome["methods"].items():
            n, h = value["racePicks"], value["frontHits"]
            lines.append(f"| {year} | {names[name]} | {h:,}/{n:,}（{100*h/n:.2f}%） | {value['brier']:.5f} | {value['auc']:.4f} |")
    r = report["top3Addon"]["2026"]
    lines += ["", "## 先行予測を穴馬の3着以内予測に加えた結果", "",
              "2025年だけで補正の強さを選び、2026年の同じレースと馬で比較。",
              f"2025年で最も確率の誤差が小さかった補正係数は{report['top3Addon']['chosenSlope']}（0なら補正なし）。", "",
              "| 2026年 | 1頭選択の3着内 | Brier |", "|---|---:|---:|",
              f"| 既存の穴馬V2 | {r['v2Hits']:,}/{r['races']:,}（{100*r['v2Hits']/r['races']:.2f}%） | {r['v2Brier']:.6f} |",
              f"| 先行予測を加えた候補 | {r['adjustedHits']:,}/{r['races']:,}（{100*r['adjustedHits']/r['races']:.2f}%） | {r['adjustedBrier']:.6f} |", "",
              f"選択が変わったレースは{r['changedSelections']}。新候補だけ3着内が{r['newOnlyHits']}、既存V2だけ3着内が{r['oldOnlyHits']}。", "",
              "### 先行予測を強制的に加えた参考比較", "",
              "初回の分析後に、2025年に候補とした正の補正係数を全部適用して比較した。ここでは係数を選ばない。", "",
              "| 補正係数 | 3着内 | 選択変更 | 新候補のみ3着内 / V2のみ3着内 | Brier |", "|---|---:|---:|---:|---:|"]
    for setting, result in report["top3Addon"]["2026ExploratoryForcedCorrections"].items():
        lines.append(f"| {setting} | {result['adjustedHits']:,}/{result['races']:,} | {result['changedSelections']} | "
                     f"{result['newOnlyHits']} / {result['oldOnlyHits']} | {result['adjustedBrier']:.6f} |")
    lines += ["",
              "将来の同じ発走前時点の記録で再検証が必要。着順照合には過去の最終オッズを使った。", ""]
    return "\n".join(lines)


def main():
    meta = json.loads((SPECIALIST / "research.json").read_text(encoding="utf-8"))
    source = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    if source != meta["sourceHistorySha256"]:
        raise ValueError("V2 archives and history do not share the same source")
    history = pd.read_parquet(HISTORY)
    history["日付"] = pd.to_datetime(history["日付"])
    print("BUILD_POSITION_INPUTS", len(history), flush=True)
    frame = position_inputs(history)
    solo = FEATURES[:-len(CONTEXT_FEATURES)]
    model1, rounds1 = fit_position(frame, solo)
    model2, rounds2 = fit_position(frame, FEATURES)
    frame = frame.loc[frame["日付"].dt.year.ge(2025)].copy()
    frame["horseOnly"] = model1.predict(frame[list(solo)], validate_features=True, num_threads=2)
    frame["withRivals"] = model2.predict(frame[list(FEATURES)], validate_features=True, num_threads=2)
    print("REPLAY_EXISTING_V2", rounds1, rounds2, flush=True)
    dated = add_top3_scores(history, frame, meta)
    report = {"version": "longshot-position-v1-20261123", "researchOnly": True,
              "historySha256": source, "positionTrainThrough": "2024-12-31",
              "positionTreeCounts": {"horseOnly": rounds1, "withRivals": rounds2},
              "position": {str(year): position_metrics(data) for year, data in dated.items()},
              "top3Addon": evaluate_addon(dated[2025], dated[2026])}
    OUT.mkdir(parents=True, exist_ok=True)
    model2.save_model(str(OUT / "position_model.txt"))
    report["positionModelSha256"] = hashlib.sha256((OUT / "position_model.txt").read_bytes()).hexdigest()
    (OUT / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (OUT / "README.md").write_text(markdown(report), encoding="utf-8")
    print("LONGSHOT_POSITION_RESEARCH_OK", json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
