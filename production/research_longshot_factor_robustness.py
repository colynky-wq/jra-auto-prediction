#!/usr/bin/env python3
"""Read-only research: which longshot Top3 signals replicate in 2025 and 2026?

No new model training decisions: an existing longshot residual candidate and
its frozen 2024 training protocol are reused. The <=2023 provisional model is
used for 2025, and the separately published <=2025 refit is used for 2026.
Historical final odds are NOT genuine T-10 odds. Cohort descriptions and
SHAP attributions establish associations, not causes or betting profitability.

All bins are registered in source before looking at outcome-group performance.
NEVER adopt filters, alter the model, or pick wagers from these explorations.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from train_longshot_top3 import (
    VERSION as LONGSHOT_VERSION, HISTORY, JST, METADATA, MODEL_PATH,
    POPRANK_MIN, WINODDS_MIN, FEATURES, WEIGHTS, ROOT,
    fit, infer_without_results, prepare_longshot, split_longshot,
    training_targets,
)
from train_no_current_odds_challenger import (
    ensure, prepare_history, verify_features,
)
from evaluate_no_current_odds_2026_history import prepare_2026
from train_top2_top3_models import BASE_COLS

REPORT = ROOT / "research/LongshotFactorRobustnessReview.json"
REPORT_MD = REPORT.with_suffix(".md")
VERSION = "longshot-factor-robustness-research-v1-20261123"
SEED = 20261123

# Fixed, human-interpretable partitions; all inclusive lower bound except
# pre-existing eligible longshot (> 6th ranking and >= 10 win odds).
ODDS_BINS = (10, 20, 50, 100, np.inf)
ODDS_LABELS = ("10–19.9", "20–49.9", "50–99.9", "100+")
POPULARITY_BINS = (6, 9, 13, np.inf)
POPULARITY_LABELS = ("6–8", "9–12", "13+")
DISTANCE_BINS = (0, 1401, 1801, 2201, np.inf)
DISTANCE_LABELS = ("≤1400m", "1401–1800m", "1801–2200m", ">2200m")
MARKET_RATIO_BINS = (0, 0.8, 1.0, 1.2, np.inf)
MARKET_RATIO_LABELS = ("<0.8x", "0.8–1.0x", "1.0–1.2x", "≥1.2x")

# Pre-selected diagnostic features, not a new optimized prediction strategy.
PRIOR_FEATURES = ("前走着順率f", "同芝ダ平均", "同芝ダ回数c",
                  "log間隔", "点数差", "脚質初角", "前走秒差f", "前走人気f")


def verify_meta(meta, original: bytes):
    ensure(meta.get("version") == LONGSHOT_VERSION and
           meta.get("modelSha256") == hashlib.sha256(original).hexdigest() and
           meta.get("features") == list(FEATURES) and
           meta.get("featureCount") == len(FEATURES) == 36,
           "published longshot model identity mismatch")
    ensure(meta.get("popularityMinimum") == POPRANK_MIN == 6 and
           meta.get("winOddsMinimum") == WINODDS_MIN == 10.0 and
           meta.get("trainThroughFor2025Test") == "2023-12-31" and
           meta.get("refitThroughFor2026Test") == "2025-12-31" and
           meta.get("yearUsedForEarlyStoppingAndMultiplier") == 2024 and
           meta.get("selectedResidualMultiplier") in WEIGHTS,
           "unexpected training or underdog contract")
    ensure(meta.get("researchOnly") is True and
           meta.get("candidatePromotionApproved") is False and
           meta.get("formalPredictionAIChanged") is False and
           meta.get("autoBetEnabled") is False,
           "only quarantined research candidate is eligible")


def partition(data: pd.DataFrame, candidate, market) -> pd.DataFrame:
    ensure(len(data) == len(candidate) == len(market) and len(data) > 0,
           "partition rows and probabilities mismatch")
    ensure(all(int(year) in (2025, 2026)
               for year in pd.to_datetime(data["日付"]).dt.year.unique()),
           "outside registered evaluation periods")
    ensure(((data["人気"] >= POPRANK_MIN) & (data["単勝オッズ"] >= WINODDS_MIN)).all(),
           "non-longshot included")
    ensure(np.isfinite(candidate).all() and np.isfinite(market).all() and
           np.all((candidate > 0) & (candidate < 1)) and
           np.all((market > 0) & (market < 1)),
           "invalid prediction values")
    out = data[["レースID", "馬番", "馬名", "着順", "人気", "単勝オッズ",
                "芝ダ", "距離", *PRIOR_FEATURES]].copy()
    out["actual"] = (out["着順"] <= 3).astype(int)
    out["pMarket"] = market
    out["pCandidate"] = candidate
    out["squaredMarket"] = (out["pMarket"] - out["actual"]) ** 2
    out["squaredCandidate"] = (out["pCandidate"] - out["actual"]) ** 2
    out["delta"] = out["squaredCandidate"] - out["squaredMarket"]
    out["単勝オッズ帯"] = pd.cut(out["単勝オッズ"],
                            bins=ODDS_BINS, labels=ODDS_LABELS,
                            right=False).astype(str)
    out["人気順位帯"] = pd.cut(out["人気"],
                            bins=POPULARITY_BINS, labels=POPULARITY_LABELS,
                            right=False).astype(str)
    out["距離帯"] = pd.cut(out["距離"],
                         bins=DISTANCE_BINS, labels=DISTANCE_LABELS,
                         right=False).astype(str)
    surface = out["芝ダ"].astype(str)
    out["芝ダ区分"] = np.where(surface.str.startswith("芝"), "芝",
                           np.where(surface.str.startswith("ダ"), "ダート", "その他"))
    past_count = out["同芝ダ回数c"].to_numpy(dtype=float)
    out["同馬場経験"] = np.select(
        (past_count == 0, (past_count > 0) & (past_count < 3), past_count >= 3),
        ("0走", "1–2走", "3走以上"), default="不明")
    last = out["前走着順率f"].to_numpy(dtype=float)
    out["前走着順位置"] = np.select(
        (last <= 0.25, (last > 0.25) & (last <= 0.5), last > 0.5),
        ("上位25%", "25–50%", "下位50%"), default="不明")
    ratio = np.asarray(candidate) / np.asarray(market)
    out["AI市場比"] = pd.cut(ratio, bins=MARKET_RATIO_BINS,
                           labels=MARKET_RATIO_LABELS,
                           right=False).astype(str)
    ensure(not (out[["単勝オッズ帯", "人気順位帯", "距離帯", "芝ダ区分",
                     "同馬場経験", "前走着順位置", "AI市場比"]] == "nan").any().any(),
           "unregistered group boundary")
    return out


def summarise(rows: pd.DataFrame) -> dict:
    """Race-cluster CI for equally weighted race means; horse-pooled Brier separate."""
    n = len(rows)
    ensure(n > 0, "empty research segment")
    deltas = rows.groupby("レースID", sort=False)["delta"].mean().to_numpy(float)
    interval = None
    if len(deltas) >= 30:
        rng = np.random.default_rng(SEED)
        # 600 resampled race means; only 1D within each subgroup.
        sample = rng.integers(0, len(deltas), size=(600, len(deltas)))
        m = deltas[sample].mean(axis=1)
        interval = [float(np.quantile(m, .025)),
                    float(np.quantile(m, .975))]
    return {
        "horseRows": int(n),
        "distinctRaces": int(len(deltas)),
        "top3ActualCount": int(rows["actual"].sum()),
        "top3ActualRate": float(rows["actual"].mean()),
        "marketExpectedRate": float(rows["pMarket"].mean()),
        "candidateExpectedRate": float(rows["pCandidate"].mean()),
        "marketBrier": float(rows["squaredMarket"].mean()),
        "candidateBrier": float(rows["squaredCandidate"].mean()),
        "candidateMinusMarketPooledBrier": float(rows["delta"].mean()),
        "candidateMinusMarketEqualRaceBrier": float(deltas.mean()),
        "equalRaceBootstrap95": interval,
        "adequateSample": bool(n >= 300 and len(deltas) >= 60),
    }


def segment_report(frame):
    dimensions = ("単勝オッズ帯", "人気順位帯", "芝ダ区分", "距離帯",
                  "同馬場経験", "前走着順位置", "AI市場比")
    out = {"ALL": summarise(frame)}
    for dim in dimensions:
        out[dim] = {
            str(group): summarise(chunk)
            for group, chunk in frame.groupby(dim, sort=True)
        }
    return out


def reliable_across_years(a, b):
    """Descriptive replication, not selective deployment or fresh holdout."""
    common = []
    for dim in a:
        if dim == "ALL":
            continue
        for name, x in a[dim].items():
            y = b.get(dim, {}).get(name)
            if not y or not x["adequateSample"] or not y["adequateSample"]:
                continue
            left = x["candidateMinusMarketPooledBrier"]
            right = y["candidateMinusMarketPooledBrier"]
            common.append({
                "dimension": dim, "group": name,
                "2025HorseRows": x["horseRows"],
                "2026HorseRows": y["horseRows"],
                "2025Delta": left, "2026Delta": right,
                "direction": ("candidate_better_both" if left < 0 and right < 0
                              else "market_better_both" if left > 0 and right > 0
                              else "mixed_direction"),
                "researchOnlyNotAnEligibilityRule": True,
            })
    return common


def case_examples(frame: pd.DataFrame, model: lgb.Booster, *,
                  max_per_outcome=3):
    """Opposing true/false-positive examples for interpretation, not fitting."""
    subset = frame.loc[(frame["単勝オッズ"] >= 30) &
                       (frame["AI市場比"].isin(("≥1.2x", "1.0–1.2x")))].copy()
    subset["uplift"] = subset["pCandidate"] - subset["pMarket"]
    examples = []
    for actual in (1, 0):
        chosen = subset.loc[subset["actual"] == actual].sort_values(
            ["uplift", "レースID", "馬番"], ascending=[False, True, True]
        ).head(max_per_outcome)
        if chosen.empty:
            continue
        contributions = np.asarray(
            model.predict(verify_features(chosen), pred_contrib=True,
                          validate_features=True), dtype=float)
        ensure(contributions.shape == (len(chosen), len(FEATURES) + 1) and
               np.isfinite(contributions).all(), "invalid factor contributions")
        for i, (_, r) in enumerate(chosen.iterrows()):
            positive = sorted(zip(FEATURES, contributions[i, :-1]),
                              key=lambda item: -item[1])[:4]
            negative = sorted(zip(FEATURES, contributions[i, :-1]),
                              key=lambda item: item[1])[:3]
            examples.append({
                "raceID": str(r["レースID"]),
                "horseNumber": int(r["馬番"]),
                "horseName": str(r["馬名"]),
                "closingWinOdds": float(r["単勝オッズ"]),
                "finalPopularityRank": int(r["人気"]),
                "finishPosition": int(r["着順"]),
                "actuallyTop3": bool(actual),
                "closingMarketTop3": float(r["pMarket"]),
                "candidateTop3": float(r["pCandidate"]),
                "candidateMinusMarket": float(r["uplift"]),
                "positiveFeatureContributionsToLogOdds": [
                    {"feature": str(f), "logOddsContribution": float(v)}
                    for f, v in positive
                ],
                "negativeFeatureContributionsToLogOdds": [
                    {"feature": str(f), "logOddsContribution": float(v)}
                    for f, v in negative
                ],
                "selectionUsesKnownOutcomeForIllustration": True,
            })
    return examples


def main():
    ensure(HISTORY.is_file() and MODEL_PATH.is_file() and METADATA.is_file(),
           "missing existing historical archive/model")
    meta = json.loads(METADATA.read_text(encoding="utf-8"))
    original = MODEL_PATH.read_bytes()
    verify_meta(meta, original)
    hist = pd.read_parquet(HISTORY)
    ensure(list(hist.columns) == BASE_COLS, "source history schema changed")
    training, validation, test25 = split_longshot(
        prepare_longshot(prepare_history(hist))
    )
    ensure((pd.to_datetime(test25["日付"]).dt.year == 2025).all(),
           "2025 holdout identity changed")
    provisional, rounds = fit(training, validation)
    ensure(rounds == meta["bestIterationsChosenWithout2025"],
           "unexpected 2024-selected tree count")
    weight = meta["selectedResidualMultiplier"]
    _, market25 = training_targets(test25)
    c25 = infer_without_results(test25, market25, provisional, weight)
    frame25 = partition(test25, c25, market25)

    # 2026 uses the PUBLISHED frozen <=2025 candidate, not the provisional
    # model, and never trains/tunes/chooses a filter using 2026 labels.
    recent = prepare_longshot(prepare_2026(hist))
    ensure((pd.to_datetime(recent["日付"]).dt.year == 2026).all(),
           "2026 period identity changed")
    final = lgb.Booster(model_file=str(MODEL_PATH))
    ensure(final.feature_name() == list(FEATURES),
           "frozen longshot candidate feature mismatch")
    _, market26 = training_targets(recent)
    c26 = infer_without_results(recent, market26, final, weight)
    frame26 = partition(recent, c26, market26)
    s25, s26 = segment_report(frame25), segment_report(frame26)
    replication = reliable_across_years(s25, s26)
    examples = case_examples(frame26, final)
    result = {
        "schemaVersion": 1, "version": VERSION,
        "generatedAtJST": datetime.now(JST).isoformat(),
        "researchOnly": True, "autoBetEnabled": False,
        "formalPredictionAIChanged": False, "candidatePromotionApproved": False,
        "status": "exploratory_retrospective_model_diagnostics",
        "modelVersion": meta["version"],
        "modelSha256": meta["modelSha256"],
        "sourceHistorySha256": hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "target": "Top3 on popularity>=6 and closing win odds>=10",
        "selection": {
            "2025ModelTrainThrough": "2023-12-31",
            "2024ValidationOnly": True,
            "2026ModelRefitThrough": "2025-12-31",
            "2026ModelHyperparametersFrozen": True,
            "selectedResidualMultiplier": weight,
            "binBoundariesFixedBeforeScoring": True,
            "subgroupsCannotChangeLiveEligibility": True,
        },
        "2025": {"races": int(test25["レースID"].nunique()),
                 "underdogHorseRows": len(test25),
                 "segments": s25},
        "2026": {"races": int(recent["レースID"].nunique()),
                 "underdogHorseRows": len(recent),
                 "segments": s26},
        "crossYearDescriptiveReplications": replication,
        "illustrative2026SurprisesAndFalsePositives": examples,
        "limitations": [
            "The historical win odds and popularity used for all bins are final odds, not genuine T-10.",
            "2025 and 2026 have been examined in this project; cross-year stable-looking groups are exploratory, not a new pristine test.",
            "Feature attribution shows association in the frozen model, not real-life causes.",
            "Examples are selected AFTER observing 2026 outcomes, for interpretation only.",
            "Every subgroup test is descriptive; no cutoff, eligibility policy, wager, or new model is selected using 2025/2026 results.",
            "No payout data or return on investment are measured here; lower Brier does not prove profitable betting.",
            "Formal and candidate first-frozen T-10 head-to-head comparisons must be evaluated prospectively.",
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                 allow_nan=False) + "\n", encoding="utf-8")
    lines = [
        "# 人気薄・3着以内予測：条件別の過去検証（研究専用）", "",
        "対象：6番人気以下・単勝10倍以上。2025年は2023年までの学習モデル、",
        "2026年は2025年末までの固定モデルを評価。最終オッズ基準でありT-10とは異なる。", "",
        "| 固定グループ | 2025年頭数 | 2025年差 | 2026年頭数 | 2026年差 | 同方向 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in replication:
        if item["direction"] == "mixed_direction":
            continue
        lines.append(
            f"| {item['dimension']} / {item['group']} | {item['2025HorseRows']} | "
            f"{item['2025Delta']:+.6f} | {item['2026HorseRows']} | "
            f"{item['2026Delta']:+.6f} | {item['direction']} |"
        )
    lines.extend(["", "差は候補Brier - 市場Brier。負の値は候補が良い。", "",
                  "## モデルが高く評価した馬：実際に好走／凡走した両方の例", ""])
    for item in examples:
        text = ("好走" if item["actuallyTop3"] else "凡走")
        factors = "、".join(f["feature"]
                           for f in item["positiveFeatureContributionsToLogOdds"][:3])
        lines.append(
            f"- {text}：{item['raceID']} {item['horseNumber']}番 {item['horseName']}、"
            f"{item['finalPopularityRank']}番人気・単勝{item['closingWinOdds']:.1f}倍、"
            f"実際{item['finishPosition']}着、市場3着内{item['closingMarketTop3']:.1%}→"
            f"AI{item['candidateTop3']:.1%}。正方向の主な寄与：{factors}"
        )
    lines.extend(["", "**注意**：要因の寄与は因果関係の証明ではない。",
                  "最終オッズを利用した探索結果であり、購入対象やモデル設定を自動変更しない。", ""])
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("LONGSHOT_FACTOR_RESEARCH_OK", json.dumps({
        "year2025": {"races": result["2025"]["races"],
                     "overall": s25["ALL"]["candidateMinusMarketPooledBrier"]},
        "year2026": {"races": result["2026"]["races"],
                     "overall": s26["ALL"]["candidateMinusMarketPooledBrier"]},
        "stableGoodGroups": [
            f"{r['dimension']}:{r['group']}" for r in replication
            if r["direction"] == "candidate_better_both"
        ],
        "stableWorseGroups": [
            f"{r['dimension']}:{r['group']}" for r in replication
            if r["direction"] == "market_better_both"
        ],
        "illustrations": len(examples),
    }, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
