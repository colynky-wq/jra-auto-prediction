"""Freeze 2024 course references; report 2025 coverage and clock errors."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from collect_race_laps import write_json
from pace_baseline_runtime import METHOD_VERSION, MIN_SAMPLES, build_baselines, classify_actual, condition_key, eligible_record
from research_formation import bootstrap_interval

REPO = Path(__file__).resolve().parent.parent


def run(records: list[dict], expected: dict[int, int]) -> tuple[dict, dict]:
    baselines = build_baselines(records)
    coarse = defaultdict(list)
    training = [r for r in records if r["raceDate"].startswith("2024-") and eligible_record(r)]
    for record in training:
        coarse[(record["surface"], record["distanceM"], record["earlyRecordedDistanceM"])].append(record["earlyRecordedTimeSeconds"])
    coarse_medians = {k: float(np.median(values)) for k, values in coarse.items()}
    reference_map = {tuple(r["condition"]): r for r in baselines}
    evaluation = [r for r in records if r["raceDate"].startswith("2025-")]
    measured = []
    for record in evaluation:
        label = classify_actual(record, baselines)
        if label is None:
            continue
        value = record["earlyRecordedTimeSeconds"]
        median = reference_map[condition_key(record)]["medianSeconds"]
        combined = coarse_medians[(record["surface"], record["distanceM"], record["earlyRecordedDistanceM"])]
        measured.append({"day": record["raceDate"], "label": label,
                         "courseError": abs(median-value), "coarseError": abs(combined-value),
                         "delta": abs(combined-value)-abs(median-value)})
    coverage = len(measured)/expected.get(2025, 1)
    counts = {year: sum(r["raceDate"].startswith(str(year)+"-") for r in records) for year in [2024, 2025]}
    complete = all(counts[y] == expected[y] for y in [2024, 2025])
    daily = pd.DataFrame(measured).groupby("day").agg(delta=("delta", "sum"), races=("delta", "count")) if measured else pd.DataFrame()
    report = {"schemaVersion": 1, "methodVersion": METHOD_VERSION,
              "status": "course_reference_only" if complete else "incomplete_data",
              "trainingYear": 2024, "evaluationYear": 2025, "minimumSamples": MIN_SAMPLES,
              "collectionByYear": {str(y): {"expected": expected[y], "stored": counts[y]} for y in [2024, 2025]},
              "trainingEligibleRaces": len(training), "conditionGroups": len(baselines),
              "evaluationRacesWithReference": len(measured), "evaluationCoveragePct": round(coverage*100, 2),
              "observed2025Labels": dict(Counter(r["label"] for r in measured)),
              "meanAbsoluteErrorSeconds": {
                  "courseAndGoingMedian": round(float(np.mean([r["courseError"] for r in measured])), 4) if measured else None,
                  "surfaceAndDistanceMedian": round(float(np.mean([r["coarseError"] for r in measured])), 4) if measured else None},
              "courseMedianImprovementSeconds95CI": bootstrap_interval(daily, "delta", "races"),
              "paceForecastEnabled": False, "stakeSizingEnabled": False, "formalSignalsChanged": False,
              "meaning": "2024年の同条件30レース以上で前半タイムの基準を固定し、2025年で基準の誤差と適用範囲を確認。速い/中間/遅いは基準の25/75%点による独自の実測分類。出走馬の組み合わせによる発走前のペース予想の的中率ではない。",
              "limitations": ["仮柵A/B/C、クラス、風、季節を補正していない。", "条件別30件未満では保留し、違う馬場や距離の基準を流用しない。", "100m/150mから始まる距離は700m/750m地点の実測で別条件として扱う。", "2025年と2026年で条件や閾値を選ばず、2026年は評価に使わない。"]}
    artifact = {"schemaVersion": 1, "methodVersion": METHOD_VERSION, "trainingYear": 2024,
                "status": report["status"], "minimumSamples": MIN_SAMPLES,
                "conditionFields": ["venue", "surface", "distanceM", "courseDetail", "going", "measuredEarlyDistanceM"],
                "paceForecastEnabled": False, "stakeSizingEnabled": False, "baselines": baselines}
    return artifact, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lap-root", type=Path, default=REPO / "research/laps")
    parser.add_argument("--history", type=Path, default=REPO / "production/production_history.parquet")
    args = parser.parse_args()
    records = []
    for path in sorted(args.lap_root.glob("*/RaceLapData.json")):
        records.extend(json.loads(path.read_text())["races"])
    history = pd.read_parquet(args.history, columns=["レースID", "日付", "障害"])
    history = history[~history["障害"].fillna(False).astype(bool)].drop_duplicates("レースID")
    counts = pd.to_datetime(history["日付"]).dt.year.value_counts()
    artifact, report = run(records, {year: int(counts[year]) for year in [2024, 2025]})
    fingerprint = hashlib.sha256(json.dumps(sorted(records, key=lambda r:r["raceID"]), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report["lapDataSHA256"] = fingerprint
    report["historySHA256"] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    artifact["lapDataSHA256"] = fingerprint
    write_json(args.lap_root / "PaceBaselines2024.json", artifact)
    write_json(REPO / "reports/PaceBaselineValidation.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
