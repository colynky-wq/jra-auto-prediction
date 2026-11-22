"""Validate a fixed fourth-reserve rule against equal-size, same-day alternatives."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from formal_portfolio_capture import stamp
from inspect_frozen_win_probabilities import prob
from research_conditional_longshot import past_races, race_features
from research_longshot_season_venue import date_interval
from research_longshot_t10 import ROOT, git_history, load_audited
from research_longshot_t10_reserve import coverage_gain
from topk_runtime import market_top2_top3
from train_longshot_top3 import HISTORY, POPRANK_MIN, WINODDS_MIN

OUT = ROOT / "research/FourthReserveGateValidation.json"
REFERENCE = ROOT / "research/LongshotT10ReserveReview.json"
METHODS = ("jointCoverage", "fourthTop3", "smallThirdFourthGap", "marketChaos")
FORWARD_START_JST = "2026-11-24T00:00:00+09:00"
HISTORY_COLUMNS = ["レースID", "日付", "馬番", "着順", "単勝オッズ", "人気", "頭数", "障害"]


def race_row(rid, date, podium, numbers, odds, ranked, place, market_risk):
    main, third, fourth = set(ranked[:3]), ranked[2], ranked[3]
    inv = 1 / np.asarray(odds, dtype=float)
    weights = dict(zip(numbers, inv / inv.sum(), strict=True))
    hits = len(main & podium)
    return {
        "raceID": rid, "日付": pd.Timestamp(date),
        "jointCoverage": coverage_gain(weights, main, fourth),
        "fourthTop3": place[fourth],
        "smallThirdFourthGap": place[fourth] - place[third],
        "marketChaos": market_risk,
        "fourthPlaced": fourth in podium,
        "newlyCovered": fourth in podium and hits == 2,
        "mainCoversAll": hits == 3,
    }


def historical(history):
    evidence = past_races(history).set_index("raceID")
    rows = []
    for rid, field in history.loc[history["単勝オッズ"].notna()].groupby("レースID", sort=False):
        if rid not in evidence.index:
            continue
        base = evidence.loc[rid]
        odds, numbers = field["単勝オッズ"].to_numpy(float), field["馬番"].to_numpy(int)
        if len(numbers) != base["fieldSize"] or len(numbers) < 4:
            raise ValueError("inconsistent historical field")
        ranked = tuple(numbers[np.lexsort((numbers, odds))])
        _, place = market_top2_top3(1 / odds)
        rows.append(race_row(rid, base["date"], base["podium"], numbers, odds,
                             ranked, dict(zip(numbers, place, strict=True)), base["marketEvent"]))
    return pd.DataFrame(rows)


def live(races, field):
    rows = []
    for race, podium in races:
        horses = race["horses"]
        odds = [h["odds"] for h in horses]
        numbers = [h["number"] for h in horses]
        mask = np.asarray([h["popularity"] >= POPRANK_MIN and h["odds"] >= WINODDS_MIN for h in horses])
        if len(horses) < 4 or not mask.any():
            continue
        place = {h["number"]: prob(h[field], field) for h in horses}
        ranked = tuple(sorted(numbers, key=lambda n: (-place[n], n)))
        rows.append(race_row(race["raceID"], race["raceDate"], podium, numbers, odds,
                             ranked, place, race_features(odds, mask)["marketEvent"]))
    return pd.DataFrame(rows)


def selection_metrics(frame, selected):
    y = frame.loc[selected]
    added = len(y)
    newly_covered = int(y["newlyCovered"].sum())
    return {"adds": added, "fourthPlaced": int(y["fourthPlaced"].sum()),
            "newlyCovered": newly_covered, "coverageGainPerAdd": newly_covered / added if added else None,
            "finalCoverage": int(frame["mainCoversAll"].sum()) + newly_covered}


def rank_at_daily_budget(frame, method, daily_budget):
    ranked = frame.sort_values(["日付", method, "raceID"], ascending=[True, False, True])
    within_day = ranked.groupby("日付").cumcount()
    return within_day.lt(ranked["日付"].map(daily_budget)).reindex(frame.index)


def analyze(frame, thresholds, sensitivity=None):
    if frame.empty:
        return {"races": 0}
    dates = frame["日付"]
    fixed = {method: selection_metrics(frame, frame[method].ge(thresholds[method])) for method in METHODS}
    joint_selected = frame["jointCoverage"].ge(thresholds["jointCoverage"])
    budget = joint_selected.groupby(dates).sum()
    complete = frame["newlyCovered"].astype(int)
    matched = {"jointCoverage": fixed["jointCoverage"]}
    for method in METHODS[1:]:
        selected = rank_at_daily_budget(frame, method, budget)
        if not selected.groupby(dates).sum().equals(budget.astype(int)):
            raise ValueError("unequal budgets by date")
        comparison = selection_metrics(frame, selected)
        deltas = complete * (joint_selected.astype(int) - selected.astype(int))
        comparison["deltaJointMinusComparator"] = int(deltas.sum())
        comparison["deltaPerRace95ByDate"] = date_interval(frame, deltas.to_numpy())
        matched[method] = comparison

    random_allocation = float((budget * complete.groupby(dates).mean()).sum())
    outcomes = frame["newlyCovered"].to_numpy(dtype=bool)
    positive = int(outcomes.sum())
    ranks = frame["jointCoverage"].rank().to_numpy()
    auc = (ranks[outcomes].sum() - positive * (positive + 1) / 2) / (positive * (len(frame) - positive)) \
        if 0 < positive < len(frame) else None
    band_info = {}
    if sensitivity is not None:
        for label, floor in sensitivity.items():
            selected = frame["jointCoverage"].ge(floor)
            band_info[label] = {"floor": floor, **selection_metrics(frame, selected)}
    return {
        "races": len(frame), "raceDates": dates.nunique(), "from": dates.min().date().isoformat(),
        "through": dates.max().date().isoformat(), "mainCoversAll": int(frame["mainCoversAll"].sum()),
        "allFourthCoverage": selection_metrics(frame, pd.Series(True, index=frame.index)),
        "marketJointScoreAUC": float(auc) if auc is not None else None,
        "marketJointScoreBrier": float(np.mean((frame["jointCoverage"] - complete) ** 2)),
        "fixedThresholds": fixed, "matchedDailyAddCounts": matched,
        "sameDayRandomExpectedNewCoverage": random_allocation,
        "dailyJointRule": {date.date().isoformat(): {
            "races": len(day), **selection_metrics(day, joint_selected.reindex(day.index)),
        } for date, day in frame.groupby("日付")},
        "jointThresholdSensitivity": band_info,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=ROOT)
    parser.add_argument("--history-archive", metavar="GIT_REF")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    raw = (args.audit_root / "AlignedPortfolioReview.json").read_bytes()
    audit = json.loads(raw)
    races, missing_podium = load_audited(args.audit_root, audit)
    pre, forward = [], []
    for race, podium in races:
        cohort = forward if stamp(race["capturedAtJST"]) >= stamp(FORWARD_START_JST) else pre
        cohort.append((race, podium))

    if args.history_archive:
        history, history_source = git_history(args.history_archive)
        history = history.loc[:, HISTORY_COLUMNS].copy()
    else:
        history = pd.read_parquet(HISTORY, columns=HISTORY_COLUMNS)
        history_source = {"sha256": hashlib.sha256(HISTORY.read_bytes()).hexdigest()}
    history["日付"] = pd.to_datetime(history["日付"])
    history = history.loc[history["日付"].ge("2025-01-01") & history["日付"].lt(FORWARD_START_JST[:10])]
    past = historical(history)
    train = past.loc[past["日付"].lt("2026-01-01")]
    jan_jun = past.loc[past["日付"].ge("2026-01-01") & past["日付"].lt("2026-07-01")]
    jul_oct = past.loc[past["日付"].ge("2026-07-01") & past["日付"].lt("2026-11-01")]
    audit_ids = {row["raceID"] for row in audit["races"]}
    november_distinct = past.loc[past["日付"].ge("2026-11-01") & ~past["raceID"].isin(audit_ids)]
    thresholds = {method: float(train[method].quantile(2 / 3)) for method in METHODS}
    registered = json.loads(REFERENCE.read_bytes())["thresholdsFrom2025Only"]["fourthGain"]
    if not np.isclose(thresholds["jointCoverage"], registered, rtol=0, atol=1e-12):
        raise ValueError("previously registered fourth-reserve threshold changed")
    thresholds["jointCoverage"] = registered
    sensitivity = {str(q): float(train["jointCoverage"].quantile(q)) for q in (.5, .80, .90)}
    ai_field, market_field = "aiTop3Probability", "marketTop3Probability"
    report = {
        "auditSha256": hashlib.sha256(raw).hexdigest(), "auditGeneratedAtJST": audit["generatedAtJST"],
        "matchedAuditedRaces": audit["aligned"]["matchedResultRaces"], "missingPodiumRaceIDs": missing_podium,
        "historicalSource": history_source,
        "thresholdsFitOn2025Races": len(train), "fixedThresholds": thresholds,
        "finalOdds2026JanJunMarketOrder": analyze(jan_jun, thresholds, sensitivity),
        "finalOdds2026JulOctMarketOrder": analyze(jul_oct, thresholds, sensitivity),
        "finalOddsNovDistinctFromAuditMarketOrder": analyze(november_distinct, thresholds, sensitivity),
        "priorT10FormalAIOrder": analyze(live(pre, ai_field), thresholds, sensitivity),
        "priorT10MarketOrder": analyze(live(pre, market_field), thresholds, sensitivity),
        "forwardStartsAtJST": FORWARD_START_JST,
        "forwardT10FormalAIOrder": analyze(live(forward, ai_field), thresholds),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Written", args.output, flush=True)


if __name__ == "__main__":
    main()
