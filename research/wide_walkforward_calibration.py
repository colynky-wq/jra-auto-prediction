#!/usr/bin/env python3
"""Research-only walk-forward evaluation; never changes live betting or recommendations.

All strategies share the same odds freshness gate for a fair comparison.
Historical payouts use saved pre-race wide odds LOW, not settled dividends.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

BANDS = (0, 2, 5, 10, 101)
STRATEGIES = ("baseline_3", "calibrated_3", "calibrated_5", "calibrated_floor5_3")


def band(probability_pct):
    if not (0 <= probability_pct <= 100):
        raise ValueError("AI wide probability out of range")
    return next(i for i in range(4) if BANDS[i] <= probability_pct < BANDS[i + 1])


def odds_age_minutes(wide_evaluation):
    """Age of the official wide odds *at capture time*, both in JST."""
    try:
        source = datetime.strptime(wide_evaluation["officialDatetime"], "%Y-%m-%d %H:%M:%S")
        captured = datetime.strptime(wide_evaluation["capturedAtJST"], "%Y-%m-%d %H:%M:%S")
        age = (captured - source).total_seconds() / 60
        return age if 0 <= age <= 24 * 60 else None
    except (ValueError, TypeError, KeyError):
        return None


def load_races(root):
    review = json.loads((root / "AlignedPortfolioReview.json").read_text())
    if review.get("projectionState") != "verified" or review.get("aligned", {}).get("countsAreCurrent") is not True:
        raise ValueError("AlignedPortfolioReview verification gate failed")
    out = []
    for original in review["races"]:
        if original.get("resultState") != "matched" or not original.get("actualFinish"):
            continue
        date = original["raceDate"].replace("-", "")
        path = root / "reports" / date / "portfolio_live10" / (original["raceID"] + ".json")
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        if doc["prediction"].get("captureQuality") != "formal_10min":
            continue
        wide = doc["signal"].get("wideEvaluation", {})
        # Never treat absent or invalid timing evidence as fresh.
        race = dict(original, _odds_age_minutes=odds_age_minutes(wide))
        out.append((race, doc["prediction"]["horses"], wide.get("validationPairs", [])))
    return sorted(out, key=lambda row: (row[0]["raceDate"], row[0]["raceID"]))


def eligible(race, max_age):
    age = race.get("_odds_age_minutes")
    return age is not None and (max_age is None or age <= max_age)


def is_top3(finish, horse_number):
    """Non-finishers, excluded horses and missing/NULL results are not top three."""
    position = finish.get(str(horse_number))
    return type(position) is int and 1 <= position <= 3


def fit_calibration(train_races):
    """Band-wise multiplicative calibration with shrinkage toward no adjustment.

    Use only strictly preceding dates. All pair outcomes in a race are correlated;
    treat this only as a research ranking correction, not a confidence interval.
    """
    counts = [[0.0, 0.0, 0] for _ in range(4)]  # hits, expected hits, pair count
    for race, _, pairs in train_races:
        finish = race["actualFinish"]
        for pair in pairs:
            p = pair["aiWideProbability"]
            i = band(p)
            hit = is_top3(finish, pair["number1"]) and is_top3(finish, pair["number2"])
            counts[i][0] += int(hit)
            counts[i][1] += p / 100
            counts[i][2] += 1
    return counts


def adjusted_probability(pair, fit, strength=200):
    p = pair["aiWideProbability"] / 100
    hits, expected, count = fit[band(pair["aiWideProbability"])]
    if not count:
        return p
    avg_prior = expected / count
    shrink = strength * avg_prior
    ratio = (hits + shrink) / (expected + shrink) if expected + shrink else 1
    return min(1.0, max(0.0, p * ratio))


def select_tickets(horses, pairs, fit, threshold=1.10):
    axis = max(horses, key=lambda horse: horse["aiTop3Probability"])
    lookup = {(pair["number1"], pair["number2"]): pair for pair in pairs}
    candidates = []
    for horse in horses:
        if horse["number"] == axis["number"] or horse["popularity"] < 6:
            continue
        key = tuple(sorted((axis["number"], horse["number"])))
        pair = lookup.get(key)
        if pair is None or pair.get("wideOddsLow", 0) <= 0:
            continue
        candidates.append((horse, pair))
    baseline = sorted(
        candidates,
        key=lambda item: item[0]["aiTop3Probability"] - item[0]["marketTop3Probability"],
        reverse=True,
    )[:3]
    ranked = sorted(candidates, key=lambda item: adjusted_probability(item[1], fit) * item[1]["wideOddsLow"], reverse=True)
    value = [item for item in ranked if adjusted_probability(item[1], fit) * item[1]["wideOddsLow"] >= threshold]
    return axis, {
        "baseline_3": baseline,
        "calibrated_3": value[:3],
        "calibrated_5": value[:5],
        "calibrated_floor5_3": [item for item in value if item[1]["aiWideProbability"] >= 5][:3],
    }


def evaluate(races, fit, max_age, threshold):
    totals = {name: dict(races=0, bets=0, stake=0, hits=0, proxy_return=0) for name in STRATEGIES}
    omitted = {"missing_or_invalid_age": 0, "stale": 0}
    for race, horses, pairs in races:
        age = race.get("_odds_age_minutes")
        if age is None:
            omitted["missing_or_invalid_age"] += 1
            continue
        if max_age is not None and age > max_age:
            omitted["stale"] += 1
            continue
        axis, choices = select_tickets(horses, pairs, fit, threshold)
        finish = race["actualFinish"]
        for name, tickets in choices.items():
            total = totals[name]
            total["races"] += 1
            total["bets"] += len(tickets)
            total["stake"] += 100 * len(tickets)
            for horse, pair in tickets:
                if is_top3(finish, axis["number"]) and is_top3(finish, horse["number"]):
                    total["hits"] += 1
                    total["proxy_return"] += round(pair["wideOddsLow"] * 100)
    for total in totals.values():
        total["proxy_roi_pct"] = round(total["proxy_return"] / total["stake"] * 100, 2) if total["stake"] else None
    return totals, omitted


def run_walkforward(races, min_train_days=2, cutoffs=(30, 60, None), threshold=1.10):
    dates = sorted({race["raceDate"] for race, _, _ in races})
    reports = []
    for max_age in cutoffs:
        days = []
        summary = {name: dict(races=0, bets=0, stake=0, hits=0, proxy_return=0) for name in STRATEGIES}
        for i in range(min_train_days, len(dates)):
            test_date = dates[i]
            train = [row for row in races if row[0]["raceDate"] < test_date and eligible(row[0], max_age)]
            test = [row for row in races if row[0]["raceDate"] == test_date]
            if not test:
                continue
            totals, omitted = evaluate(test, fit_calibration(train), max_age, threshold)
            for name, row in totals.items():
                for field in ("races", "bets", "stake", "hits", "proxy_return"):
                    summary[name][field] += row[field]
            days.append({"test_date": test_date, "training_races": len(train), "loaded_test_races": len(test),
                         "omitted": omitted, "strategies": totals})
        for row in summary.values():
            row["proxy_roi_pct"] = round(row["proxy_return"] / row["stake"] * 100, 2) if row["stake"] else None
        reports.append({"max_odds_age_minutes": max_age, "days": days, "summary": summary})
    return {"status": "research_only", "capture_count": len(races), "train_days_excluded": min_train_days,
            "method": "past_days_only; same age gate for training and all compared strategies",
            "payout": "pre-race saved odds LOW proxy, not actual dividends",
            "cutoffs": reports}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--min-train-days", type=int, default=2)
    ap.add_argument("--threshold", type=float, default=1.10)
    args = ap.parse_args()
    print(json.dumps(run_walkforward(load_races(args.root), args.min_train_days, threshold=args.threshold),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
