from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_early_review as m

JST = timezone(timedelta(hours=9))


def race(i, scope, selected=True, settled=True, hit=False, profitable=False, torigami=False):
    return {
        "raceDate": "2026-10-18" if scope == "development_backfill" else "2026-10-24",
        "raceID": f"R{i:03d}",
        "startTime": "12:00",
        "scope": scope,
        "ruleVersion": m.SOURCE_VERSION,
        "planType": "win_single" if selected else "skip",
        "planLabel": "単勝1点" if selected else "見送り",
        "reason": "条件不足" if not selected else "",
        "settled": settled if selected else False,
        "hitAny": hit if selected and settled else None,
        "profitable": profitable if selected and settled else None,
        "torigami": torigami if selected and settled else None,
        "totalStakeYen": 100 if selected else 0,
        "returnYen": (300 if profitable else (80 if torigami else 0)) if selected and settled else None,
    }


def source(races):
    return {
        "schemaVersion": 1,
        "version": m.SOURCE_VERSION,
        "status": "research_display_only",
        "formalRecommendationsEnabled": False,
        "autoBetEnabled": False,
        "stakeSizingEnabled": False,
        "prospectiveStartDate": m.PROSPECTIVE_START,
        "races": races,
    }


dev = [race(i, "development_backfill", selected=(i % 3 == 0), hit=(i % 6 == 0), profitable=(i % 9 == 0)) for i in range(69)]
out = m.build(source(dev), datetime(2026, 10, 20, 20, tzinfo=JST))
assert out["reviewStatus"] == "collecting"
assert out["developmentRacesUsed"] == 69
assert out["prospectiveRacesUsed"] == 0
assert out["remainingToReference100"] == 31
assert out["progressPct"] == 69.0

future = [race(100 + i, "prospective", selected=True, settled=(i != 30), hit=(i % 5 == 0), profitable=(i % 10 == 0)) for i in range(31)]
out = m.build(source(dev + future), datetime(2026, 10, 24, 20, tzinfo=JST))
assert out["combinedReferenceRaces"] == 100
assert out["prospectiveRacesUsed"] == 31
assert out["remainingToReference100"] == 0
assert out["reviewStatus"] == "reference_100_reached_results_pending"

future[-1] = race(130, "prospective", selected=True, settled=True)
out = m.build(source(dev + future), datetime(2026, 10, 24, 21, tzinfo=JST))
assert out["reviewStatus"] == "reference_100_ready"

# Baseline count drift must be surfaced rather than silently treated as the same study.
out = m.build(source(dev[:-1]), datetime(2026, 10, 20, 20, tzinfo=JST))
assert out["baselineDriftDetected"] is True
assert out["reviewStatus"] == "baseline_drift_detected"

print("portfolio early reference review tests OK")
