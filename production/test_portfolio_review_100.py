from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_review_100 as review

JST = timezone(timedelta(hours=9))


def race(day, rid, plan_type, label, settled=True, hit=False, profitable=False, torigami=False,
         stake=100, returned=0, reason="", chaos=1, battle="A", start="12:00", formal=True):
    evidence = {
        "eligible": formal,
        "reason": "formal_10min_verified" if formal else "formal_win_sidecar_missing",
    }
    return {
        "raceDate": day,
        "raceID": rid,
        "startTime": start,
        "scope": "prospective",
        "ruleVersion": review.SOURCE_VERSION,
        "formal10minEligible": formal,
        "formal10minEvidence": evidence,
        "chaosLevel": chaos,
        "battleGrade": battle,
        "planType": plan_type,
        "planLabel": label,
        "reason": reason,
        "settled": settled,
        "hitAny": hit if settled and plan_type != "skip" else None,
        "profitable": profitable if settled and plan_type != "skip" else None,
        "torigami": torigami if settled and plan_type != "skip" else None,
        "totalStakeYen": 0 if plan_type == "skip" else stake,
        "returnYen": None if not settled or plan_type == "skip" else returned,
    }


def source(races):
    return {
        "schemaVersion": 1,
        "version": review.SOURCE_VERSION,
        "status": "research_display_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "prospectiveStartDate": review.PROSPECTIVE_START,
        "races": races,
    }


out = review.build(source([]), datetime(2026, 10, 23, 12, tzinfo=JST))
assert out["reviewStatus"] == "waiting_for_formal_prospective_capture"
assert out["remainingTo100"] == 100
assert out["reviewWindowRaces"] == 0

races = [
    race("2026-10-24", "R1", "win_single", "単勝1点", True, True, True, False, 100, 300, chaos=1, battle="A", start="10:00"),
    race("2026-10-24", "R2", "win_plus_wide", "単勝＋ワイド", True, True, False, True, 300, 250, chaos=3, battle="B", start="11:00"),
    race("2026-10-24", "R3", "skip", "見送り", False, reason="勝負度がC以下", chaos=4, battle="C", start="12:00"),
    race("2026-10-24", "R4", "wide_focus", "ワイド中心", False, stake=200, chaos=5, battle="A", start="13:00"),
]
out = review.build(source(races), datetime(2026, 10, 24, 18, tzinfo=JST))
assert out["reviewStatus"] == "collecting"
assert out["prospectiveRacesObserved"] == 4
assert out["racesObserved"] == 4 and out["remainingTo100"] == 96
assert out["excludedNonFormalRaces"] == 0
assert out["overall"]["selectedRaces"] == 3
assert out["overall"]["skippedRaces"] == 1
assert out["overall"]["settledSelectedRaces"] == 2
assert out["overall"]["resultsPendingSelectedRaces"] == 1
assert out["overall"]["hitAnyRaces"] == 2
assert out["overall"]["profitableRaces"] == 1
assert out["overall"]["torigamiRaces"] == 1
assert out["overall"]["stakeYen"] == 400
assert out["overall"]["returnYen"] == 550
assert abs(out["overall"]["returnIndex"] - 1.375) < 1e-12
assert out["overall"]["torigamiRatePctOfHitRaces"] == 50.0
assert len(out["planMix"]) == 4
assert len(out["chaosBreakdown"]) == 4
assert out["skipReasons"][0]["races"] == 1

with_morning = [
    race("2026-10-24", "F1", "skip", "見送り", False, start="09:45"),
    race("2026-10-24", "MORNING", "skip", "見送り", False, start="09:55", formal=False),
    race("2026-10-24", "F2", "skip", "見送り", False, start="10:05"),
]
out = review.build(source(with_morning))
assert out["prospectiveRacesObserved"] == 3
assert out["racesObserved"] == 2
assert out["remainingTo100"] == 98
assert out["excludedNonFormalRaces"] == 1
assert out["excludedNonFormalRaceDetails"][0]["raceID"] == "MORNING"
assert out["qualityChecks"]["formal10minEvidenceRequired"] is True
assert out["qualityChecks"]["nonFormalProspectiveExcluded"] == 1

missing_marker = race("2026-10-24", "NOEVID", "skip", "見送り", False)
missing_marker.pop("formal10minEligible")
missing_marker.pop("formal10minEvidence")
out = review.build(source([missing_marker]))
assert out["racesObserved"] == 0
assert out["excludedNonFormalRaces"] == 1

races100 = []
for i in range(100):
    start = f"{9 + (i % 8):02d}:{(i * 5) % 60:02d}"
    if i % 4 == 0:
        races100.append(race("2026-10-24", f"R{i:03d}", "skip", "見送り", False,
                             reason="条件不足", chaos=(i % 5) + 1, battle="C", start=start))
    else:
        pending = i == 99
        races100.append(race("2026-10-24", f"R{i:03d}", "win_single", "単勝1点", not pending,
                             i % 3 == 0, i % 5 == 0, False, 100, 200 if i % 5 == 0 else 0,
                             chaos=(i % 5) + 1, battle="A", start=start))
out = review.build(source(races100), datetime(2026, 10, 24, 20, tzinfo=JST))
assert out["racesObserved"] == 100
assert out["reviewWindowRaces"] == 100
assert out["remainingTo100"] == 0
assert out["reviewStatus"] == "target_reached_results_pending"

races100[-1] = race("2026-10-24", "R099", "win_single", "単勝1点", True, False, False, False,
                    100, 0, chaos=5, battle="A", start="12:15")
out = review.build(source(races100), datetime(2026, 10, 24, 21, tzinfo=JST))
assert out["reviewStatus"] == "ready_for_short_term_review"
assert out["first100CohortLockedByRule"] is True

base = out
later = list(races100)
for i in range(100, 120):
    later.append(race("2026-10-25", f"R{i:03d}", "win_single", "単勝1点", True, True, True,
                      False, 100, 500, chaos=1, battle="S", start=f"{10 + (i % 6):02d}:00"))
out = review.build(source(later), datetime(2026, 10, 25, 21, tzinfo=JST))
assert out["racesObserved"] == 120
assert out["reviewWindowRaces"] == 100
assert out["qualityChecks"]["laterFormalRacesExcludedAfterFirst100"] == 20
assert out["overall"] == base["overall"]
assert out["planMix"] == base["planMix"]

dev = race("2026-10-23", "DEV", "win_single", "単勝1点", True, True, True, False, 100, 999)
dev["scope"] = "development_backfill"
out = review.build(source([dev] + races[:2]))
assert out["racesObserved"] == 2

bad = source([race("2026-10-24", "BAD", "win_single", "単勝1点")])
bad["races"][0]["ruleVersion"] = "portfolio-shadow-v2-test"
try:
    review.build(bad)
    raise AssertionError("mixed version was accepted")
except ValueError:
    pass

print("portfolio 100-race review tests OK")
