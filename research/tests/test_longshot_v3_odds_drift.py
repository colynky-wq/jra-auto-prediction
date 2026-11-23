"""Regression checks for T10-vs-closing-odds diagnostic (no betting)."""
import unittest
from research.longshot_v3_odds_drift import (
    movement_bin, observed_podium, source_ages, metrics, sha_of_dict, win_quote_age_minutes,
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

    def test_original_win_quote_age_and_linkage(self):
        original = {"capturedAtJST": "2026-11-23T15:18:32.211366+09:00", "horses": []}
        odds = {
            "betType": "win",
            "sourceURL": "https://race.netkeiba.com/api/api_get_jra_odds.html?race_id=1",
            "prediction": original,
            "officialDatetime": "2026-11-23 14:25:17",
            "requestStartedAtJST": "2026-11-23T15:18:32.486747+09:00",
            "receivedAtJST": "2026-11-23T15:18:32.705148+09:00",
        }
        self.assertAlmostEqual(win_quote_age_minutes(odds, original), 53.261752, places=4)
        with self.assertRaises(ValueError):
            win_quote_age_minutes(odds, {"capturedAtJST": original["capturedAtJST"], "horses": [1]})
        odds["officialDatetime"] = "2026-11-23 16:25:17"
        with self.assertRaises(ValueError):
            win_quote_age_minutes(odds, original)

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
