"""Test race-level upset predictions and conditional inclusion of one longshot."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

import formal_portfolio_capture as capture
from inspect_frozen_win_probabilities import official_podium, prob
from research_longshot_top3_v2 import OUTPUT as SPECIALIST, bootstrap_mean
from train_longshot_top3 import HISTORY, POPRANK_MIN, ROOT, WINODDS_MIN, logit, sigmoid
from train_top2_top3_models import binary_logloss

OUT = ROOT / "research/model_candidates/conditional_longshot_v1"
VERSION = "conditional-longshot-top3-v1-20261123"
HIGH_RISK = 0.50
FEATURES = ("marketEvent", "fieldSize", "longshotCount", "favoriteWinShare",
            "top3WinShare", "longshotWinShare", "bestLongshotWinShare", "winEntropy")
PARAMS = {"objective": "binary", "metric": "binary_logloss", "num_leaves": 7,
          "max_depth": 3, "min_data_in_leaf": 400, "lambda_l2": 30,
          "learning_rate": .04, "num_threads": 2, "deterministic": True,
          "force_col_wise": True, "seed": 20261123, "verbosity": -1}


def race_features(odds, longshots):
    """Probability of at least one longshot under sequential win-odds weights."""
    weights = 1.0 / np.asarray(odds, dtype=float)
    weights /= weights.sum()
    non = weights[~longshots]
    if not longshots.any():
        market = 0.0
    else:
        i, j = np.nonzero(~np.eye(len(non), dtype=bool))
        first, second = non[i], non[j]
        remaining_non_longshots = non.sum() - first - second
        no_longshot = np.sum(first * second * remaining_non_longshots / (
            (1 - first) * (1 - first - second)
        ))
        market = float(np.clip(1 - no_longshot, 0, 1))
    largest = np.sort(weights)[-3:]
    tail = weights[longshots]
    return dict(zip(FEATURES, (
        market, len(weights), len(tail), largest[-1], largest.sum(), tail.sum(),
        tail.max() if len(tail) else 0.0, -np.dot(weights, np.log(weights)),
    ), strict=True))


def past_races(history):
    rows = []
    history = history.loc[~history["障害"].fillna(False).astype(bool)].sort_values(["日付", "馬番"])
    # Non-runners carry no odds, popularity or finish in this archive. The
    # declared active field must still match after excluding those rows.
    history = history.loc[~history[["単勝オッズ", "人気", "着順"]].isna().all(axis=1)]
    for race_id, race in history.groupby("レースID", sort=False):
        odds, popularity, finish, field = (race[column].to_numpy(dtype=float) for column in
                                           ("単勝オッズ", "人気", "着順", "頭数"))
        numbers = race["馬番"].to_numpy(dtype=int)
        if (not (len(race) >= 3 and (field == len(race)).all() and len(set(numbers)) == len(numbers))
                or not (np.isfinite(odds).all() and (odds >= 1).all()
                        and np.isfinite(popularity).all() and np.isfinite(finish).all())
                or not all((finish == rank).sum() == 1 for rank in (1, 2, 3))):
            continue
        mask = (popularity >= POPRANK_MIN) & (odds >= WINODDS_MIN)
        if not mask.any():
            continue
        # Market Top3 marginals have the same ordering as their positive win weights.
        order = np.lexsort((numbers, odds))
        podium = frozenset(int(n) for n in numbers[(finish >= 1) & (finish <= 3)])
        rows.append({"raceID": str(race_id), "date": race["日付"].iat[0],
                     **race_features(odds, mask), "upset": int(bool(podium & set(numbers[mask]))),
                     "podium": podium, "baseline": tuple(int(n) for n in numbers[order[:3]]),
                     "longshotNumbers": frozenset(int(n) for n in numbers[mask])})
    return pd.DataFrame(rows)


def variation(data, model=None, mode="market", offset=0.0):
    base = data["marketEvent"].to_numpy(dtype=float)
    if mode == "market":
        return base
    correction = offset if mode == "marketCalibrated" else model.predict(
        data.loc[:, list(FEATURES)], raw_score=True, validate_features=True, num_threads=2,
    )
    p = sigmoid(logit(base) + correction)
    return np.where(data["longshotCount"].to_numpy() == 0, 0.0, p)


def train(data, validation=None, rounds=None):
    x = data.loc[:, list(FEATURES)]
    dataset = lgb.Dataset(x, label=data["upset"], init_score=logit(data["marketEvent"]))
    if validation is None:
        return lgb.train(PARAMS, dataset, num_boost_round=rounds)
    hold = lgb.Dataset(validation.loc[:, list(FEATURES)], label=validation["upset"],
                       init_score=logit(validation["marketEvent"]), reference=dataset)
    return lgb.train(PARAMS, dataset, num_boost_round=150, valid_sets=[hold],
                     callbacks=[lgb.early_stopping(25, verbose=False), lgb.log_evaluation(0)])


def brier(data, probability):
    return float(np.mean((data["upset"].to_numpy() - probability)**2))


def risk_summary(data, probability):
    labels = data["upset"].to_numpy()
    groups = {"low": probability < .25, "middle": (probability >= .25) & (probability < HIGH_RISK),
              "high": probability >= HIGH_RISK}
    return {"races": len(data), "actualEventRate": float(labels.mean()),
            "marketBrier": brier(data, variation(data)), "modelBrier": brier(data, probability),
            "modelLogLoss": binary_logloss(labels, probability), "bands": {
                name: {"races": int(mask.sum()), "actualEvents": int(labels[mask].sum()),
                       "actualPct": float(labels[mask].mean()*100) if mask.any() else None,
                       "meanPredictedPct": float(probability[mask].mean()*100) if mask.any() else None}
                for name, mask in groups.items()}}


def compare_picks(data, probability, longshot_col, threshold=HIGH_RISK):
    """Give each policy the same fixed budget of three predicted places."""
    stats = Counter(dict.fromkeys(("switches", "baselineTop3Hits", "adaptiveTop3Hits",
         "baselineExactPodiums", "adaptiveExactPodiums", "baselineActualLongshots",
         "adaptiveActualLongshots", "improvedRaces", "worsenedRaces"), 0))
    paired = []
    for row, risk in zip(data.to_dict("records"), probability, strict=True):
        before = set(row["baseline"])
        podium = row["podium"]
        tail = row[longshot_col]
        after = before
        if risk >= threshold and tail not in before:
            after = set(row["baseline"][:2]) | {tail}
            stats["switches"] += 1
        b, a = len(before & podium), len(after & podium)
        stats["baselineTop3Hits"] += b
        stats["adaptiveTop3Hits"] += a
        stats["baselineExactPodiums"] += b == 3
        stats["adaptiveExactPodiums"] += a == 3
        stats["baselineActualLongshots"] += len(before & podium & row["longshotNumbers"])
        stats["adaptiveActualLongshots"] += len(after & podium & row["longshotNumbers"])
        stats["improvedRaces"] += a > b
        stats["worsenedRaces"] += a < b
        paired.append(a - b)
    return {**stats, "races": len(data), "meanMatchedHorsesDelta": float(np.mean(paired)),
            "pairedRaceBootstrap95": bootstrap_mean(paired)}


def evaluate_period(data, probabilities, longshot_col):
    sensitivity = {}
    for threshold in (.70, .80, .90):
        result = compare_picks(data, probabilities, longshot_col, threshold)
        sensitivity[f"{threshold:.0%}"] = {
            "changes": result["switches"],
            "matchedHorsesDifference": result["adaptiveTop3Hits"] - result["baselineTop3Hits"],
        }
    return {"risk": risk_summary(data, probabilities),
            "picks": compare_picks(data, probabilities, longshot_col),
            "thresholdSensitivityExploratory": sensitivity}


def benchmark_t10(audit):
    if (audit.get("projectionState") != "verified" or audit["aligned"].get("countsAreCurrent") is not True
            or audit.get("cohortLockRecoveryRequired") is True
            or len(audit["races"]) != audit["aligned"]["racesObserved"]):
        raise ValueError("verified frozen T-10 audit required")
    rows, chaos = [], {}
    excluded = {(r["raceDate"], r["raceID"]) for r in audit["quality"]["resultExclusions"]}
    for row in audit["races"]:
        day, race_id = row["raceDate"], row["raceID"]
        if row["resultState"] != "matched" or (day, race_id) in excluded:
            continue
        archive = capture.load(ROOT, day, race_id)
        race = archive["prediction"]
        if (archive["fingerprint"] != row["inputFingerprint"] or race["predictionTiming"] != "live_10min"
                or race["captureQuality"] != "formal_10min"):
            raise ValueError(f"invalid frozen T-10 provenance for {race_id}")
        capture.window(day, race["startTime"], race["capturedAtJST"])
        horses = race["horses"]
        odds = np.array([h["odds"] for h in horses], dtype=float)
        mask = np.array([h["odds"] >= WINODDS_MIN and h["popularity"] >= POPRANK_MIN for h in horses])
        if not mask.any():
            continue
        numbers = [horse["number"] for horse in horses]
        try:
            podium = official_podium(row["actualFinish"], numbers, 3, race_id)
        except ValueError as exc:
            if "ambiguous podium dead heat" not in str(exc):
                raise
            continue
        if not podium:
            continue
        order = sorted(horses, key=lambda h: (-prob(h["aiTop3Probability"], "T-10 Top3"), h["number"]))
        tail = frozenset(n for n, longshot in zip(numbers, mask, strict=True) if longshot)
        event = int(bool(podium & tail))
        rows.append({"upset": event, "raceID": race_id, **race_features(odds, mask), "podium": podium,
                     "baseline": tuple(h["number"] for h in order[:3]), "longshotNumbers": tail,
                     "aiTail": next(h["number"] for h in order if h["number"] in tail)})
        level = archive["signal"]["chaos"]["level"]
        bucket = "1–2" if level <= 2 else "3" if level == 3 else "4–5"
        counters = chaos.setdefault(bucket, Counter())
        counters["races"] += 1
        counters["actualEvents"] += event
    return pd.DataFrame(rows), chaos


def markdown(report):
    lines = ["# 混戦度に応じて穴馬を入れる予測の検証", "",
             "荒れやすさの判定は穴馬候補を探す目安になる。一律に上位3頭を入れ替えると、今回の比較では全体の精度が下がった。", "",
             "6番人気以下・単勝10倍以上の馬が3着以内に1頭以上入る確率を推定した。",
             "競走単位の確率が50%以上と出た場合に、予想上位3頭のうち3番手と穴馬を入れ替えた。",
             "過去検証では市場の上位3頭と穴馬専用モデルの1位を使用した。発走10分前の参考比較では、現行AIで上位3頭と穴馬を選んだ。", "",
             f"2024年の確率誤差が最も小さい方式を採用した。採用した方式は `{report['selection']['mode']}`。", "",
             "| 期間 | レース数 | 混戦確率25%未満の実際の穴馬好走率 | 混戦確率50%以上の実際の穴馬好走率 | 入替回数 | 上位3頭の一致数 基準→入替 | 拾えた穴馬 基準→入替 | 3頭すべて一致 基準→入替 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, period in report["periods"].items():
        r, p = period["risk"], period["picks"]
        def band(key):
            value = r["bands"][key]
            return f"{value['actualPct']:.1f}%（{value['actualEvents']}/{value['races']}R）" if value["races"] else "—"
        lines.append(f"| {name} | {r['races']:,} | {band('low')} | {band('high')} | {p['switches']:,} | "
                     f"{p['baselineTop3Hits']:,} → {p['adaptiveTop3Hits']:,} | "
                     f"{p['baselineActualLongshots']:,} → {p['adaptiveActualLongshots']:,} | "
                     f"{p['baselineExactPodiums']:,} → {p['adaptiveExactPodiums']:,} |")
    lines += ["", "発走10分前のデータで既存の混戦度別に集計した穴馬の好走率。", "",
              "| 既存の混戦度 | 穴馬好走あり / 全体 | 割合 |", "|---|---:|---:|"]
    for band, value in sorted(report["t10ExistingChaosBands"].items()):
        lines.append(f"| ★{band} | {value['actualEvents']}/{value['races']} | "
                     f"{100*value['actualEvents']/value['races']:.1f}% |")
    lines += ["", "初回の結果を確認した後に閾値を70%、80%、90%へ上げた参考比較。数値は入替回数 / 3着以内との一致数の差。", "",
              "| 期間 | 70% | 80% | 90% |", "|---|---:|---:|---:|"]
    for name, period in report["periods"].items():
        values = [f"{item['changes']}R / {item['matchedHorsesDifference']:+d}頭"
                  for item in period["thresholdSensitivityExploratory"].values()]
        lines.append(f"| {name} | " + " | ".join(values) + " |")
    lines += ["", "| 期間 | 市場の混戦確率Brier | 2024年に選んだ補正後のBrier |", "|---|---:|---:|"]
    for name, period in report["periods"].items():
        risk = period["risk"]
        lines.append(f"| {name} | {risk['marketBrier']:.6f} | {risk['modelBrier']:.6f} |")
    lines += ["", "比較は1レース3頭で統一した。穴馬を拾った数と、3着以内を拾った総数は別に集計した。",
              "2025年と2026年の大規模検証には最終オッズを利用した。2026年の発走前比較は既に保存されていた予測を使った参考集計。",
              "発走前の穴馬専用モデルの記録が蓄積するまでは、同じ時点にそろえた専用モデルの実測比較を待つ。", "",
              "KeibaAIでの使い方としては、上位を現行予測で選び、混戦時に穴馬を別枠の候補として示す案が適している。",
              "上位3頭への繰り上げは、穴馬1頭と入替対象の馬の3着内確率を同じ尺度で比較できる場合に検討する。", ""]
    return "\n".join(lines)


def main():
    source_sha = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    previous = json.loads((SPECIALIST / "research.json").read_text())
    if previous["sourceHistorySha256"] != source_sha:
        raise ValueError("historical source differs from earlier specialist predictions")
    history = pd.read_parquet(HISTORY, columns=["レースID", "日付", "馬番", "着順", "単勝オッズ", "人気", "頭数", "障害"])
    history["日付"] = pd.to_datetime(history["日付"])
    print("BUILD_RACE_INPUTS", len(history), flush=True)
    data = past_races(history)
    dates = data["date"].dt.year
    older, validation = data.loc[dates <= 2023], data.loc[dates == 2024]
    provisional = train(older, validation)
    offsets = (0., -.1, .1, -.2, .2, -.3, .3, -.4, .4, -.5, .5)
    offset = min(offsets, key=lambda x: brier(validation, variation(validation, mode="marketCalibrated", offset=x)))
    modes = ("market", "marketCalibrated", "residualGBDT")
    scores = {mode: brier(validation, variation(validation, provisional, mode, offset)) for mode in modes}
    chosen = min(modes, key=scores.get)
    rounds = provisional.best_iteration
    forecast = train(data.loc[dates <= 2025], rounds=rounds)
    OUT.mkdir(parents=True, exist_ok=True)
    if chosen == "residualGBDT":
        forecast.save_model(str(OUT / "model_through2025.txt"))
    periods = {}
    for year, booster in ((2025, provisional), (2026, forecast)):
        past = data.loc[dates == year].copy()
        candidates = pd.read_csv(SPECIALIST / f"picks_{year}.csv", dtype={"race":str})
        overview = past.merge(candidates[["race", "馬番"]], left_on="raceID", right_on="race", validate="one_to_one")
        if len(overview) != len(candidates) or not all(tail in eligible for tail, eligible in zip(
                overview["馬番"], overview["longshotNumbers"], strict=True)):
            raise ValueError("specialist predictions and historical eligibility differ")
        probability = variation(overview, booster, chosen, offset)
        periods[str(year)] = evaluate_period(overview, probability, "馬番")
    audit = json.loads((ROOT / "AlignedPortfolioReview.json").read_text())
    actual_t10, existing_levels = benchmark_t10(audit)
    probability = variation(actual_t10, forecast, chosen, offset)
    periods["発走10分前・参考"] = evaluate_period(actual_t10, probability, "aiTail")
    report = {"version": VERSION, "researchOnly": True, "historySha256": source_sha,
              "sourceAuditGeneratedAtJST": audit["generatedAtJST"], "highRiskThreshold": HIGH_RISK,
              "featureNames": FEATURES, "selection": {"mode": chosen, "offset": offset,
                                                        "rounds": rounds, "brier2024": scores},
              "periods": periods, "t10ExistingChaosBands": existing_levels}
    (OUT / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    (OUT / "README.md").write_text(markdown(report), encoding="utf-8")
    print("CONDITIONAL_LONGSHOT_RESEARCH_OK", json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
