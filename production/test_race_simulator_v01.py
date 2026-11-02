#!/usr/bin/env python3
import copy
import unittest

from race_simulator_v01 import HorseInput, simulate_from_keibaai, simulate_race


class RaceSimulatorV01Tests(unittest.TestCase):
    def test_win_probabilities_sum_to_one(self):
        horses = [
            HorseInput(1, "A", style="逃げ"),
            HorseInput(2, "B", style="先行"),
            HorseInput(3, "C", style="差し"),
            HorseInput(4, "D", style="追込"),
        ]
        result = simulate_race(horses, distance_m=1600, runs=120, seed=1234, dt=0.5)
        total = sum(row["simulationWinProbability"] for row in result["horses"])
        self.assertAlmostEqual(total, 1.0, places=6)
        for row in result["horses"]:
            self.assertLessEqual(row["simulationWinProbability"], row["simulationTop2Probability"])
            self.assertLessEqual(row["simulationTop2Probability"], row["simulationTop3Probability"])

    def test_existing_ai_probability_does_not_change_motion_answer(self):
        base_race = [{
            "raceID": "TEST-R1", "raceName": "independence test", "distance": 1600,
            "horses": [
                {"horseNumber": 1, "horseName": "A", "aiProbability": 0.90, "odds": 1.2},
                {"horseNumber": 2, "horseName": "B", "aiProbability": 0.08, "odds": 20.0},
                {"horseNumber": 3, "horseName": "C", "aiProbability": 0.02, "odds": 80.0},
            ],
        }]
        research = [{
            "raceID": "TEST-R1", "formation": {"pace": "MID"},
            "style": [
                {"number": 1, "style": "逃げ", "sampleCount": 5, "avgCorner1": 1.4, "avgCorner4": 2.0},
                {"number": 2, "style": "差し", "sampleCount": 5, "avgCorner1": 6.0, "avgCorner4": 4.0},
                {"number": 3, "style": "追込", "sampleCount": 5, "avgCorner1": 8.0, "avgCorner4": 5.0},
            ],
        }]
        flipped = copy.deepcopy(base_race)
        flipped[0]["horses"][0]["aiProbability"] = 0.01
        flipped[0]["horses"][0]["odds"] = 100.0
        flipped[0]["horses"][1]["aiProbability"] = 0.01
        flipped[0]["horses"][2]["aiProbability"] = 0.98
        flipped[0]["horses"][2]["odds"] = 1.01

        a = simulate_from_keibaai(base_race, research, "TEST-R1", runs=100, seed=77)
        b = simulate_from_keibaai(flipped, research, "TEST-R1", runs=100, seed=77)
        self.assertEqual(a["horses"], b["horses"])
        self.assertFalse(a["independence"]["usesPredictionAIForMotion"])
        self.assertFalse(a["independence"]["usesOddsForMotion"])
        self.assertNotEqual(a["comparisonWithPredictionAI"], b["comparisonWithPredictionAI"])

    def test_same_seed_is_reproducible(self):
        horses = [
            HorseInput(1, "A", style="逃げ", independent_strength=1.04),
            HorseInput(2, "B", style="先行", independent_strength=1.00),
            HorseInput(3, "C", style="差し", independent_strength=0.96),
        ]
        a = simulate_race(horses, 2000, pace="FAST", runs=80, seed=2026)
        b = simulate_race(horses, 2000, pace="FAST", runs=80, seed=2026)
        self.assertEqual(a, b)

    def test_external_strength_changes_only_independent_strength_path(self):
        race = [{
            "raceID": "TEST-R2", "distance": "芝1800m",
            "horses": [
                {"horseNumber": 1, "horseName": "A", "aiProbability": 0.50},
                {"horseNumber": 2, "horseName": "B", "aiProbability": 0.30},
                {"horseNumber": 3, "horseName": "C", "aiProbability": 0.20},
            ],
        }]
        research = [{"raceID": "TEST-R2", "formation": {"paceRaw": "SLOW"}, "style": []}]
        strengths = {"TEST-R2": {"1": 0.8, "2": 1.0, "3": 1.2}}
        result = simulate_from_keibaai(race, research, "TEST-R2", strengths, runs=80, seed=9)
        self.assertEqual(result["independence"]["strengthSource"], "external-independent")
        by_no = {row["horseNumber"]: row for row in result["horses"]}
        self.assertGreater(by_no[3]["independentStrength"], by_no[1]["independentStrength"])


if __name__ == "__main__":
    unittest.main()
