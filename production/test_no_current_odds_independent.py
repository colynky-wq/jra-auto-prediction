"""Offline tests for the independent T10 research runtime."""
from test_no_current_odds_shadow import fixture, now, FakeBooster
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import unittest
from market_shrink_shadow import JST
from no_current_odds_shadow import shadow_path
from train_no_current_odds_challenger import FEATURES

class IndependentCaptureTests(unittest.TestCase):
    def test_features_without_odds_match_known_current_market_features(self):
        import numpy as np
        import pandas as pd
        import live_monitor_dry_run as base
        from no_current_odds_independent import feature_matrix
        from train_no_current_odds_challenger import FEATURES
        from unittest.mock import patch

        day = pd.Timestamp("2026-11-23")
        race = {"race_id": "202605050601", "surface": "芝",
                "distance": 1600, "class": "1勝"}
        entries = pd.DataFrame({"馬番": [1, 2, 3], "馬名": ["a", "b", "c"],
            "年齢": [3, 3, 3], "斤量": [55., 56., 57.], "騎手_JRA": ["j"]*3,
            "調教師_JRA": ["t"]*3, "馬体重": [450., 460., 470.], "増減": [0., 2., -2.],
            "単勝オッズ": [2., 4., 6.], "人気": [1., 2., 3.]})
        hist = entries.rename(columns={"騎手_JRA": "騎手", "調教師_JRA": "調教師"}).copy()
        hist["馬キー"] = hist["馬名"]
        for key, val in {"レースID": "202605050501", "日付": day-pd.Timedelta(days=21),
            "芝ダ": "芝", "距離": 1600, "クラス": "1勝", "障害": False, "頭数": 3,
            "出典": "history"}.items():
            hist[key] = val
        hist["着順"] = [1., 2., 3.]
        hist["タイム秒"] = [95., 96., 97.]
        hist["上がり3F"] = [34., 35., 36.]
        hist["初角"] = [1., 2., 3.]
        cur = hist.copy()
        cur["日付"], cur["レースID"] = day, race["race_id"]
        cur["出典"] = "GitHubライブ監視Top2Top3ドライラン"
        for c in ["着順", "タイム秒", "上がり3F", "初角"]:
            cur[c] = np.nan
        full = base.add_features(base.add_rating(pd.concat([hist, cur], ignore_index=True)))
        expected = full.loc[full["日付"] == day, list(FEATURES)].astype(np.float32)
        with patch("no_current_odds_independent.assign_horse_keys",
                   side_effect=lambda entries, *_: entries["馬名"]):
            actual, nums = feature_matrix(race, entries, hist, day)
            changed = entries.assign(単勝オッズ=[99., 1., 10.], 人気=[3., 1., 2.])
            second, _ = feature_matrix(race, changed, hist, day)
        self.assertEqual(nums, [1, 2, 3])
        np.testing.assert_allclose(actual[list(FEATURES)].values, expected.values)
        np.testing.assert_array_equal(actual.values, second.values)
        self.assertNotIn("市場ロジット", actual.columns)
        with self.assertRaisesRegex(ValueError, "history must strictly precede"):
            feature_matrix(race, entries, cur, day)

    def test_independent_record_separate_and_rejects_late(self):
        import pandas as pd
        from no_current_odds_independent import build, archive_path
        race, features, meta, _ = fixture()
        day = pd.Timestamp(race["raceDate"])
        r = {"race_id": race["raceID"], "start_time": race["startTime"]}
        entries = pd.DataFrame({"馬番": [1, 2, 3], "馬名": ["a", "b", "c"], "年齢": [3]*3})
        evidence = {"raceID": race["raceID"], "raceDate": race["raceDate"],
            "scheduledPostJST": "2026-11-23T11:00:00+09:00",
            "capturedAtJST": race["capturedAtJST"], "sourceSHA256": "a"*64,
            "runners": [{"number": n, "name": name, "age": 3, "withdrawn": False}
                        for n, name in zip([1, 2, 3], ["a", "b", "c"])]}
        X = pd.DataFrame(features["rows"], columns=list(FEATURES))
        X["レースID"] = race["raceID"]
        with patch("no_current_odds_independent.feature_matrix", return_value=(X, [1,2,3])):
            value = build(r, entries, evidence, None, day, meta, FakeBooster(), clock=now)
            self.assertFalse(value["currentOddsUsed"])
            self.assertFalse(value["formalRecommendationsEnabled"])
            self.assertFalse(value["autoBetEnabled"])
            self.assertNotEqual(archive_path(Path("."), race["raceDate"], race["raceID"]),
                                shadow_path(Path("."), race))
            with self.assertRaisesRegex(ValueError, "window"):
                build(r, entries, evidence, None, day, meta, FakeBooster(),
                      clock=lambda: datetime(2026,11,23,10,53,tzinfo=JST))
            with self.assertRaisesRegex(ValueError, "roster"):
                build(r, entries.iloc[:2], evidence, None, day, meta, FakeBooster(), clock=now)


if __name__ == "__main__":
    unittest.main()
