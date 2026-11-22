"""Causal-history, selection-boundary and paired-cohort regression tests."""
import unittest

import numpy as np
import pandas as pd

from research_longshot_top3_v2 import (
    FEATURES, FEATURE_SETS, PLACE_FEATURES, add_extras, evaluate,
    freeze_thresholds, matrix, place_history, predict, select_validation,
    top_picks,
)
from test_train_longshot_top3 import sample
from train_longshot_top3 import prepare_longshot


def history_sample():
    rows = []
    for day, rank in ((1, 2), (2, 8), (3, 3), (4, np.nan)):
        rows.append({"レースID": "R"+str(day), "馬番": 1, "馬キー": "H1",
                     "日付": pd.Timestamp(2024, 1, day), "芝ダ": "芝",
                     "距離": 1600, "クラス": "1勝", "着順": rank,
                     "頭数": 10, "障害": False})
    return pd.DataFrame(rows)


class FakeModel:
    def __init__(self, features):
        self.features = features
    def feature_name(self):
        return list(self.features)
    def predict(self, frame, **kwargs):
        assert list(frame) == list(self.features)
        return np.repeat(.1, len(frame))


class PlaceHistoryTests(unittest.TestCase):
    def test_mutating_current_or_future_results_cannot_change_features(self):
        h = history_sample()
        before = place_history(h)
        changed = h.copy()
        changed.loc[changed["日付"] >= pd.Timestamp(2024, 1, 3), "着順"] = 8
        after = place_history(changed)
        pd.testing.assert_frame_equal(before.iloc[:3], after.iloc[:3])
        self.assertNotEqual(before.iloc[3]["placeLast3V2"], after.iloc[3]["placeLast3V2"])

    def test_same_date_results_are_excluded_until_next_date(self):
        h = history_sample()
        second = h.iloc[[0]].copy()
        second["レースID"] = "SAME_DAY_SECOND_RACE"
        second["着順"] = 1
        result = place_history(pd.concat([h, second], ignore_index=True))
        firstday = result[result["レースID"].isin(["R1", "SAME_DAY_SECOND_RACE"])]
        self.assertEqual(len(firstday), 2)
        np.testing.assert_allclose(firstday[list(PLACE_FEATURES)].iloc[0],
                                   firstday[list(PLACE_FEATURES)].iloc[1])

    def test_missing_and_jump_results_do_not_become_losses(self):
        h = history_sample()
        h.loc[h["レースID"] == "R2", "障害"] = True
        h.loc[h["レースID"] == "R3", "着順"] = np.nan
        result = place_history(h)
        # Only the first valid flat race is in the history at race four.
        self.assertAlmostEqual(result.iloc[-1]["placeLast5V2"], 1.42/3)
        self.assertAlmostEqual(result.iloc[-1]["placeHistoryLogV2"], np.log(2))

    def test_distance_and_surface_history_remain_separate(self):
        h = history_sample()
        h.loc[h["レースID"] == "R2", "芝ダ"] = "ダート"
        h.loc[h["レースID"] == "R2", "距離"] = 1200
        result = place_history(h)
        self.assertAlmostEqual(result.iloc[2]["placeSurfaceV2"], 1.42/3)
        self.assertAlmostEqual(result.iloc[2]["placeDistanceV2"], 1.42/3)

    def test_shuffling_source_does_not_change_identity_bound_features(self):
        h = history_sample()
        pd.testing.assert_frame_equal(place_history(h),
                                      place_history(h.sample(frac=1, random_state=2)))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            place_history(pd.concat([h, h.iloc[[0]]]))


class SelectionTests(unittest.TestCase):
    def data(self, year=2024):
        d = prepare_longshot(sample(year))
        for feature in PLACE_FEATURES:
            d[feature] = .21
        d["marketTop3LogitV2"] = -2.
        d["logWinOddsV2"] = np.log(d["単勝オッズ"])
        d["popularityFractionV2"] = d["人気"]/d["頭数"]
        d["馬名"] = "horse"
        return d

    def test_predictions_do_not_access_results_or_raw_history(self):
        d = self.data().drop(columns=["着順", "馬名", "日付"])
        features = FEATURE_SETS["place_and_market"]
        p = predict(d, FakeModel(features), features, 1.)
        self.assertTrue(np.isfinite(p).all())
        with self.assertRaisesRegex(ValueError, "identity"):
            predict(d, FakeModel(FEATURES), features, 1.)

    def test_2025_and_2026_cannot_select_features_weights_or_thresholds(self):
        for year in (2025, 2026):
            d = self.data(year)
            with self.assertRaisesRegex(ValueError, "2024 only"):
                select_validation(d, [("control36", FakeModel(FEATURES), 10)])
            with self.assertRaisesRegex(ValueError, "2024 only"):
                freeze_thresholds(d, np.repeat(.2, len(d)))

    def test_missing_feature_and_unknown_feature_set_fail_closed(self):
        d = self.data()
        with self.assertRaisesRegex(ValueError, "missing"):
            matrix(d.drop(columns=[PLACE_FEATURES[0]]), FEATURE_SETS["place_history"])
        with self.assertRaisesRegex(ValueError, "unregistered"):
            matrix(d, ("着順",))

    def test_picks_use_horse_number_for_ties_never_finish(self):
        d = self.data()
        picks = top_picks(d, np.repeat(.2, len(d)))
        self.assertEqual(picks["馬番"].tolist(), [6, 6])
        changed = d.copy()
        changed["着順"] = np.arange(len(changed))[::-1]
        other = top_picks(changed, np.repeat(.2, len(d)))
        self.assertEqual(picks["馬番"].tolist(), other["馬番"].tolist())

    def test_abstention_compares_identical_races_and_no_target_leakage(self):
        d = self.data()
        p = np.array([.4, .1, .05, .2, .1, .05])
        _, m = d["着順"], d["市場3着内基準"].to_numpy()
        r, _ = evaluate(d, p, m, {"1.0": 0., "0.5": .3})
        self.assertEqual(r["races"], 2)
        self.assertEqual(r["abstentionPolicies"]["0.5"]["selectedRaces"], 1)
        self.assertEqual(r["abstentionPolicies"]["0.5"]["methods"]["market"]["top3Hits"], 1)


if __name__ == "__main__":
    unittest.main()
