#!/usr/bin/env python3
import unittest

from build_race_simulation_data import build_batch


class BuildRaceSimulationDataTests(unittest.TestCase):
    def test_builds_current_batch_and_keeps_motion_independent(self):
        race_data = [
            {
                "raceID": "TEST-BATCH-1",
                "raceDate": "2026-11-03",
                "venue": "東京",
                "raceNumber": 1,
                "startTime": "10:00",
                "raceName": "テスト",
                "course": "芝1600m",
                "horses": [
                    {"number": 1, "name": "A", "aiProbability": 80.0, "odds": 1.2},
                    {"number": 2, "name": "B", "aiProbability": 15.0, "odds": 8.0},
                    {"number": 3, "name": "C", "aiProbability": 5.0, "odds": 25.0},
                ],
            }
        ]
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
        self.assertEqual(payload["summary"]["simulatedRaces"], 1)
        row = payload["races"][0]
        self.assertEqual(row["raceID"], "TEST-BATCH-1")
        self.assertFalse(row["independence"]["usesPredictionAIForMotion"])
        self.assertFalse(row["independence"]["usesOddsForMotion"])
        self.assertEqual(row["independence"]["strengthSource"], "neutral-equal-strength")
        self.assertEqual(len(row["horses"]), 3)

    def test_missing_research_uses_neutral_fallback_instead_of_failing(self):
        race_data = [
            {
                "raceID": "TEST-BATCH-2",
                "raceDate": "2026-11-03",
                "venue": "東京",
                "raceNumber": 2,
                "startTime": "10:30",
                "raceName": "テスト2",
                "course": "ダ1400m",
                "horses": [
                    {"number": 1, "name": "A", "aiProbability": 50.0},
                    {"number": 2, "name": "B", "aiProbability": 50.0},
                ],
            }
        ]
        payload = build_batch(race_data, {"races": []}, runs=10, seed=11)
        self.assertEqual(payload["summary"]["simulatedRaces"], 1)
        self.assertEqual(payload["summary"]["errorRaces"], 0)
        self.assertEqual(payload["races"][0]["paceSource"], "neutral-mid-fallback")
        self.assertEqual(payload["races"][0]["inputQuality"]["styleCoveragePct"], 0.0)


if __name__ == "__main__":
    unittest.main()
