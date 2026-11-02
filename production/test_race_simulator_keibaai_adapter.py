#!/usr/bin/env python3
import copy
import unittest

from race_simulator_keibaai_adapter import simulate_current_keibaai


class CurrentKeibaAIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.race = [{
            "raceDate": "2026-11-01",
            "raceID": "202608041101",
            "venue": "京都",
            "raceName": "2歳未勝利",
            "course": "ダ1400m",
            "horses": [
                {"number": 1, "name": "A", "aiProbability": 40.0, "marketProbability": 39.0, "odds": 2.0},
                {"number": 2, "name": "B", "aiProbability": 35.0, "marketProbability": 36.0, "odds": 3.0},
                {"number": 3, "name": "C", "aiProbability": 25.0, "marketProbability": 25.0, "odds": 5.0},
            ],
        }]
        self.research = {
            "schemaVersion": 1,
            "races": [{
                "raceID": "202608041101",
                "horses": [
                    {"number": 1, "styleTendency": {"sampleRaces": 3, "dominantStyle": "先行", "distributionPct": {"逃げ": 0.0, "先行": 66.7, "差し": 33.3, "追込": 0.0}}, "initialPositionHistory": {"sampleRaces": 3, "meanRelativePosition": 0.25}},
                    {"number": 2, "styleTendency": {"sampleRaces": 4, "dominantStyle": "追込", "distributionPct": {"逃げ": 0.0, "先行": 0.0, "差し": 25.0, "追込": 75.0}}, "initialPositionHistory": {"sampleRaces": 4, "meanRelativePosition": 0.75}},
                    {"number": 3, "styleTendency": None, "initialPositionHistory": None},
                ],
                "formation": {"status": "insufficient_history", "coveragePct": 18.2, "paceForecast": {"status": "unavailable"}},
            }],
        }

    def test_current_schema_is_parsed(self):
        result = simulate_current_keibaai(self.race, self.research, "202608041101", runs=40, seed=111)
        self.assertEqual(result["distanceMeters"], 1400.0)
        self.assertEqual(result["track"], "ダ1400m")
        self.assertEqual(result["pace"], "MID")
        self.assertEqual(result["paceSource"], "neutral-mid-fallback")
        self.assertEqual(result["inputQuality"]["styleKnownHorses"], 2)
        by_no = {row["horseNumber"]: row for row in result["horses"]}
        self.assertEqual(by_no[1]["style"], "先行")
        self.assertEqual(by_no[2]["style"], "追込")
        self.assertEqual(by_no[3]["style"], "不明")

    def test_ai_and_odds_do_not_change_motion_result(self):
        flipped = copy.deepcopy(self.race)
        flipped[0]["horses"][0].update(aiProbability=1.0, marketProbability=1.0, odds=100.0)
        flipped[0]["horses"][1].update(aiProbability=1.0, marketProbability=1.0, odds=100.0)
        flipped[0]["horses"][2].update(aiProbability=98.0, marketProbability=98.0, odds=1.01)
        a = simulate_current_keibaai(self.race, self.research, "202608041101", runs=60, seed=77)
        b = simulate_current_keibaai(flipped, self.research, "202608041101", runs=60, seed=77)
        self.assertEqual(a["horses"], b["horses"])
        self.assertNotEqual(a["comparisonWithPredictionAI"], b["comparisonWithPredictionAI"])


if __name__ == "__main__":
    unittest.main()
