"""Evaluate one reserve longshot, then compare on a fixed three-horse budget."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from evaluate_no_current_odds_2026_history import prepare_2026
from feature_engine import ALLF
from research_conditional_longshot import OUT as UPSET_OUTPUT, past_races, variation
from research_longshot_top3_v2 import OUTPUT as SPECIALIST, bootstrap_mean
from topk_runtime import logit, market_top2_top3, normalize_probability_total, sigmoid
from train_longshot_top3 import HISTORY, ROOT

OUT = ROOT / "research/model_candidates/longshot_reserve_v1"
FORMAL_MODEL = ROOT / "production/lightgbm_top3_fixed_2025.txt"
AI_CALIBRATION_END = pd.Timestamp("2026-07-01")


def assemble(history, race_data, predictions, corrections=None):
    """Join the complete field to the one frozen specialist pick per race."""
    by_race = race_data.set_index("raceID")
    picks = predictions.set_index("race")
    if not by_race.index.is_unique or not picks.index.is_unique or set(by_race.index) != set(picks.index):
        raise ValueError("race-level inputs and specialist picks do not match")
    correction_by_race = corrections.set_index(["レースID", "馬番"])["correction"] if corrections is not None else None
    active = history.loc[~history[["単勝オッズ", "人気", "着順"]].isna().all(axis=1)]
    rows = []
    for rid, field in active.groupby("レースID", sort=False):
        if rid not in by_race.index:
            continue
        numbers = field["馬番"].to_numpy(dtype=int)
        odds = field["単勝オッズ"].to_numpy(dtype=float)
        _, market = market_top2_top3(1 / odds)
        if correction_by_race is None:
            probabilities = market
            ranked = np.lexsort((numbers, odds))
        else:
            keys = pd.MultiIndex.from_arrays([[rid] * len(numbers), numbers])
            adjustment = correction_by_race.reindex(keys).to_numpy(dtype=float)
            if not np.isfinite(adjustment).all():
                raise ValueError(f"incomplete current AI replay field {rid}")
            probabilities = normalize_probability_total(sigmoid(logit(market) + adjustment), 3)
            ranked = np.lexsort((numbers, -probabilities))
        race = by_race.loc[rid]
        if len(field) != int(race["fieldSize"]) or len(ranked) < 4:
            raise ValueError(f"wrong active field size {rid}")
        pick = picks.loc[rid]
        tail = int(pick["馬番"])
        tail_p = float(pick["probability"])
        if tail not in race["longshotNumbers"] or not 0 < tail_p < 1:
            raise ValueError(f"invalid specialist prediction {rid}")
        top = tuple(int(n) for n in numbers[ranked[:4]])
        rows.append({"raceID": rid, "date": race["date"], "risk": race["risk"],
                     "podium": race["podium"], "topFour": top, "tail": tail,
                     "thirdProbability": probabilities[ranked[2]], "tailProbability": tail_p})
    if len(rows) != len(picks):
        raise ValueError("active field reconciliation dropped races")
    return pd.DataFrame(rows)


def offset_for_role(probabilities, outcomes):
    """Calibrate both methods to the same binary event by a log-odds offset."""
    base = logit(probabilities)
    lo, hi = -8., 8.
    for _ in range(45):
        mid = (lo + hi) / 2
        if sigmoid(base + mid).mean() < outcomes.mean():
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ready_for_reserve(row, threshold):
    return row["risk"] >= threshold and row["tail"] not in row["topFour"][:3]


def fit_role_offsets(frame, cut, threshold):
    eligible = [row for row in frame.to_dict("records") if ready_for_reserve(row, threshold)]
    if not eligible or max(row["date"] for row in eligible) >= cut:
        raise ValueError("calibration must precede evaluation")
    fit = {"eligibleRaces": len(eligible), "through": max(row["date"] for row in eligible).date().isoformat()}
    for field, number in (("thirdProbability", lambda row: row["topFour"][2]),
                          ("tailProbability", lambda row: row["tail"])):
        p = np.array([row[field] for row in eligible])
        y = np.array([int(number(row) in row["podium"]) for row in eligible])
        fit[field] = offset_for_role(p, y)
    return fit


def evaluate(frame, threshold, calibration=None):
    names = ("baseline3", "reserveLongshot", "ordinaryFourth", "rawPromotion", "calibratedPromotion")
    totals = {name: Counter() for name in names}
    paired = {"reserveMinusFourth": [], "rawMinusThree": [], "calibratedMinusThree": []}
    switched = {"rawPromotion": Counter(), "calibratedPromotion": Counter()}
    reserved, reserve_wins, fourth_wins = 0, 0, 0
    for row in frame.to_dict("records"):
        first, second, third, fourth = row["topFour"]
        top3, podium = {first, second, third}, row["podium"]
        eligible = ready_for_reserve(row, threshold)
        candidate = row["tail"]
        policies = dict.fromkeys(names, top3)
        if eligible:
            policies["reserveLongshot"] = top3 | {candidate}
            policies["ordinaryFourth"] = top3 | {fourth}
            reserved += 1
            reserve_wins += candidate in podium
            fourth_wins += fourth in podium
            edge_raw = float(logit(row["tailProbability"]) - logit(row["thirdProbability"]))
            edges = {"rawPromotion": edge_raw}
            if calibration is not None:
                edges["calibratedPromotion"] = edge_raw + (
                    calibration["tailProbability"] - calibration["thirdProbability"]
                )
            for method, edge in edges.items():
                if edge > 0:
                    policies[method] = {first, second, candidate}
                    switched[method]["cases"] += 1
                    switched[method]["longshotOnlyPlaced"] += candidate in podium and third not in podium
                    switched[method]["originalOnlyPlaced"] += third in podium and candidate not in podium
        hits = {name: len(selected & podium) for name, selected in policies.items()}
        for name, n in hits.items():
            totals[name]["matchedHorses"] += n
            totals[name]["coveredAllThree"] += n == 3
        paired["reserveMinusFourth"].append(hits["reserveLongshot"] - hits["ordinaryFourth"])
        paired["rawMinusThree"].append(hits["rawPromotion"] - hits["baseline3"])
        paired["calibratedMinusThree"].append(hits["calibratedPromotion"] - hits["baseline3"])
    n = len(frame)
    if not n:
        raise ValueError("empty validation cohort")
    for key in ("rawPromotion", "calibratedPromotion"):
        for outcome in ("cases", "longshotOnlyPlaced", "originalOnlyPlaced"):
            switched[key].setdefault(outcome, 0)
    if calibration is None:
        del totals["calibratedPromotion"], paired["calibratedMinusThree"], switched["calibratedPromotion"]
    return {
        "races": n, "from": frame["date"].min().date().isoformat(), "through": frame["date"].max().date().isoformat(),
        "reserveRaces": reserved, "reserveLongshotPlaced": reserve_wins, "ordinaryFourthPlaced": fourth_wins,
        "summaries": {name: {**result, "meanMatchedHorses": result["matchedHorses"] / n,
                              "coveredAllThreePct": 100 * result["coveredAllThree"] / n}
                      for name, result in totals.items()},
        "pairedDifferences": {name: {"horses": int(sum(delta)), "improvedRaces": delta.count(1),
                                     "worsenedRaces": delta.count(-1), "mean95Pct": bootstrap_mean(delta)}
                              for name, delta in paired.items()},
        "promotionCounts": switched,
    }


def markdown(report):
    lines = ["# 混戦時の穴馬の押さえと繰り上げを検証", "",
             "対象は6番人気以下・単勝10倍以上の穴馬がいた平地レース。荒れ予測が50%以上のとき、本線3頭に含まれない穴馬1頭を押さえ候補にした。",
             "同じレースで通常の4番手を追加する方法とも比較した。3頭すべてを含む割合は着順を問わない。", "",
             "| 比較 | 全体 | 押さえを追加したレース | 穴馬の押さえが3着内 | 通常4番手が3着内 | 本線3頭で3着以内の全馬をカバー | 穴馬追加で全馬をカバー | 通常4番手追加で全馬をカバー |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label, period in report["periods"].items():
        s = period["summaries"]
        n, k = period["races"], period["reserveRaces"]
        def ratio(hit, total):
            return f"{hit:,}/{total:,}（{100*hit/total:.1f}%）" if total else "—"
        cells = [f"{n:,}", f"{k:,}", ratio(period["reserveLongshotPlaced"], k),
                 ratio(period["ordinaryFourthPlaced"], k)]
        cells.extend(ratio(s[name]["coveredAllThree"], n) for name in ("baseline3", "reserveLongshot", "ordinaryFourth"))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "### 本線3頭に繰り上げる場合", "",
              "混戦時に、穴馬の3着内確率が本線3番手を上回るときだけ、3番手と差し替えた。",
              "馬単位の確率補正は市場版が2025年、現行AI版が2026年6月までの好走実績に合わせた。評価はどちらも補正に使った期間より後。", "",
              "| 比較 | 確率の補正 | 入替 | 穴馬だけ3着内 | 元の3番手だけ3着内 | 上位3頭の一致数の差 |",
              "|---|---|---:|---:|---:|---:|"]
    for label, period in report["periods"].items():
        for name, stats in period["promotionCounts"].items():
            suffix = "補正あり" if name == "calibratedPromotion" else "補正なし"
            diff = stats["longshotOnlyPlaced"] - stats["originalOnlyPlaced"]
            lines.append(f"| {label} | {suffix} | {stats['cases']} | {stats['longshotOnlyPlaced']} | "
                         f"{stats['originalOnlyPlaced']} | {diff:+d} |")
    lines += ["", "最終オッズを使った過去再現。現行AI版は2025年末までの学習モデルを使い、比較期間は2026年7月以降。",
              "現行AI版の再現では、欠損した馬体重を固定480kgで補った。全期間の馬体重から補完値を決めないための措置。",
              "新しい穴馬専用モデルの発走10分前の実測比較には、同じ時点でそのモデルが保存した確率と結果が必要。", ""]
    return "\n".join(lines)


def main():
    source = hashlib.sha256(HISTORY.read_bytes()).hexdigest()
    gate = json.loads((UPSET_OUTPUT / "research.json").read_text(encoding="utf-8"))
    specialist = json.loads((SPECIALIST / "research.json").read_text(encoding="utf-8"))
    if source != gate["historySha256"] or source != specialist["sourceHistorySha256"]:
        raise ValueError("historical source differs from frozen earlier studies")
    settings = gate["selection"]
    if settings["mode"] not in ("market", "marketCalibrated"):
        raise ValueError("expected frozen odds-based race model")
    threshold = gate["highRiskThreshold"]
    history = pd.read_parquet(HISTORY)
    history["日付"] = pd.to_datetime(history["日付"])
    recent = history.loc[history["日付"].dt.year >= 2025]
    race_data = past_races(recent)
    race_data["risk"] = variation(race_data, mode=settings["mode"], offset=settings["offset"])
    by_year, inputs = {}, {}
    for year in (2025, 2026):
        pred = pd.read_csv(SPECIALIST / f"picks_{year}.csv", dtype={"race": str})
        races = race_data.loc[race_data["raceID"].isin(pred["race"])]
        inputs[year] = (recent.loc[recent["日付"].dt.year == year], races, pred)
        by_year[year] = assemble(*inputs[year])
    cut = pd.Timestamp("2026-01-01")
    market_calibration = fit_role_offsets(by_year[2025], cut, threshold)
    print("MARKET_RESERVE_READY", len(by_year[2025]), len(by_year[2026]), flush=True)

    # Reconstruct the existing Top3 model with the same formula as live
    # inference. The historical inputs use closing odds, so labels remain
    # descriptive replay rather than timestamp-frozen evidence.
    replay_history = history.copy()
    # The live engine can see only the median of past data and the current
    # field. A fixed fill prevents the replay from using any later weights.
    replay_history["馬体重"] = replay_history["馬体重"].fillna(480.)
    runners = prepare_2026(replay_history)
    runner_ids = set(inputs[2026][1]["raceID"])
    runners = runners.loc[runners["レースID"].isin(runner_ids)].copy()
    model = lgb.Booster(model_file=str(FORMAL_MODEL))
    runners["correction"] = model.predict(runners.loc[:, ALLF].astype(np.float32), raw_score=True,
                                          validate_features=True, num_threads=2)
    ai = assemble(*inputs[2026], corrections=runners[["レースID", "馬番", "correction"]])
    ai_calibration = fit_role_offsets(ai.loc[ai["date"] < AI_CALIBRATION_END], AI_CALIBRATION_END, threshold)
    reports = {
        "市場 2025": evaluate(by_year[2025], threshold),
        "市場 2026": evaluate(by_year[2026], threshold, market_calibration),
        "現行AI 2026年7月以降": evaluate(ai.loc[ai["date"] >= AI_CALIBRATION_END], threshold, ai_calibration),
    }
    report = {"version": "longshot-reserve-and-promotion-v1-20261123", "researchOnly": True,
              "historySha256": source, "currentTop3ModelSha256": hashlib.sha256(FORMAL_MODEL.read_bytes()).hexdigest(),
              "upsetThreshold": threshold, "replayMissingWeightKg": 480,
              "marketCalibration": market_calibration,
              "aiCalibration": ai_calibration, "periods": reports}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(markdown(report), encoding="utf-8")
    print("LONGSHOT_RESERVE_RESEARCH_OK", json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
