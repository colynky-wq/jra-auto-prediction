"""Freeze the six-race reference on 2024; evaluate 2025 with daily replay."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from formation_runtime import FORMATION_METHOD_VERSION, MIN_COVERAGE, MIN_PAST_RACES, is_straight_race, relative_position

REPO = Path(__file__).resolve().parent.parent
HISTORY_COLUMNS = ["レースID", "日付", "馬番", "馬キー", "初角", "頭数", "障害", "芝ダ", "距離"]


def replay(history: pd.DataFrame) -> list[dict]:
    d = history.copy()
    d["日付"] = pd.to_datetime(d["日付"], errors="coerce").dt.normalize()
    d = d[d["日付"].notna() & (d["日付"].dt.year <= 2025)
          & ~d["障害"].fillna(False).astype(bool)].copy()
    straight = [is_straight_race(rid, surface, distance)
                for rid, surface, distance in d[["レースID", "芝ダ", "距離"]].itertuples(index=False, name=None)]
    d = d[~np.array(straight)].copy()
    if d.duplicated(["レースID", "馬番"]).any():
        raise ValueError("duplicate race/horse rows")
    d = d.sort_values(["日付", "レースID", "馬番"])
    windows = defaultdict(lambda: deque(maxlen=6))
    outputs = []
    for day, group in d.groupby("日付", sort=False):
        updates = []
        for rid, number, key, corner, count in group[["レースID", "馬番", "馬キー", "初角", "頭数"]].itertuples(index=False, name=None):
            window = windows[key] if pd.notna(key) else []
            actual = relative_position(corner, count)
            known = len(window) >= MIN_PAST_RACES
            if day.year in (2024, 2025):
                outputs.append({
                    "day": day.strftime("%Y-%m-%d"), "year": day.year, "raceID": rid,
                    "number": number, "fieldSize": count, "actual": actual,
                    "last": window[-1] if known else None,
                    "six": round(float(np.mean(window)), 6) if known else None,
                })
            if actual is not None and pd.notna(key):
                updates.append((key, actual))
        # No result from this day enters any prediction on the same day.
        for key, actual in updates:
            windows[key].append(actual)
    return outputs


def bootstrap_interval(daily: pd.DataFrame, value: str, count: str) -> list[float] | None:
    if daily.empty:
        return None
    rng = np.random.default_rng(20261012)
    indices = rng.integers(0, len(daily), size=(2000, len(daily)))
    numerators = daily[value].to_numpy()[indices].sum(axis=1)
    denominators = daily[count].to_numpy()[indices].sum(axis=1)
    values = numerators / denominators
    return [round(float(x), 6) for x in np.quantile(values, [0.025, 0.975])]


def evaluate(rows: list[dict], year: int, method: str = "six") -> dict:
    d = pd.DataFrame([row for row in rows if row["year"] == year])
    labeled = d[d["actual"].notna() & d["six"].notna()].copy()
    error = {name: float((labeled[name] - labeled["actual"]).abs().mean())
             for name in ("last", "six")}
    error["middle"] = float((0.5 - labeled["actual"]).abs().mean())
    labeled["delta"] = (labeled["last"] - labeled["actual"]).abs() - (labeled[method] - labeled["actual"]).abs()
    day_errors = labeled.groupby("day").agg(delta=("delta", "sum"), rows=("delta", "count"))
    races = []
    skipped_coverage = missing_leader = 0
    for rid, group in d.groupby("raceID", sort=False):
        count = int(group["fieldSize"].iloc[0])
        known = group[group[method].notna()].copy()
        if count < 3 or len(known) < 3 or len(known) / count < MIN_COVERAGE:
            skipped_coverage += 1
            continue
        leaders = set(group.loc[group["actual"] == 0, "number"])
        if not leaders:
            missing_leader += 1
            continue
        ordered = known.sort_values([method, "number"])["number"].tolist()
        last = known.sort_values(["last", "number"])["number"].tolist()
        leader_known = len(leaders.intersection(ordered))
        k = len(ordered)
        # Hypergeometric probability of >=1 leader among three random known horses.
        from math import comb
        random3 = 1 - comb(k - leader_known, 3) / comb(k, 3) if k >= 3 else 1.0
        pairs = correct = 0.0
        actual_known = known[known["actual"].notna()]
        pairs_data = list(actual_known[[method, "actual"]].itertuples(index=False, name=None))
        for i, (pred_a, actual_a) in enumerate(pairs_data):
            for pred_b, actual_b in pairs_data[i+1:]:
                if actual_a == actual_b:
                    continue
                pairs += 1
                correct += 0.5 if abs(pred_a - pred_b) < 1e-12 else float((pred_a < pred_b) == (actual_a < actual_b))
        hit3 = bool(leaders.intersection(ordered[:3]))
        races.append({"day": group["day"].iloc[0], "hit1": ordered[0] in leaders,
                      "hit3": hit3, "lastHit3": bool(leaders.intersection(last[:3])),
                      "randomHit1": leader_known / k, "randomHit3": random3,
                      "delta3": float(hit3) - random3, "pairs": pairs, "correct": correct})
    r = pd.DataFrame(races)
    daily = r.groupby("day").agg(delta=("delta3", "sum"), races=("delta3", "count")) if len(r) else pd.DataFrame()
    pct = lambda column: round(float(r[column].mean()) * 100, 2) if len(r) else None
    return {
        "year": year, "horseRows": len(d), "labeledHorseRowsWithThreePastRuns": len(labeled),
        "normalizedPositionMAE": {key: round(value, 6) for key, value in error.items()},
        "positionImprovementVsLast95CI": bootstrap_interval(day_errors, "delta", "rows"),
        "allRaces": int(d["raceID"].nunique()), "eligibleRaces": len(r),
        "skippedForCoverage": skipped_coverage, "skippedMissingLeader": missing_leader,
        "leaderTop1Pct": pct("hit1"), "leaderTop3Pct": pct("hit3"),
        "lastPositionLeaderTop3Pct": pct("lastHit3"),
        "randomKnownHorseLeaderTop1Pct": pct("randomHit1"), "randomKnownHorseLeaderTop3Pct": pct("randomHit3"),
        "leaderTop3ImprovementVsRandom95CI": bootstrap_interval(daily, "delta", "races"),
        "pairwiseOrderAgreementPct": round(float(r["correct"].sum() / r["pairs"].sum()) * 100, 2) if len(r) and r["pairs"].sum() else None,
        "evaluationDays": len(daily),
    }


def run(history: pd.DataFrame) -> dict:
    rows = replay(history)
    development = evaluate(rows, 2024)
    # Fixed, existing six-race window. This decision uses 2024 only.
    accepted = development["normalizedPositionMAE"]["six"] < development["normalizedPositionMAE"]["last"]
    return {
        "schemaVersion": 1, "methodVersion": FORMATION_METHOD_VERSION,
        "status": "research_reference_only" if accepted else "do_not_publish",
        "fixedMethod": "mean of normalized first-corner positions in last six valid past flat races",
        "minimumPastRaces": MIN_PAST_RACES, "minimumCoveragePct": MIN_COVERAGE * 100,
        "development2024": development, "evaluation2025": evaluate(rows, 2025),
        "paceForecastValidated": False, "stakeSizingEnabled": False, "formalSignalsChanged": False,
        "limitations": [
            "2025 is evaluated after the method is accepted using 2024; 2026 is excluded.",
            "All runners on a day are predicted before that day's results are added.",
            "Top-three includes ties and unknown leaders count as misses; random baseline uses the same known-horse pool.",
            "Initial-corner order is a rough reference, not the full race formation or a leader probability.",
            "This reference evaluates first-corner order only; early-lap pace forecasts require separate validation.",
            "Straight races, debut-heavy fields and fields below 80% coverage are not given a race-wide reference.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, default=REPO / "production/production_history.parquet")
    parser.add_argument("--output", type=Path, default=REPO / "reports/FormationValidation.json")
    args = parser.parse_args()
    data = pd.read_parquet(args.history, columns=HISTORY_COLUMNS)
    result = run(data)
    result["historySHA256"] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
