#!/usr/bin/env python3
import unittest

from build_race_simulation_data import build_batch


def horse(number, name, probability=50.0):
    return {"number": number, "name": name, "aiProbability": probability}


def race(race_id, race_date, number=1):
    return {
        "raceID": race_id,
        "raceDate": race_date,
        "venue": "東京",
        "raceNumber": number,
        "startTime": "10:00",
        "raceName": "テスト",
        "course": "芝1600m",
        "horses": [horse(1, "A", 60.0), horse(2, "B", 30.0), horse(3, "C", 10.0)],
    }


class BuildRaceSimulationDataTests(unittest.TestCase):
    def test_builds_current_batch_and_keeps_motion_independent(self):
        race_data = [race("TEST-BATCH-1", "2026-11-03")]
        research_data = {
            "races": [
                {
                    "raceID": "TEST-BATCH-1",
                    "raceDate": "2026-11-03",
                    "horses": [
                        {"number": 1, "name": "A", "styleTendency": {"sampleRaces": 4, "dominantStyle": "逃げ", "confidence": "中", "distributionPct": {"逃げ": 100.0}, "latestHistoryDate": "2026-10-01"}, "initialPositionHistory": {"sampleRaces": 4, "meanRelativePosition": 0.1, "minRelativePosition": 0.0, "maxRelativePosition": 0.2, "latestHistoryDate": "2026-10-01"}},
                        {"number": 2, "name": "B", "styleTendency": {"sampleRaces": 4, "dominantStyle": "先行", "confidence": "中", "distributionPct": {"先行": 100.0}, "latestHistoryDate": "2026-10-01"}, "initialPositionHistory": {"sampleRaces": 4, "meanRelativePosition": 0.35, "minRelativePosition": 0.2, "maxRelativePosition": 0.5, "latestHistoryDate": "2026-10-01"}},
                        {"number": 3, "name": "C", "styleTendency": {"sampleRaces": 4, "dominantStyle": "差し", "confidence": "中", "distributionPct": {"差し": 100.0}, "latestHistoryDate": "2026-10-01"}, "initialPositionHistory": {"sampleRaces": 4, "meanRelativePosition": 0.7, "minRelativePosition": 0.5, "maxRelativePosition": 0.9, "latestHistoryDate": "2026-10-01"}},
                    ],
                    "formation": {"paceForecast": {"status": "unavailable"}},
                }
            ]
        }

        payload = build_batch(race_data, research_data, runs=20, seed=7, dt=0.5)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["targetRaceDate"], "2026-11-03")
        self.assertEqual(payload["summary"]["simulatedRaces"], 1)
        row = payload["races"][0]
        self.assertEqual(row["raceID"], "TEST-BATCH-1")
        self.assertFalse(row["independence"]["usesPredictionAIForMotion"])
        self.assertFalse(row["independence"]["usesOddsForMotion"])
        self.assertEqual(row["independence"]["strengthSource"], "neutral-equal-strength")
        self.assertEqual(len(row["horses"]), 3)

    def test_missing_research_uses_neutral_fallback_instead_of_failing(self):
        race_data = [race("TEST-BATCH-2", "2026-11-03", 2)]
        payload = build_batch(race_data, {"races": []}, runs=10, seed=11)
        self.assertEqual(payload["summary"]["simulatedRaces"], 1)
        self.assertEqual(payload["summary"]["errorRaces"], 0)
        self.assertEqual(payload["races"][0]["paceSource"], "neutral-mid-fallback")
        self.assertEqual(payload["races"][0]["inputQuality"]["styleCoveragePct"], 0.0)

    def test_daily_mode_only_simulates_latest_date_and_backfill_can_include_all_dates(self):
        race_data = [race("OLD", "2026-11-01"), race("NEW", "2026-11-03")]
        daily = build_batch(race_data, {"races": []}, runs=5, seed=3)
        self.assertEqual(daily["targetRaceDate"], "2026-11-03")
        self.assertEqual(daily["summary"]["sourceRaces"], 2)
        self.assertEqual(daily["summary"]["inputRaces"], 1)
        self.assertEqual([row["raceID"] for row in daily["races"]], ["NEW"])

        backfill = build_batch(race_data, {"races": []}, runs=5, seed=3, all_dates=True)
        self.assertEqual(backfill["summary"]["inputRaces"], 2)
        self.assertEqual({row["raceID"] for row in backfill["races"]}, {"OLD", "NEW"})


if __name__ == "__main__":
    unittest.main()
