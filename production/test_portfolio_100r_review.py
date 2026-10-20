from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_100r_review as m


def race(i, selected=True, settled=True, rule=m.SOURCE_VERSION):
    day = "2026-10-24" if i < 50 else "2026-10-25"
    row = {
        "raceDate": day,
        "raceID": f"R{i:03d}",
        "startTime": f"{9 + (i % 8):02d}:{(i * 5) % 60:02d}",
        "scope": "prospective",
        "ruleVersion": rule,
        "chaosLevel": (i % 5) + 1,
        "battleGrade": ["S", "A", "B", "C", "見送り"][i % 5],
        "planType": "win_single" if selected else "skip",
        "planLabel": "単勝1点" if selected else "見送り",
        "reason": "candidate" if selected else "条件不足",
        "legs": [],
        "totalStakeYen": 100 if selected else 0,
        "settled": bool(selected and settled),
        "returnYen": 200 if selected and settled and i % 3 == 0 else (0 if selected and settled else None),
        "profitYen": 100 if selected and settled and i % 3 == 0 else (-100 if selected and settled else None),
        "hitAny": True if selected and settled and i % 3 == 0 else (False if selected and settled else None),
        "profitable": True if selected and settled and i % 3 == 0 else (False if selected and settled else None),
        "torigami": False if selected and settled else None,
    }
    return row


def source(rows):
    return {
        "schemaVersion": 1,
        "version": m.SOURCE_VERSION,
        "status": "research_display_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "generatedAtJST": "2026-10-25T18:00:00+09:00",
        "prospectiveStartDate": m.PROSPECTIVE_START,
        "races": rows,
    }


# Before 100: collecting.
v = m.build(source([race(i, selected=(i % 2 == 0)) for i in range(99)]))
assert v["status"] == "collecting"
assert v["reviewWindowRaces"] == 99 and v["remainingTo100"] == 1

# Exactly 100, fully settled: first review ready.
v = m.build(source([race(i, selected=(i % 2 == 0)) for i in range(100)]))
assert v["status"] == "first_review_ready"
assert v["reviewWindowRaces"] == 100 and v["remainingTo100"] == 0
assert v["overall"]["selectedRaces"] == 50
assert v["overall"]["settledSelectedRaces"] == 50
assert v["first100CohortLockedByRule"] is True

# 100 observed but one selected result is pending: do not declare review ready.
rows = [race(i, selected=(i % 2 == 0)) for i in range(100)]
rows[98] = race(98, selected=True, settled=False)
v = m.build(source(rows))
assert v["status"] == "awaiting_settlement"
assert v["overall"]["pendingSelectedRaces"] == 1

# Later races must not change first-100 cohort metrics.
base = m.build(source([race(i, selected=(i % 2 == 0)) for i in range(100)]))
with_later = m.build(source([race(i, selected=(i % 2 == 0)) for i in range(120)]))
assert with_later["racesObserved"] == 120
assert with_later["reviewWindowRaces"] == 100
assert with_later["qualityChecks"]["laterRacesExcludedAfterFirst100"] == 20
assert with_later["overall"] == base["overall"]
assert with_later["planBreakdown"] == base["planBreakdown"]

# Development rows are ignored even if present.
dev = race(999)
dev["raceDate"] = "2026-10-23"
dev["scope"] = "development_backfill"
v = m.build(source([dev] + [race(i, selected=False) for i in range(10)]))
assert v["racesObserved"] == 10

# A prospective row with a silently changed rule version must fail closed.
rows = [race(0, rule="portfolio-shadow-v1-mutated")]
try:
    m.build(source(rows))
    raise AssertionError("version mismatch should fail")
except ValueError as exc:
    assert "different rule version" in str(exc)

print("portfolio 100R review tests OK")
