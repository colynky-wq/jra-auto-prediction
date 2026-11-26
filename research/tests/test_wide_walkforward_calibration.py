"""Regression tests for research-only wide calibration and odds freshness."""
import unittest

from research.wide_walkforward_calibration import (
    adjusted_probability,
    band,
    eligible,
    evaluate,
    fit_calibration,
    is_top3,
    odds_age_minutes,
    run_walkforward,
)


def fixture(date="2026-11-23", age=55):
    race = {"raceDate": date, "raceID": date, "_odds_age_minutes": age,
            "actualFinish": {"1": 1, "2": 3, "3": None}}
    horses = [
        {"number": 1, "popularity": 1, "aiTop3Probability": 80, "marketTop3Probability": 75},
        {"number": 2, "popularity": 7, "aiTop3Probability": 40, "marketTop3Probability": 30},
        {"number": 3, "popularity": 9, "aiTop3Probability": 15, "marketTop3Probability": 10},
    ]
    pairs = [
        {"number1": 1, "number2": 2, "aiWideProbability": 10, "wideOddsLow": 20},
        {"number1": 1, "number2": 3, "aiWideProbability": 5, "wideOddsLow": 30},
    ]
    return race, horses, pairs


class OddsFreshnessTests(unittest.TestCase):
    def test_jst_age_and_missing(self):
        valid = {"officialDatetime": "2026-11-23 14:25:17", "capturedAtJST": "2026-11-23 15:18:32"}
        self.assertAlmostEqual(odds_age_minutes(valid), 53.25)
        self.assertIsNone(odds_age_minutes({}))
        self.assertIsNone(odds_age_minutes({"officialDatetime": valid["capturedAtJST"],
                                            "capturedAtJST": valid["officialDatetime"]}))

    def test_null_finish_not_hit(self):
        self.assertTrue(is_top3({"2": 3}, 2))
        self.assertFalse(is_top3({"2": None}, 2))
        self.assertFalse(is_top3({"2": True}, 2))
        self.assertFalse(is_top3({}, 2))

    def test_all_strategies_same_freshness_gate(self):
        rows = [fixture()]
        fit = fit_calibration(rows)
        limited, omissions = evaluate(rows, fit, 30, 1.10)
        self.assertEqual(omissions["stale"], 1)
        self.assertTrue(all(v["races"] == 0 for v in limited.values()))
        ok, omissions = evaluate(rows, fit, 60, 1.10)
        self.assertEqual(omissions["stale"], 0)
        self.assertTrue(all(v["races"] == 1 for v in ok.values()))
        self.assertEqual(ok["baseline_3"]["hits"], 1)
        self.assertEqual(ok["baseline_3"]["proxy_return"], 2000)

    def test_unknown_age_rejected_even_without_limit(self):
        rows = [fixture(age=None)]
        totals, omissions = evaluate(rows, fit_calibration(rows), None, 1.10)
        self.assertEqual(omissions["missing_or_invalid_age"], 1)
        self.assertTrue(all(v["races"] == 0 for v in totals.values()))

    def test_walkforward_uses_prior_dates_only(self):
        rows = [fixture("2026-11-07"), fixture("2026-11-08"), fixture("2026-11-14")]
        result = run_walkforward(rows, min_train_days=2, cutoffs=(30, 60))
        strict, relaxed = result["cutoffs"]
        self.assertEqual(strict["summary"]["baseline_3"]["races"], 0)
        self.assertEqual(relaxed["days"][0]["training_races"], 2)
        self.assertEqual(relaxed["days"][0]["loaded_test_races"], 1)
        self.assertEqual(relaxed["summary"]["baseline_3"]["races"], 1)

    def test_shrinkage_falls_back_to_original_when_no_training_pairs(self):
        pair = {"aiWideProbability": 4}
        self.assertAlmostEqual(adjusted_probability(pair, [[0, 0, 0] for _ in range(4)]), 0.04)
        self.assertEqual(band(100), 3)


if __name__ == "__main__":
    unittest.main()
