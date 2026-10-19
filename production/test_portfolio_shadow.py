from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_shadow as m


def c(cid, p, ev, odds, hit=None, payout=None):
    return {
        "id": str(cid), "label": str(cid), "probabilityPct": p,
        "oddsLow": odds, "oddsHigh": odds, "expectedMultiplierLow": ev,
        "unitYen": 100, "hit": hit, "payoutYen": payout,
    }


def row(kind, candidates, status="matched"):
    return {"betType": kind, "status": status, "candidates": candidates}


def sig(level, grade, pairs=None):
    return {
        "eligible": True, "venue": "X", "raceNumber": 1, "raceName": "r", "startTime": "10:00",
        "chaos": {"level": level, "stars": "*"}, "battle": {"grade": grade},
        "wideEvaluation": {"status": "live_odds_connected", "pairs": pairs or []},
    }


def wp(a, b, p, ev, low=5, rank=1):
    return {
        "number1": a, "name1": f"h{a}", "number2": b, "name2": f"h{b}",
        "aiWideProbability": p, "conservativeExpectedValue": ev,
        "wideOddsLow": low, "wideOddsHigh": low,
        "practicalScore": 1, "practicalRank": rank,
    }


# low chaos -> win single
rows = {("R", "win"): row("win", [c(1, 25, 1.10, 4, True, 400)])}
p = m.build_race_plan("R", "2026-10-24", rows, sig(2, "A"), {})
assert p["planType"] == "win_single" and p["totalStakeYen"] == 100 and p["profitYen"] == 300

# medium -> same anchor win + wide
rows = {("R", "win"): row("win", [c(1, 20, 1.08, 5, False, 0)])}
pairs = [wp(1, 2, 15, 1.20, 8)]
payouts = {("R", 1, 2): {"hit": True, "payoutPer100Yen": 800}}
p = m.build_race_plan("R", "2026-10-24", rows, sig(3, "B", pairs), payouts)
assert p["planType"] == "win_plus_wide" and len(p["legs"]) == 2 and p["returnYen"] == 800 and p["profitYen"] == 600

# high chaos with B must skip
p = m.build_race_plan("R", "2026-10-24", {}, sig(5, "B", pairs), payouts)
assert p["planType"] == "skip"

# high chaos with A -> wide + trio when trio qualifies
rows = {("R", "trio"): row("trio", [c("010203", 5, 1.20, 24, False, 0)])}
p = m.build_race_plan("R", "2026-10-24", rows, sig(5, "A", pairs), payouts)
assert p["planType"] == "wide_plus_trio" and len(p["legs"]) == 2

# old dates are never counted as prospective
p = m.build_race_plan("R", "2026-10-18", rows, sig(5, "A", pairs), payouts)
assert p["scope"] == "development_backfill"

print("portfolio shadow tests OK")
