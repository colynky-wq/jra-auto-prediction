from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import build_horse_profiles as builder
from horse_profile_runtime import horse_profile_id, normalize_horse_name


def history() -> pd.DataFrame:
    rows = []
    for name, date in [("アルタイル", "2026-10-04"), ("アテンド", "2026-07-05")]:
        rows.append({
            "レースID": "202605040501", "日付": pd.Timestamp(date),
            "馬番": 1, "馬名": name, "馬キー": name + "_2024",
            "年齢": 2, "斤量": 55.0, "騎手": "騎手", "調教師": "調教師",
            "着順": 2, "人気": 3, "単勝オッズ": 8.0, "芝ダ": "芝",
            "距離": 1600, "クラス": "未勝利", "障害": False,
            "馬体重": 450, "増減": 0, "頭数": 10,
        })
    return pd.DataFrame(rows)


class HorseProfileCoverageTests(unittest.TestCase):
    def test_runner_returning_from_break_is_not_lost_at_limit(self):
        with patch.object(builder, "LIMIT", 1):
            index, profiles, manifest = builder.build_profiles(history(), ["アテンド"])
        self.assertEqual({entry["name"] for entry in index}, {"アルタイル", "アテンド"})
        self.assertIn(horse_profile_id("アテンド_2024"), profiles)
        self.assertEqual(manifest["selection"]["runnerProfilesAddedBeyondLimit"], 1)
        self.assertEqual(manifest["selection"]["runnersWithoutSavedHistory"], 0)

    def test_similar_prefix_is_not_another_horses_profile(self):
        with patch.object(builder, "LIMIT", 1):
            _, _, manifest = builder.build_profiles(history(), ["アルタイルテソーロ"])
        self.assertEqual(manifest["selection"]["unmatchedRunnerNames"], ["アルタイルテソーロ"])
        self.assertEqual(manifest["selection"]["runnersWithSavedHistory"], 0)

    def test_width_and_whitespace_do_not_drop_runner(self):
        with patch.object(builder, "LIMIT", 1):
            _, _, manifest = builder.build_profiles(history(), [" ｱﾃﾝﾄﾞ　"])
        self.assertEqual(normalize_horse_name(" ｱﾃﾝﾄﾞ　"), "アテンド")
        self.assertEqual(manifest["selection"]["runnersWithSavedHistory"], 1)

    def test_new_runner_does_not_get_fabricated_history(self):
        with patch.object(builder, "LIMIT", 1):
            index, _, manifest = builder.build_profiles(history(), ["新しい馬"])
        self.assertEqual(len(index), 1)
        self.assertEqual(manifest["selection"]["unmatchedRunnerNames"], ["新しい馬"])


if __name__ == "__main__":
    unittest.main()
