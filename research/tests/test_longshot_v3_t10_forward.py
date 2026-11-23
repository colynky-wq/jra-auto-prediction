"""No-outcome-leakage and error-handling tests for frozen T10 longshot V3 research."""
import unittest
from research.longshot_v3_t10_forward import eligible, fingerprint, podium, summarize


class LongshotV3Tests(unittest.TestCase):
    def test_filter_requires_sixth_or_worse_and_minimum_ten_odds(self):
        self.assertFalse(eligible({"popularity": 5, "odds": 11}))
        self.assertFalse(eligible({"popularity": 6, "odds": 9.9}))
        self.assertTrue(eligible({"popularity": 6, "odds": 10}))

    def test_hash_deterministic_regardless_of_json_key_order(self):
        self.assertEqual(fingerprint({"b": 1, "a": 2}), fingerprint({"a": 2, "b": 1}))

    def test_podium_accepts_unplaced_null_but_rejects_missing_winner(self):
        field = [{"number": i} for i in (1, 2, 3, 4)]
        r = {"actualFinish": {"1": 3, "2": 1, "3": 2, "4": None}}
        self.assertEqual(podium(r, field), {1, 2, 3})
        r["actualFinish"]["2"] = None
        self.assertIsNone(podium(r, field))

    def test_shrinkage_only_uses_predictions(self):
        market, specialist, ai = .10, .30, .12
        row = {"horse": 8, "actual": 1, "market": market, "formalAI": ai,
               "specialistV2": specialist, "market75_v2_25": .75*market+.25*specialist,
               "market50_v2_50": .5*market+.5*specialist}
        self.assertAlmostEqual(row["market75_v2_25"], .15)
        self.assertAlmostEqual(row["market50_v2_50"], .20)
        a = summarize([{"raceID": "r", "raceDate": "2026-11-23", "rows": [row]}])
        self.assertEqual(a["market75_v2_25"]["top_pick_hits"], 1)
        self.assertLess(a["market50_v2_50"]["brier_equal_race"],
                        a["market75_v2_25"]["brier_equal_race"])
        self.assertLess(a["market75_v2_25"]["brier_equal_race"],
                        a["market"]["brier_equal_race"])

    def test_race_is_equal_weight_for_brier(self):
        small = {"horse": 7, "actual": 1, "market": .4, "formalAI": .4,
                 "specialistV2": .4, "market75_v2_25": .4, "market50_v2_50": .4}
        large = {"horse": 8, "actual": 0, "market": .1, "formalAI": .1,
                 "specialistV2": .1, "market75_v2_25": .1, "market50_v2_50": .1}
        races = [{"raceDate": "2026-11-23", "raceID": "a", "rows": [small]},
                 {"raceDate": "2026-11-23", "raceID": "b", "rows": [large]*3}]
        score = summarize(races)["market"]["brier_equal_race"]
        self.assertAlmostEqual(score, ((1-.4)**2 + .1**2)/2)


if __name__ == "__main__":
    unittest.main()
