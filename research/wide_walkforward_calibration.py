#!/usr/bin/env python3
"""Research-only walk-forward evaluation of wide-bet calibration. No live recommendations."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

BANDS = (0, 2, 5, 10, 100)
STRATEGIES = ("baseline_3", "calibrated_3", "calibrated_5")

def band(p):
    return next(i for i in range(4) if BANDS[i] <= p < BANDS[i + 1])

def load_races(root):
    review = json.loads((root / "AlignedPortfolioReview.json").read_text())
    if review.get("projectionState") != "verified" or review.get("aligned", {}).get("countsAreCurrent") is not True:
        raise ValueError("AlignedPortfolioReview verification gate failed")
    out = []
    for r in review["races"]:
        if r.get("resultState") != "matched":
            continue
        date = r["raceDate"].replace("-", "")
        path = root / "reports" / date / "portfolio_live10" / (r["raceID"] + ".json")
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        if doc["prediction"].get("captureQuality") != "formal_10min":
            continue
        horses = doc["prediction"]["horses"]
        finish = r.get("actualFinish", {})
        if not finish:
            continue
        pairs = doc["signal"]["wideEvaluation"].get("validationPairs", [])
        out.append((r, horses, pairs))
    return sorted(out, key=lambda x: (x[0]["raceDate"], x[0]["raceID"]))

def fit_calibration(races):
    counts = [[0, 0] for _ in range(4)]
    for r, _, pairs in races:
        finish = r["actualFinish"]
        for q in pairs:
            p = q["aiWideProbability"]
            i = band(p)
            hit = int(finish.get(str(q["number1"]), 999) <= 3 and finish.get(str(q["number2"]), 999) <= 3)
            counts[i][0] += hit
            counts[i][1] += 1
    # Conservative shrinkage to original probability within each band.
    # Prevents small-sample rates from becoming zero or one.
    return [(hits, n) for hits, n in counts]

def adjusted_probability(q, fit, strength=200):
    i = band(q["aiWideProbability"])
    hits, n = fit[i]
    prior = q["aiWideProbability"] / 100
    return (hits + strength * prior) / (n + strength)

def evaluate(races, fit, threshold=1.10, max_odds_age_minutes=30):
    totals = {name: dict(bets=0, stake=0, proxy_return=0, hits=0, races=0) for name in STRATEGIES}
    excluded_stale = 0
    for r, horses, pairs in races:
        axis = max(horses, key=lambda h: h["aiTop3Probability"])
        finish = r["actualFinish"]
        lookup = {(q["number1"], q["number2"]): q for q in pairs}
        candidates = []
        for h in horses:
            if h["number"] == axis["number"] or h["popularity"] < 6:
                continue
            key = tuple(sorted((axis["number"], h["number"])))
            q = lookup.get(key)
            if q is None or q.get("wideOddsLow", 0) <= 0:
                continue
            candidates.append((h, q))
        baseline = sorted(candidates, key=lambda hq: hq[0]["aiTop3Probability"] - hq[0]["marketTop3Probability"], reverse=True)[:3]
        ranked = sorted(candidates, key=lambda hq: adjusted_probability(hq[1], fit) * hq[1]["wideOddsLow"], reverse=True)
        calibrated = [hq for hq in ranked if adjusted_probability(hq[1], fit) * hq[1]["wideOddsLow"] >= threshold]
        choices = {"baseline_3": baseline, "calibrated_3": calibrated[:3], "calibrated_5": calibrated[:5]}
        # Freshness is a separate eligibility gate for calibrated strategies.
        meta = r.get("_odds_age_minutes")
        if meta is not None and meta > max_odds_age_minutes:
            choices["calibrated_3"] = []
            choices["calibrated_5"] = []
            excluded_stale += 1
        for name, tickets in choices.items():
            t = totals[name]
            t["races"] += 1
            t["bets"] += len(tickets)
            t["stake"] += 100 * len(tickets)
            for h, q in tickets:
                hit = finish.get(str(axis["number"]), 999) <= 3 and finish.get(str(h["number"]), 999) <= 3
                if hit:
                    t["hits"] += 1
                    t["proxy_return"] += round(100 * q["wideOddsLow"])
    for t in totals.values():
        t["proxy_roi_pct"] = round(100 * t["proxy_return"] / t["stake"], 2) if t["stake"] else None
    return totals, excluded_stale

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--min-train-days", type=int, default=2)
    args = ap.parse_args()
    races = load_races(args.root)
    dates = sorted({r[0]["raceDate"] for r in races})
    reports = []
    for i in range(args.min_train_days, len(dates)):
        train = [r for r in races if r[0]["raceDate"] < dates[i]]
        test = [r for r in races if r[0]["raceDate"] == dates[i]]
        if not train or not test:
            continue
        fit = fit_calibration(train)
        result, excluded = evaluate(test, fit)
        reports.append({"test_date": dates[i], "train_races": len(train), "test_races": len(test), "odds_age_exclusions": excluded, "strategies": result})
    print(json.dumps({"status": "research_only", "method": "walk_forward_no_future_labels", "payout": "pre_race_odds_low_proxy_not_settled", "days": reports}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
