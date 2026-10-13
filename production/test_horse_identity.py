from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from horse_identity_runtime import assign_horse_keys, birth_year, history_key_index, resolve_runner_key
from build_app_research_data import build_races
from pace_prospective_runtime import field_features
from repair_horse_identity import correct_verified_row
from test_pace_prospective import fixture
from test_prerace_snapshot import ENTRIES, HTML, T, URL, RID, DAY
from prerace_snapshot_runtime import parse_snapshot


class HorseIdentityTests(unittest.TestCase):
    def test_debut_does_not_take_older_namesake_key(self):
        history = pd.DataFrame({"馬名": ["アインザッツ"], "馬キー": ["アインザッツ_2013"]})
        entries = pd.DataFrame({"馬名": ["アインザッツ"], "年齢": [2]})
        self.assertEqual(assign_horse_keys(entries, history, "2026-10-11"), ["アインザッツ_2024"])

    def test_correct_generation_wins_even_if_another_generation_is_later(self):
        history = pd.DataFrame({"馬名": ["テスト", "テスト"], "馬キー": ["テスト_2022", "テスト_2013"]})
        entries = pd.DataFrame({"馬名": ["テスト"], "年齢": [4]})
        self.assertEqual(assign_horse_keys(entries, history, "2026-10-17"), ["テスト_2022"])

    def test_normalization_preserves_existing_key_and_never_uses_similar_name(self):
        history = pd.DataFrame({"馬名": ["テ ス ト", "テストテソーロ"], "馬キー": ["テ ス ト_2024", "テストテソーロ_2024"]})
        entries = pd.DataFrame({"馬名": ["テスト", "テスト２"], "年齢": [2, 2]})
        self.assertEqual(assign_horse_keys(entries, history, "2026-10-17"), ["テ ス ト_2024", "テスト2_2024"])

    def test_birth_year_stays_same_across_calendar_year(self):
        self.assertEqual(birth_year("2025-12-31", 2), birth_year("2026-01-01", 3))
        for age in [None, True, 0, -1, 2.5, float("nan"), float("inf")]:
            with self.assertRaises(ValueError): birth_year("2026-10-17", age)

    def test_ambiguous_aliases_do_not_silently_choose_a_key(self):
        history = pd.DataFrame({"馬名": ["テ ス ト", "テスト"], "馬キー": ["テ ス ト_2024", "テスト_2024"]})
        with self.assertRaises(ValueError): history_key_index(history)

    def test_missing_or_conflicting_runner_identity_is_not_name_only_matching(self):
        keys = {("テスト", 2013): "テスト_2013"}
        self.assertIsNone(resolve_runner_key({"name": "テスト"}, "2026-10-17", keys))
        with self.assertRaises(ValueError):
            resolve_runner_key({"name": "テスト", "age": 2, "horseKey": "テスト_2013"}, "2026-10-17", keys)

    def test_prospective_debut_has_zero_known_history_and_no_past_lap_borrowing(self):
        history, laps, observation, _, _ = fixture()
        for runner in observation["runners"]: runner["age"] = 2
        features = field_features(observation, history, laps)
        self.assertEqual(features["knownStyleRunners"], 0)
        self.assertEqual(features["pastLapCoverage"], 0)
        self.assertIsNone(features["latestSourceDay"])

    def test_prospective_identity_never_comes_from_target_day_results(self):
        history, laps, observation, _, _ = fixture()
        for runner in observation["runners"]: runner.pop("age")
        extra = history.iloc[-3:].copy().assign(日付=observation["raceDate"], 年齢=4)
        self.assertEqual(field_features(observation, history, laps), field_features(observation, pd.concat([history, extra]), laps))
        self.assertEqual(field_features(observation, history, laps)["knownStyleRunners"], 0)

    def test_display_debut_does_not_borrow_old_generation(self):
        from test_app_research_data import past, race
        history = past([("2016-01-01", "テスト", "テスト_2013", 1)] * 3)
        result = build_races(history, [race()])[0]
        self.assertIsNone(result["horses"][0]["styleTendency"])
        self.assertEqual(result["formation"]["knownHorses"], 0)

    def test_legacy_display_recovers_exact_entrant_age_only(self):
        from test_app_research_data import past, race
        old = past([("2016-01-01", "テスト", "テスト_2013", 1)] * 3).assign(馬番=1, 年齢=3)
        current = past([("2025-01-02", "テスト", "テスト_2022", 10)]).assign(馬番=1, 年齢=3)
        current["レースID"] = race()["raceID"]
        legacy = race(); legacy["horses"][0].pop("age")
        result = build_races(pd.concat([old, current]), [legacy])[0]["horses"][0]
        self.assertIsNone(result["styleTendency"])
        current["初角"] = 1
        self.assertEqual(build_races(pd.concat([old, current]), [legacy])[0]["horses"][0], result)
        current["馬番"] = 2
        self.assertIsNone(build_races(pd.concat([old, current]), [legacy])[0]["horses"][0]["styleTendency"])

    def test_snapshot_keeps_official_age_and_rejects_parser_disagreement(self):
        result = parse_snapshot(HTML, RID, DAY, URL, T, ENTRIES)
        self.assertEqual(result["runners"][0]["age"], 2)
        self.assertEqual(result["runners"][0]["horseKey"], "テスト馬A_2024")
        with self.assertRaises(ValueError):
            parse_snapshot(HTML, RID, DAY, URL, T, ENTRIES.assign(年齢=3))

    def test_verified_correction_changes_one_cell_and_is_idempotent(self):
        history = pd.DataFrame({"レースID": ["202605040405", "201605010101"], "日付": pd.to_datetime(["2026-10-11", "2016-01-01"]),
                                "馬名": ["アインザッツ"] * 2, "馬キー": ["アインザッツ_2013"] * 2,
                                "年齢": [2, 3], "出典": ["JRA公式更新", "元履歴"], "初角": [3., 1.]})
        corrected, changed, _ = correct_verified_row(history)
        self.assertTrue(changed)
        pd.testing.assert_frame_equal(history.drop(columns="馬キー"), corrected.drop(columns="馬キー"))
        self.assertEqual(corrected["馬キー"].tolist(), ["アインザッツ_2024", "アインザッツ_2013"])
        again, changed, _ = correct_verified_row(corrected)
        self.assertFalse(changed); pd.testing.assert_frame_equal(again, corrected)
        with self.assertRaises(ValueError): correct_verified_row(pd.concat([history, history.iloc[:1]], ignore_index=True))
        with self.assertRaises(ValueError): correct_verified_row(history.assign(馬キー="予期しないキー"))

    def test_actual_result_update_uses_new_birth_year(self):
        import update_production_history as updater
        history = pd.DataFrame({"馬名": ["アインザッツ"], "馬キー": ["アインザッツ_2013"], "騎手": ["騎手"], "調教師": ["調教師"]})
        row = {column: 1 for column in updater.BASE_COLS}
        row.update(レースID="202605040405", 馬名="アインザッツ", 年齢=2, 出典="JRA公式更新", 芝ダ="芝", クラス="新馬", 障害=False, 騎手_JRA="騎手", 調教師_JRA="調教師", 結果状態="確定")
        with patch.object(updater, "fetch_completed_race", return_value=pd.DataFrame([row])), contextlib.redirect_stdout(io.StringIO()):
            result = updater.build_target_rows(history, [row["レースID"]], "20261011")
        self.assertEqual(result["馬キー"].tolist(), ["アインザッツ_2024"])

    def test_both_live_paths_start_new_namesake_without_old_ratings(self):
        import live_monitor_dry_run as live
        import live_monitor_topk as topk
        from feature_engine import add_rating

        history = pd.DataFrame({"レースID": ["201605010101"], "日付": pd.to_datetime(["2016-01-01"]),
                                "馬番": [1], "馬名": ["アインザッツ"], "馬キー": ["アインザッツ_2013"],
                                "騎手": ["騎手"], "調教師": ["調教師"], "着順": [1.]})
        entries = pd.DataFrame({"馬番": [1, 2], "馬名": ["アインザッツ", "別の新馬"], "年齢": [2, 2],
                                "斤量": [55., 55.], "騎手_JRA": ["騎手"] * 2, "調教師_JRA": ["調教師"] * 2,
                                "馬体重": [480., 480.], "増減": [0., 0.]})
        odds = pd.DataFrame({"馬番": [1, 2], "単勝オッズ": [2., 3.]})
        race = dict(race_id="202605040405", venue="東京", race_no=5, surface="芝", distance=1600, race_name="新馬", **{"class": "新馬"})

        class CheckedBeforeModel(RuntimeError): pass

        def verify_ratings(work):
            work = add_rating(work)
            current = work[work["レースID"] == race["race_id"]]
            self.assertEqual(current["馬キー"].tolist(), ["アインザッツ_2024", "別の新馬_2024"])
            self.assertEqual(current["出走数"].tolist(), [0., 0.])
            self.assertEqual(current["能力点数"].tolist(), [1500., 1500.])
            raise CheckedBeforeModel()

        for predict in [live.predict_live_race, topk.predict_live_race_topk]:
            with self.subTest(predict=predict.__name__), patch.object(live, "fetch_meta", return_value=race), patch.object(live, "fetch_win_odds", return_value=(odds, None)), patch.object(live, "fetch_jra_entry_table", return_value=(entries.copy(), T, URL)), patch.object(live, "add_rating", side_effect=verify_ratings), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(CheckedBeforeModel): predict(race, history, pd.Timestamp("2026-10-11"))

    def test_publisher_retains_identity_in_future_racedata(self):
        import live_monitor_topk_publish as publisher
        row = {key: 1. for key in ["単勝オッズ", "市場勝率(%)", "AI勝率(%)", "市場2着内率(%)", "AI2着内率(%)", "市場3着内率(%)", "AI3着内率(%)", "AI÷市場", "期待払戻指数"]}
        row.update(馬番=1, 馬名="アインザッツ", 年齢=2, 馬キー="アインザッツ_2024")
        race = dict(race_id="202605040405", venue="東京", race_no=5, start_time="12:00", surface="芝", distance=1600)
        with tempfile.TemporaryDirectory() as root, patch.object(publisher, "RACEDATA_PATH", Path(root) / "RaceData.json"), patch.object(publisher.base, "TARGET_DATE8", "20261011"), patch.object(publisher, "_refresh_prediction_bundle"), contextlib.redirect_stdout(io.StringIO()):
            publisher.publish_race_to_racedata(race, pd.DataFrame([row]))
            horse = json.loads(publisher.RACEDATA_PATH.read_text())[0]["horses"][0]
        self.assertEqual((horse["age"], horse["horseKey"]), (2, "アインザッツ_2024"))


if __name__ == "__main__": unittest.main()
