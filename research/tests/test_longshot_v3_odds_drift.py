"""Regression checks for T10-vs-closing-odds diagnostic (no betting)."""
import unittest
from research.longshot_v3_odds_drift import (
    movement_bin, observed_podium, source_ages, metrics, sha_of_dict,
)


class OddsDriftTests(unittest.TestCase):
    def test_odds_drift_bins(self):
        self.assertEqual(movement_bin(10, 7), "shorten_20pct")
        self.assertEqual(movement_bin(10, 11), "stable")
        self.assertEqual(movement_bin(10, 13), "lengthen_25pct")
        self.assertEqual(movement_bin(10, 8), "stable")
        self.assertEqual(movement_bin(10, 12.5), "stable")

    def test_podium_rejects_missing_or_dead_heat(self):
        entrants = [1,2,3,4]
        self.assertEqual(observed_podium({"actualFinish":{"1":3,"2":1,"3":2,"4":None}},entrants),{1,2,3})
        self.assertIsNone(observed_podium({"actualFinish":{"1":3,"2":1,"3":None,"4":None}},entrants))
        self.assertIsNone(observed_podium({"actualFinish":{"1":3,"2":1,"3":1,"4":None}},entrants))

    def test_outcome_rank_bool_is_not_accepted(self):
        self.assertIsNone(observed_podium({"actualFinish":{"1":True,"2":2,"3":3}},[1,2,3]))

    def test_timestamp_warning_and_parse(self):
        self.assertAlmostEqual(source_ages({"officialDatetime":"2026-11-23 14:25:17",
                                             "capturedAtJST":"2026-11-23 15:18:32"}),53.25)
        self.assertIsNone(source_ages({}))

    def test_deterministic_json_fingerprint(self):
        self.assertEqual(sha_of_dict({"z":3,"a":4}),sha_of_dict({"a":4,"z":3}))

    def test_equal_race_brier(self):
        a={"raceID":"r1","number":1,"hit":1,"t10_original":.6}
        b={"raceID":"r2","number":1,"hit":0,"t10_original":.2}
        c={"raceID":"r2","number":2,"hit":0,"t10_original":.2}
        z=metrics([a,b,c],"t10_original")
        self.assertEqual(z["races"],2)
        self.assertEqual(z["top_pick_top3"],1)
        self.assertAlmostEqual(z["brier_equal_race"],(.16+.04)/2)


if __name__=="__main__":
    unittest.main()
