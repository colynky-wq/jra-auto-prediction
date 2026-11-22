"""Compare ways to allocate one extra prediction using audited T-10 archives."""
from __future__ import annotations

from collections import Counter
import hashlib
from itertools import combinations, permutations
import json

import numpy as np
import pandas as pd

from inspect_frozen_win_probabilities import prob
from research_conditional_longshot import past_races, race_features
from research_longshot_season_venue import date_interval
from research_longshot_t10 import ROOT, load_audited
from topk_runtime import market_top2_top3
from train_longshot_top3 import HISTORY, POPRANK_MIN, WINODDS_MIN

OUT = ROOT / "research/LongshotT10ReserveReview.json"
POLICIES = ("ordinaryFourth", "marketLongshot", "aiLongshot")


def coverage_gain(weights, main, extra):
    """Market probability that this extra horse completes an otherwise missed podium."""
    value = 0.
    for pair in combinations(main, 2):
        for first, second, third in permutations((*pair, extra)):
            a, b, c = weights[first], weights[second], weights[third]
            value += a * b * c / ((1 - a) * (1 - a - b))
    return float(value)


def inputs(rid, date, podium, odds, numbers, longshots, base_order, market_order, risk, market_top3):
    main = frozenset(base_order[:3])
    remaining = longshots - main
    fourth = base_order[3]
    candidate = {
        "ordinaryFourth": fourth,
        "marketLongshot": next((n for n in market_order if n in remaining), None),
        "aiLongshot": next((n for n in base_order if n in remaining), None),
    }
    inv = 1 / np.asarray(odds, dtype=float)
    weights = dict(zip(numbers, inv / inv.sum(), strict=True))
    return {"raceID": rid, "日付": pd.Timestamp(date), "marketRisk": risk, "upset": bool(podium & longshots),
            "longshotProbability": market_top3.get(candidate["marketLongshot"], 0.),
            "fourthGain": coverage_gain(weights, main, fourth), "podium": podium,
            "main": main, "candidate": candidate}


def historical(history):
    evidence = past_races(history).set_index("raceID")
    active = history.loc[history["単勝オッズ"].notna()]
    rows = []
    for rid, field in active.groupby("レースID", sort=False):
        if rid not in evidence.index:
            continue
        row = evidence.loc[rid]
        odds, numbers = field["単勝オッズ"].to_numpy(float), field["馬番"].to_numpy(int)
        if len(numbers) != row["fieldSize"] or len(numbers) < 4:
            raise ValueError("inconsistent historical field")
        order = tuple(numbers[np.lexsort((numbers, odds))])
        _, place = market_top2_top3(1 / odds)
        rows.append(inputs(rid, row["date"], row["podium"], odds, numbers,
                           row["longshotNumbers"], order, order, float(row["marketEvent"]),
                           dict(zip(numbers, place, strict=True))))
    return pd.DataFrame(rows)


def live(races):
    rows = []
    omitted = []
    for race, podium in races:
        horses = race["horses"]
        rid = race["raceID"]
        odds = [h["odds"] for h in horses]
        numbers = [h["number"] for h in horses]
        mask = np.array([h["popularity"] >= POPRANK_MIN and h["odds"] >= WINODDS_MIN for h in horses])
        if len(horses) < 4 or not mask.any():
            omitted.append(rid)
            continue
        longshots = frozenset(n for n, is_longshot in zip(numbers, mask, strict=True) if is_longshot)
        orders = []
        for key in ("aiTop3Probability", "marketTop3Probability"):
            ranked = sorted(horses, key=lambda h: (-prob(h[key], key), h["number"]))
            orders.append(tuple(h["number"] for h in ranked))
        market_top3 = {h["number"]: prob(h["marketTop3Probability"], "market Top3") for h in horses}
        rows.append(inputs(rid, race["raceDate"], podium, odds, numbers, longshots,
                           *orders, race_features(odds, mask)["marketEvent"], market_top3))
    return pd.DataFrame(rows), omitted


def event_summary(frame):
    y = frame["upset"].to_numpy()
    n = len(y)
    positive = int(y.sum())
    p = frame["marketRisk"]
    ranks = p.rank(method="average").to_numpy()
    denominator = positive * (n - positive)
    return {
        "races": n, "events": positive, "marketBrier": float(np.mean((p.to_numpy() - y) ** 2)),
        "constantRateBaselineBrier": float(np.mean((y.mean() - y) ** 2)),
        "marketAUC": float((ranks[y].sum() - positive * (positive + 1) / 2) / denominator) if denominator else None,
        "meanForecast": float(p.mean()),
    }


def evaluate(frame, mask):
    eligible = frame.loc[mask].copy()
    policies = {p: Counter(added=0, placed=0, completed=0) for p in POLICIES}
    # Record the same pair in each race for fair comparisons and date-level uncertainty.
    deltas = {p: {"placed": [], "completed": []} for p in POLICIES[1:]}
    baseline_complete = sum(row.main == row.podium for row in frame.itertuples())
    for row in eligible.itertuples():
        if any(row.candidate[p] is None for p in POLICIES):
            raise ValueError("common reserve policy requires a longshot outside the main three")
        actual = {}
        for p in POLICIES:
            extra = row.candidate[p]
            placed = extra in row.podium
            completed = placed and len(row.main & row.podium) == 2
            policies[p]["added"] += 1
            policies[p]["placed"] += placed
            policies[p]["completed"] += completed
            actual[p] = (placed, completed)
        for p in POLICIES[1:]:
            for i, key in enumerate(("placed", "completed")):
                deltas[p][key].append(int(actual[p][i]) - int(actual["ordinaryFourth"][i]))
    for counts in policies.values():
        counts["allThreeCovered"] = baseline_complete + counts["completed"]
    paired = {
        p: {key: {"net": sum(values), "betterRaces": values.count(1), "worseRaces": values.count(-1),
                  "perRaceDeltaDateBootstrap95": date_interval(eligible, np.asarray(values))}
            for key, values in events.items()} for p, events in deltas.items()
    }
    whole = frame.assign(selected=mask)
    # Matched budget against a date-stratified uniformly random decision to add #4.
    matched_budget, matched_longshot = 0., 0.
    for _, day in whole.groupby("日付"):
        if not day["selected"].any():
            continue
        common = [r for r in day.itertuples() if r.candidate["marketLongshot"] is not None]
        n_complete = sum(r.candidate["ordinaryFourth"] in r.podium and len(r.main & r.podium) == 2
                         for r in common)
        n_longshot = sum(r.candidate["marketLongshot"] in r.podium for r in common)
        share = int(day["selected"].sum()) / len(common)
        matched_budget += share * n_complete
        matched_longshot += share * n_longshot
    return {
        "addedRaces": len(eligible), "actualLongshotEventOnAddedRaces": int(eligible["upset"].sum()),
        "mainOnlyAllThreeCovered": baseline_complete, "policies": policies, "pairedVsOrdinary": paired,
        "matchedDailyBudgetRandomFourthExpectedCompletions": float(matched_budget),
        "matchedDailyBudgetRandomLongshotExpectedPlaced": float(matched_longshot),
    }


def analyze(frame, thresholds):
    can_add = frame["candidate"].map(lambda c: c["marketLongshot"] is not None)
    high = frame["marketRisk"].ge(thresholds["marketRisk"])
    out = {"event": event_summary(frame),
           "eventHighRisk": event_summary(frame.loc[high]) if high.any() else None,
           "eventOther": event_summary(frame.loc[~high]) if (~high).any() else None,
           "racesWithLongshotOutsideMainThree": int(can_add.sum()), "gates": {}}
    for label, active in (("allEligible", can_add),
                          ("highMarketRisk", can_add & high),
                          ("highLongshotProbability", can_add & frame["longshotProbability"].ge(thresholds["longshotProbability"])),
                          ("highFourthCoverageGain", can_add & frame["fourthGain"].ge(thresholds["fourthGain"]))):
        out["gates"][label] = evaluate(frame, active)
    return out


def main():
    raw = (ROOT / "AlignedPortfolioReview.json").read_bytes()
    audit = json.loads(raw)
    races, missing_podium = load_audited(ROOT, audit)
    t10, omitted = live(races)
    history = pd.read_parquet(HISTORY, columns=["レースID", "日付", "馬番", "着順", "単勝オッズ", "人気", "頭数", "障害"])
    history["日付"] = pd.to_datetime(history["日付"])
    history = history.loc[history["日付"].ge("2025-01-01") & history["日付"].lt("2026-11-01")]
    past = historical(history)
    train = past.loc[past["日付"].lt("2026-01-01")]
    validate = past.loc[past["日付"].ge("2026-01-01")]
    threshold = {c: float(train[c].quantile(2 / 3)) for c in ("marketRisk", "longshotProbability", "fourthGain")}
    result = {
        "auditSha256": hashlib.sha256(raw).hexdigest(), "auditGeneratedAtJST": audit["generatedAtJST"],
        "historySha256": hashlib.sha256(HISTORY.read_bytes()).hexdigest(),
        "trainingRaces": len(train), "thresholdsFrom2025Only": threshold,
        "matchedT10Races": audit["aligned"]["matchedResultRaces"], "missingPodiumRaceIDs": missing_podium,
        "ineligibleT10RaceIDs": omitted, "T10Days": sorted(t10["日付"].dt.strftime("%Y-%m-%d").unique()),
        "t10Base": "saved formal AI top 3", "historicalBase": "final win-market top 3",
        "t10": analyze(t10, threshold), "2026JanToOctFinalOdds": analyze(validate, threshold),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Written", OUT, flush=True)


if __name__ == "__main__":
    main()
