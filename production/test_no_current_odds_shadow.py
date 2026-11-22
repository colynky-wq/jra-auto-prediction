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
            self.assertEqual(report["byEvent"]["win"]["pairedRaces"], 1)
            self.assertFalse(report["candidatePromotionApproved"])
            self.assertFalse(report["autoBetEnabled"])

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


if __name__ == "__main__":
    unittest.main()
