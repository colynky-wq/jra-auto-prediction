from __future__ import annotations

import unittest

import pandas as pd

from app_research_runtime import classify_style, practical_candidates, practical_metrics
from build_app_research_data import build_races, evaluate_2025


def past(rows):
    return pd.DataFrame([{
        "レースID": f"2025050101{i:02d}", "日付": day,
        "馬名": name, "馬キー": key, "初角": pos, "頭数": 10, "障害": False,
    } for i, (day, name, key, pos) in enumerate(rows, 1)])


def race(name="テスト", day="2025-01-02"):
    return {"raceID": "202505010201", "raceDate": day, "horses": [{
        "number": 1, "name": name, "age": 3, "odds": 10.0,
        "aiProbability": 3.0, "marketProbability": 2.0,
    }]}


class AppResearchTests(unittest.TestCase):
    def test_same_day_and_future_results_do_not_change_tendency(self):
        original = past([("2025-01-01", "テスト", "テスト_2022", 1)])
        later = past([("2025-01-02", "テスト", "テスト_2022", 10), ("2025-01-03", "テスト", "テスト_2022", 10)])
        before = build_races(original, [race()])
        after = build_races(pd.concat([original, later]), [race()])
        self.assertEqual(before, after)

    def test_same_name_generations_are_separate(self):
        d = past([("2015-01-01", "テスト", "テスト_2012", 1), ("2025-01-01", "テスト", "テスト_2022", 10)])
        stat = build_races(d, [race()])[0]["horses"][0]["styleTendency"]
        self.assertEqual(stat["sampleRaces"], 1)
        self.assertEqual(stat["dominantStyle"], "追込")

    def test_similar_name_does_not_borrow_history(self):
        d = past([("2025-01-01", "テスト", "テスト_2022", 1)])
        self.assertIsNone(build_races(d, [race(name="テストテソーロ")])[0]["horses"][0]["styleTendency"])

    def test_invalid_corner_is_unknown(self):
        self.assertIsNone(classify_style(11, 10))
        self.assertIsNone(classify_style(1.5, 10))
        self.assertIsNone(classify_style(float("nan"), 10))

    def test_2025_evaluation_uses_2024_but_not_same_day(self):
        d = past([("2024-12-31", "テスト", "テスト_2022", 1), ("2025-01-01", "テスト", "テスト_2022", 10), ("2025-01-01", "テスト", "テスト_2022", 10)])
        result = evaluate_2025(d)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["lastStyleExactPct"], 0)
        future = past([("2026-01-01", "テスト", "テスト_2022", 1)])
        self.assertEqual(result, evaluate_2025(pd.concat([d, future])))

    def test_equal_ev_favors_higher_chance(self):
        self.assertGreater(practical_metrics(0.06, 20)["practicalScore"], practical_metrics(0.03, 40)["practicalScore"])
        self.assertEqual(practical_metrics(0.01, 50)["practicalScore"], 0)

    def test_rounding_cannot_promote_shadow_gate(self):
        horses = [{"number": i, "name": str(i), "marketProbability": 20-i, "aiProbability": 10.0, "odds": 2.0} for i in range(1, 6)]
        horses.append({"number": 6, "name": "候補", "marketProbability": 1.0, "aiProbability": 5.0, "odds": 20.9998})
        item = practical_candidates({"horses": horses})[0]
        self.assertEqual(item["expectedValue"], 1.05)
        self.assertFalse(item["shadowGates"]["hit_5pct_ev105"])


if __name__ == "__main__":
    unittest.main()
