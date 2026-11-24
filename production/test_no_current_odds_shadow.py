"""Network-free prospective model isolation and verified result-pairing tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from market_shrink_shadow import JST, _write_first
from train_no_current_odds_challenger import FEATURES, VERSION
from no_current_odds_shadow import build, shadow_path, record_best_effort
from evaluate_no_current_odds_shadow import evaluate


class FakeBooster:
    def feature_name(self):
        return list(FEATURES)
    def predict(self, df, validate_features=True):
        assert validate_features
        assert "市場ロジット" not in df.columns
        return [0.25, 0.5, 0.25]


def fixture():
    horses = []
    for n in (1, 2, 3):
        horses.append({
            "number": n,
            "aiProbability": 35.0 if n == 1 else 32.5,
            "marketProbability": 33.4 if n == 1 else 33.3,
            "aiTop2Probability": 70.0 if n == 1 else 65.0,
            "marketTop2Probability": 67.0 if n == 1 else 66.5,
            "aiTop3Probability": 100.0,
            "marketTop3Probability": 100.0,
        })
    race = {
        "raceDate": "2026-11-23", "raceID": "202605050601",
        "startTime": "11:00", "capturedAtJST": "2026-11-23T10:50:00+09:00",
        "predictionTiming": "live_10min",
        "captureQuality": "formal_10min",
        "horses": horses,
    }
    features = {
        "featureNames": list(FEATURES),
        "horseNumbers": [1, 2, 3],
        "rows": [[float(n)]*len(FEATURES) for n in (1, 2, 3)],
    }
    meta = {
        "version": VERSION, "modelSha256": "sha-test",
        "finalRefitThrough": "2025-12-31", "features": list(FEATURES),
        "featureCount": 36, "candidatePromotionApproved": False,
    }
    audit = {
        "projectionState": "verified", "generatedAtJST": "2026-11-23T21:00:00+09:00",
        "aligned": {"countsAreCurrent": True, "racesObserved": 1,
                    "matchedResultRaces": 1},
        "quality": {"issues": [], "resultExclusions": []},
        "races": [{
            "raceDate": race["raceDate"], "raceID": race["raceID"],
            "resultState": "matched", "inputFingerprint": "bundle-fingerprint",
            "actualFinish": {"1": 2, "2": 1, "3": 3},
        }],
    }
    return race, features, meta, audit


def now():
    return datetime(2026, 11, 23, 10, 50, 10, tzinfo=JST)


class NoCurrentOddsForwardTests(unittest.TestCase):
    def test_build_first_frozen_probability_is_pure_research(self):
        race, features, meta, _ = fixture()
        original = deepcopy(race)
        row = build(race, features, meta, FakeBooster(), clock=now)
        self.assertEqual(race, original)
        self.assertFalse(row["autoBetEnabled"])
        self.assertFalse(row["candidatePromotionApproved"])
        self.assertEqual(row["status"], "prospectively_frozen_not_approved")
        self.assertEqual(row["candidateHorses"][1]["number"], 2)
        self.assertGreater(row["candidateHorses"][1]["winPct"],
                           row["candidateHorses"][0]["winPct"])

    def test_late_prediction_or_mismatched_features_do_not_create_evidence(self):
        race, features, meta, _ = fixture()
        late = deepcopy(race)
        late["capturedAtJST"] = "2026-11-23T10:54:00+09:00"
        with self.assertRaisesRegex(ValueError, "T10 window"):
            build(late, features, meta, FakeBooster(), clock=now)
        wrong = deepcopy(features)
        wrong["horseNumbers"] = [3, 2, 1]
        with self.assertRaisesRegex(ValueError, "entrant identity"):
            build(race, wrong, meta, FakeBooster(), clock=now)
        with TemporaryDirectory() as root:
            report = record_best_effort(root, race, features,
                                        first_formal_capture=False, clock=now)
            self.assertEqual(report["status"], "missing_first_capture_no_backfill")
            self.assertFalse(shadow_path(root, race).exists())

    def test_future_settlement_uses_same_original_race_and_not_hindsight(self):
        race, features, meta, audit = fixture()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            v = build(race, features, meta, FakeBooster(), clock=now)
            _write_first(shadow_path(root, race), v)
            model_report = root / "metadata.json"
            model_report.write_text(json.dumps(meta))
            with patch("evaluate_no_current_odds_shadow.REPORT_PATH", model_report):
                report = evaluate(root, audit, load_bundle=lambda *_: {
                    "fingerprint": "bundle-fingerprint", "prediction": race})
            self.assertEqual(report["status"], "insufficient_prospective_evidence")
            self.assertEqual(report["counts"]["pairedAuditedRaces"], 1)
            self.assertEqual(report["counts"]["eligibleAlignedCapturesSinceActivation"], 1)
            self.assertEqual(report["counts"]["missingCandidateShadowsSinceActivation"], 0)
            self.assertEqual(report["counts"]["candidateShadowCoveragePct"], 100.0)
            self.assertEqual(report["byEvent"]["win"]["pairedRaceUncertaintyVsFormalAI"]["pairedRaces"], 1)
            self.assertEqual(report["byEvent"]["win"]["pairedRaces"], 1)
            self.assertFalse(report["candidatePromotionApproved"])
            self.assertFalse(report["autoBetEnabled"])

    def test_missing_first_capture_is_reported_but_never_fabricated(self):
        race, features, meta, audit = fixture()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "metadata.json"
            p.write_text(json.dumps(meta))
            with patch("evaluate_no_current_odds_shadow.REPORT_PATH", p):
                report = evaluate(root, audit)
                self.assertEqual(report["counts"]["frozenShadows"], 0)
                self.assertEqual(report["counts"]["eligibleAlignedCapturesSinceActivation"], 1)
                self.assertEqual(report["counts"]["missingCandidateShadowsSinceActivation"], 1)
                self.assertEqual(report["counts"]["candidateShadowCoveragePct"], 0.0)
                self.assertEqual(report["missingCandidateRaceIDs"], [{
                    "raceDate": race["raceDate"], "raceID": race["raceID"]}])
            self.assertFalse(shadow_path(root, race).exists())

    def test_no_missing_alert_for_legacy_before_candidate_deployment(self):
        _, _, meta, audit = fixture()
        audit["races"][0]["raceDate"] = "2026-11-22"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "metadata.json"
            p.write_text(json.dumps(meta))
            with patch("evaluate_no_current_odds_shadow.REPORT_PATH", p):
                report = evaluate(root, audit)
                self.assertEqual(report["counts"]["eligibleAlignedCapturesSinceActivation"], 0)
                self.assertEqual(report["counts"]["missingCandidateShadowsSinceActivation"], 0)
                self.assertIsNone(report["counts"]["candidateShadowCoveragePct"])

    def test_failed_audit_and_unsettled_result_are_never_promoted(self):
        race, features, meta, audit = fixture()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_first(shadow_path(root, race),
                         build(race, features, meta, FakeBooster(), clock=now))
            path = root / "metadata.json"
            path.write_text(json.dumps(meta))
            with patch("evaluate_no_current_odds_shadow.REPORT_PATH", path):
                pending = deepcopy(audit)
                pending["races"][0]["resultState"] = "awaiting_result"
                pending["aligned"]["matchedResultRaces"] = 0
                report = evaluate(root, pending)
                self.assertEqual(report["counts"]["pairedAuditedRaces"], 0)
                self.assertEqual(report["counts"]["unmatchedOrNotAudited"], 1)
                bad = deepcopy(audit)
                bad["projectionState"] = "failed"
                with self.assertRaises(ValueError):
                    evaluate(root, bad)


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
