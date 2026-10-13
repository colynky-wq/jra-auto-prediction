from __future__ import annotations

import unittest

import pandas as pd

from build_app_research_data import build_races
from formation_runtime import current_race_has_corner, formation_reference, position_history, relative_position
from research_formation import replay


def horse(number, position=None, observations=3):
    return {"number": number, "name": str(number),
            "initialPositionHistory": position_history([(position, f"2024-01-{i+1:02d}") for i in range(observations)]) if position is not None else None,
            "styleTendency": {"distributionPct": {"逃げ": 0, "先行": 50, "差し": 50, "追込": 0}}}


def rows(records):
    return pd.DataFrame([{
        "レースID": rid, "日付": day, "馬番": 1, "馬キー": key, "馬名": "テスト",
        "初角": corner, "頭数": 10, "障害": False, "芝ダ": surface, "距離": distance,
    } for rid, day, key, corner, surface, distance in records])


def seed():
    return rows([(f"202405010{i+1}01", f"2024-01-0{i+1}", "テスト_2022", 1, "芝", 1600) for i in range(3)])


class FormationTests(unittest.TestCase):
    def test_normalization_accounts_for_field_size_and_rejects_invalid_data(self):
        self.assertEqual(relative_position(1, 10), 0)
        self.assertEqual(relative_position(10, 10), 1)
        self.assertEqual(relative_position(3, 5), relative_position(5, 9))
        for pos, count in [(11, 10), (1.5, 10), (1, 1), (1, 10.5), (float("nan"), 10)]:
            self.assertIsNone(relative_position(pos, count))

    def test_unknown_or_short_history_does_not_create_a_front_position(self):
        horses = [horse(1, .1), horse(2, .3), horse(3, .6), horse(4, .2, 2), horse(5)]
        result = formation_reference(horses)
        self.assertEqual(result["knownHorses"], 3)
        self.assertEqual(result["unknownHorses"], 2)
        self.assertEqual(result["orderedHorseNumbers"], [])

    def test_coverage_boundary_is_exact_and_unknown_horse_is_not_ranked(self):
        horses = [horse(1, .1), horse(2, .3), horse(3, .6), horse(4, .2), horse(5)]
        result = formation_reference(horses)
        self.assertEqual(result["status"], "reference_order")
        self.assertEqual(result["orderedHorseNumbers"], [1, 4, 2, 3])
        self.assertFalse(result["paceForecast"]["status"] == "available")

    def test_straight_course_has_no_initial_corner_reference(self):
        race = {"raceID": "202504010101", "venue": "新潟", "course": "芝1000m"}
        self.assertFalse(current_race_has_corner(race))
        self.assertTrue(current_race_has_corner({**race, "course": "芝1600m"}))
        result = formation_reference([horse(i, .1) for i in range(1, 6)], has_corner=False)
        self.assertEqual(result["status"], "no_corner")
        self.assertEqual(result["orderedHorseNumbers"], [])

    def test_same_day_results_are_not_used_in_other_same_day_predictions(self):
        same_day = rows([("202505010101", "2025-01-01", "テスト_2022", 10, "芝", 1600),
                         ("202505010201", "2025-01-01", "テスト_2022", 10, "芝", 1600)])
        result = replay(pd.concat([seed(), same_day]))
        self.assertEqual([row["six"] for row in result if row["year"] == 2025], [0, 0])
        future = rows([("202605010101", "2026-01-01", "テスト_2022", 10, "芝", 1600)])
        self.assertEqual(result, replay(pd.concat([seed(), same_day, future])))

    def test_straight_passage_positions_do_not_seed_corner_history(self):
        data = rows([(f"202404010{i+1}01", f"2024-01-0{i+1}", "テスト_2022", 1, "芝", 1000) for i in range(3)]
                    + [("202505010101", "2025-01-01", "テスト_2022", 1, "芝", 1600)])
        self.assertIsNone(replay(data)[0]["six"])

    def test_latest_generation_is_resolved_before_filtering_straight_observations(self):
        old = seed().assign(馬キー="テスト_2012")
        new = rows([("202404020101", "2024-08-01", "テスト_2022", 1, "芝", 1000)])
        race = {"raceID": "202505010101", "raceDate": "2025-01-01", "horses": [{
            "number": 1, "name": "テスト", "age": 3, "aiProbability": 3, "marketProbability": 2, "odds": 10}]}
        horse_data = build_races(pd.concat([old, new]), [race])[0]["horses"][0]
        self.assertIsNone(horse_data["initialPositionHistory"])
        self.assertIsNone(horse_data["styleTendency"])

    def test_live_reference_ignores_same_day_and_future_history(self):
        race = {"raceID": "202505010101", "raceDate": "2025-01-01", "horses": [{
            "number": 1, "name": "テスト", "age": 3, "aiProbability": 3, "marketProbability": 2, "odds": 10}]}
        later = rows([("202505010101", "2025-01-01", "テスト_2022", 10, "芝", 1600),
                      ("202605010101", "2026-01-01", "テスト_2022", 10, "芝", 1600)])
        self.assertEqual(build_races(seed(), [race]), build_races(pd.concat([seed(), later]), [race]))


if __name__ == "__main__":
    unittest.main()
