from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_shadow as m

# Forward-protocol lock.  Changing any of these after the prospective start must
# be done under a new portfolio-shadow-v2-* version instead of silently altering
# v1.  These assertions intentionally fail CI if v1 is edited in place.
assert m.VERSION == "portfolio-shadow-v1-20261019"
assert m.PROSPECTIVE_START == "2026-10-24"
assert m.UNIT_YEN == 100
assert m.ALLOWED_BATTLE == {"S", "A", "B"}
assert m.STRONG_BATTLE == {"S", "A"}
assert m.WIN_MIN_PROB_PCT == 18.0
assert m.WIN_MIN_EV == 1.05
assert m.MEDIUM_WIN_MIN_PROB_PCT == 15.0
assert m.MEDIUM_WIN_MIN_EV == 1.03
assert m.WIDE_MIN_PROB_PCT == 7.0
assert m.WIDE_MIN_EV == 1.05
assert m.HIGH_CHAOS_WIDE_MIN_PROB_PCT == 5.0
assert m.HIGH_CHAOS_WIDE_MIN_EV == 1.10
assert m.TRIO_MIN_PROB_PCT == 4.0
assert m.TRIO_MIN_EV == 1.10


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
assert p["scope"] == "prospective"
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

# boundary: the day immediately before start is development only, start day is prospective.
p = m.build_race_plan("R", "2026-10-23", rows, sig(5, "A", pairs), payouts)
assert p["scope"] == "development_backfill"
p = m.build_race_plan("R", "2026-10-24", rows, sig(5, "A", pairs), payouts)
assert p["scope"] == "prospective"

print("portfolio shadow tests OK")
