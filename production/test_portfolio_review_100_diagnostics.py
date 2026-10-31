from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import portfolio_review_100_diagnostics as d

assert d.parse_course("芝1600m") == ("芝", 1600)
assert d.parse_course("ダ1400m") == ("ダ", 1400)
assert d.distance_band(1200) == "<=1200m"
assert d.distance_band(1400) == "1400-1600m"

review = {
    "version": d.REVIEW_VERSION,
    "sourceVersion": d.SOURCE_VERSION,
    "first100CohortLockedByRule": True,
    "reviewWindowRaces": 100,
    "racesObserved": 100,
    "remainingTo100": 0,
    "reviewStatus": "ready_for_short_term_review",
    "generatedAtJST": "2026-11-01T12:00:00+09:00",
    "reviewWindowStartDate": "2026-10-24",
    "reviewWindowEndDate": "2026-11-01",
}
assert d.should_generate(review)
assert not d.should_generate({**review, "reviewWindowRaces": 99, "remainingTo100": 1})
assert not d.should_generate({**review, "reviewStatus": "target_reached_results_pending"})

with TemporaryDirectory() as td:
    root = Path(td)
    cohort = []
    for i in range(100):
        day = "2026-10-24" if i < 50 else "2026-11-01"
        rid = f"R{i:03d}"
        plan = "skip" if i % 4 == 0 else ("wide_focus" if i % 2 == 0 else "wide_plus_trio")
        selected = plan != "skip"
        hit = selected and i % 7 == 0
        legs = [] if not selected else [{
            "betType": "wide",
            "unitYen": 100,
            "hit": hit,
            "payoutYen": 300 if hit else 0,
        }]
        cohort.append({
            "raceDate": day,
            "raceID": rid,
            "venue": "東京" if i % 2 == 0 else "京都",
            "raceNumber": i % 12 + 1,
            "startTime": "12:00",
            "chaosLevel": 5 if selected else 3,
            "battleGrade": "S" if i % 3 == 0 else "A",
            "planType": plan,
            "reason": "条件不足" if not selected else "ok",
            "legs": legs,
            "settled": selected,
            "hitAny": hit if selected else None,
            "profitable": hit if selected else None,
            "torigami": False if selected else None,
            "totalStakeYen": 100 if selected else 0,
            "returnYen": (300 if hit else 0) if selected else None,
        })
        directory = root / "reports" / day.replace("-", "") / "all_bet_v1" / "win"
        directory.mkdir(parents=True, exist_ok=True)
        horses = [
            {
                "number": n,
                "name": f"H{n}",
                "popularity": n,
                "odds": 1.5 + n,
                "aiProbability": 40.0 if n == 1 else max(1.0, 20.0 - n),
            }
            for n in range(1, 14)
        ]
        (directory / f"{rid}.json").write_text(json.dumps({
            "prediction": {
                "predictionTiming": "live_10min",
                "course": "芝1600m" if i % 2 == 0 else "ダ1400m",
                "horses": horses,
            }
        }), encoding="utf-8")

    out = d.build_diagnostics(root, review, cohort)
    assert out["overall"]["races"] == 100
    assert out["qualityChecks"]["cohortExactly100"] is True
    assert out["qualityChecks"]["allSelectedSettled"] is True
    assert out["qualityChecks"]["formalSnapshotCoverageRaces"] == 100
    assert out["focusMetrics"]["battleS"]["races"] > 0
    assert len(out["ticketBreakdown"]) == 1
    assert out["ticketBreakdown"][0]["betType"] == "wide"
    assert sum(x["races"] for x in out["breakdowns"]["surface"]) == 100
    assert sum(x["races"] for x in out["breakdowns"]["venue"]) == 100
    md = d.render_markdown(out)
    assert "勝負度" in md
    assert "1位予測馬のAI勝率帯" in md
    assert "見送り理由" in md

print("portfolio 100-race diagnostics tests OK")
