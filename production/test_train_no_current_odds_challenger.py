"""Fast network-free identity, leakage and probability tests for the challenger."""
from __future__ import annotations
from copy import deepcopy
import unittest
import numpy as np
import pandas as pd
from train_no_current_odds_challenger import (
    FEATURES, normalized_win, probability_triplet, verify_features, split_years,
)


class FakeBooster:
    def __init__(self):
        self.calls = 0
    def feature_name(self):
        return list(FEATURES)
    def predict(self, frame, validate_features):
        assert validate_features is True
        assert "市場ロジット" not in frame.columns
        self.calls += 1
        return np.asarray([0.2, 0.5, 0.3, 0.1, 0.6, 0.3])


class NoCurrentOddsTests(unittest.TestCase):
    def sample(self):
        d = pd.DataFrame(np.ones((6, len(FEATURES))),
                         columns=list(FEATURES))
        d["レースID"] = ["A"] * 3 + ["B"] * 3
        d["馬番"] = [1, 2, 3, 1, 2, 3]
        d["日付"] = [pd.Timestamp("2025-03-01")] * 6
        return d

    def test_exact_features_exclude_current_market_even_when_present(self):
        self.assertEqual(len(FEATURES), 36)
        self.assertNotIn("市場ロジット", FEATURES)
        self.assertIn("前走人気f", FEATURES)
        d = self.sample()
        d["市場ロジット"] = [1000, -1000, 0, 10, -20, 40]
        d["単勝オッズ"] = [1.1, 5, 10, 2, 3, 15]
        f = verify_features(d)
        self.assertEqual(list(f.columns), list(FEATURES))
        self.assertNotIn("市場ロジット", f.columns)
        self.assertNotIn("単勝オッズ", f.columns)

    def test_bad_feature_and_model_identity_are_rejected(self):
        d = self.sample()
        d.loc[0, "点数差"] = np.nan
        with self.assertRaisesRegex(ValueError, "invalid pre-race feature"):
            verify_features(d)
        d = self.sample()
        d.drop(columns=[FEATURES[-1]], inplace=True)
        with self.assertRaisesRegex(ValueError, "missing pre-race features"):
            verify_features(d)

    def test_triplet_is_race_normalized_without_market_input(self):
        d = self.sample()
        predictor = FakeBooster()
        first = probability_triplet(d, predictor)
        d["市場ロジット"] = [100000, -100000, 0, 20, 30, -40]
        second = probability_triplet(d, predictor)
        self.assertEqual(predictor.calls, 2)
        for key, k in (("win", 1), ("top2", 2), ("top3", 3)):
            np.testing.assert_allclose(first[key], second[key])
            self.assertAlmostEqual(first[key][:3].sum(), k, places=9)
            self.assertAlmostEqual(first[key][3:].sum(), k, places=9)
        self.assertTrue(np.all(first["win"] <= first["top2"]))
        self.assertTrue(np.all(first["top2"] <= first["top3"]))

    def test_invalid_raw_probabilities_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid raw prediction"):
            normalized_win([0.0, float("nan"), 0.2], ["A"] * 3)
        with self.assertRaisesRegex(ValueError, "prediction size mismatch"):
            normalized_win([0.2], ["A", "A"])

    def test_chronological_split_never_uses_2026_for_fit(self):
        d = pd.DataFrame({
            "日付": pd.to_datetime(["2023-01-01"] * 5001 +
                                    ["2024-01-01"] * 5001 +
                                    ["2025-01-01"] * 5001 +
                                    ["2026-01-01"] * 5001),
            "レースID": ["A"] * 5001 + ["B"] * 5001 +
                        ["C"] * 5001 + ["D"] * 5001,
        })
        tr, valid, holdout = split_years(d)
        self.assertEqual(len(tr), 5001)
        self.assertEqual(len(valid), 5001)
        self.assertEqual(len(holdout), 5001)
        self.assertLess(holdout["日付"].max(), pd.Timestamp("2026-01-01"))


if __name__ == "__main__":
    unittest.main()
