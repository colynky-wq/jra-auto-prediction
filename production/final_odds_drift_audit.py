"""Read-only diagnostic of frozen T-10 win odds vs published final win odds.

The JRA final odds are **post hoc** data. They are NEVER eligible as T-10
market prices, prediction inputs, verified pre-race performance or real-time
expected-value estimates. This audit only quantifies observed market movement.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median

from odds_integrity_audit import read_json


def positive_number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) and x > 0 else None
    except (TypeError, ValueError):
        return None


def compare_race(record, outcome):
    prediction = record.get("prediction") or {}
    rid = prediction.get("raceID")
    if outcome.get("raceID") != rid:
        raise ValueError("race identity mismatch")
    final = {}
    for horse in outcome.get("horses") or []:
        try:
            n = int(horse["number"])
            price = positive_number(horse.get("finalOdds"))
        except (ValueError, TypeError, KeyError):
            continue
        if price is not None:
            final[n] = price

    captured = {}
    for horse in prediction.get("horses") or []:
        try:
            n = int(horse["number"])
            price = positive_number(horse.get("odds"))
        except (ValueError, TypeError, KeyError):
            continue
        if price is not None:
            captured[n] = price

    rows = []
    for n in sorted(set(captured) & set(final)):
        a, b = captured[n], final[n]
        ratio = b / a
        rows.append({
            "number": n,
            "frozenOdds": a,
            "finalOdds": b,
            "finalToFrozenOddsRatio": round(ratio, 4),
            "absPctChange": round(abs(ratio - 1) * 100, 3),
            "absLogPriceChange": abs(math.log(ratio)),
            "decreased50PctOrMore": b <= a * 0.5,
            "increased50PctOrMore": b >= a * 1.5,
        })
    favorite_before = min(captured, key=captured.get) if captured else None
    favorite_after = min(final, key=final.get) if final else None
    return {
        "raceID": rid,
        "capturedHorses": len(captured),
        "finalHorses": len(final),
        "matchedHorses": len(rows),
        "favoriteChanged": (
            favorite_before != favorite_after
            if favorite_before is not None and favorite_after is not None
            else None
        ),
        "frozenFavoriteNumber": favorite_before,
        "finalFavoriteNumber": favorite_after,
        "rows": rows,
    }


def audit_repository(root: Path):
    aligned = read_json(root / "AlignedPortfolioReview.json")
    if (aligned.get("projectionState") != "verified"
            or aligned.get("aligned", {}).get("countsAreCurrent") is not True):
        raise ValueError("aligned cohort not verified/current")
    cache = {}
    races = []
    failures = []
    for race in aligned.get("races", []):
        if race.get("resultState") != "matched":
            continue
        day = str(race["raceDate"]).replace("-", "")
        rid = str(race["raceID"])
        if day not in cache:
            path = root / "reports" / day / "ResultData.json"
            if not path.exists():
                cache[day] = {}
            else:
                cache[day] = {
                    str(x.get("raceID")): x for x in read_json(path)
                    if isinstance(x, dict) and x.get("raceID")
                }
        outcome = cache[day].get(rid)
        win_path = root / "reports" / day / "all_bet_v1" / "win" / f"{rid}.json"
        if outcome is None or not win_path.exists():
            failures.append({"raceID": rid, "reason": "frozen_or_final_missing"})
            continue
        try:
            compared = compare_race(read_json(win_path), outcome)
            compared["raceDate"] = race["raceDate"]
            races.append(compared)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as err:
            failures.append({"raceID": rid, "reason": "parse_or_identity_error",
                             "type": type(err).__name__})

    allrows = [x for race in races for x in race["rows"]]
    changes = sorted(x["absPctChange"] for x in allrows)
    favs = [r for r in races if r["favoriteChanged"] is not None]
    notable = []
    for race in races:
        if race["rows"]:
            biggest = max(race["rows"], key=lambda x: x["absPctChange"])
            notable.append({"raceDate": race["raceDate"], "raceID": race["raceID"],
                            "number": biggest["number"],
                            "frozenOdds": biggest["frozenOdds"],
                            "finalOdds": biggest["finalOdds"],
                            "absPctChange": biggest["absPctChange"],
                            "favoriteChanged": race["favoriteChanged"]})
    notable.sort(key=lambda x: x["absPctChange"], reverse=True)
    return {
        "schemaVersion": 1,
        "mode": "post_hoc_price_movement_only",
        "alignedVerified": True,
        "matchedRacesAudited": len(races),
        "totalHorsesCompared": len(allrows),
        "missingOrInvalidRaces": failures,
        "favoriteChangedRaces": sum(bool(r["favoriteChanged"]) for r in favs),
        "favoriteComparableRaces": len(favs),
        "averageAbsPctPriceMovement": (
            round(sum(changes) / len(changes), 3) if changes else None
        ),
        "medianAbsPctPriceMovement": round(median(changes), 3) if changes else None,
        "horsesMovedAtLeast20Pct": sum(x >= 20 for x in changes),
        "horsesMovedAtLeast50Pct": sum(x >= 50 for x in changes),
        "pricesHalvedOrMore": sum(x["decreased50PctOrMore"] for x in allrows),
        "pricesIncreased50PctOrMore": sum(x["increased50PctOrMore"] for x in allrows),
        "mostChangedExamples": notable[:15],
        "finalOddsUsedAsPreRace": False,
        "liveRecommendationsChanged": False,
        "networkRequests": 0,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(audit_repository(args.root), indent=2, ensure_ascii=False,
                     allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
