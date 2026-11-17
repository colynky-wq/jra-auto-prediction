"""Regression tests for read-only odds provenance and wide-reference audit."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from odds_integrity_audit import (
    age_minutes, audit_repository, audit_snapshot, audit_wide_references,
    compare_early_and_formal,
)


class OddsIntegrityAuditTests(unittest.TestCase):
    def setUp(self):
        self.record = {"prediction": {
            "raceID": "test-01", "raceDate": "2026-11-15",
            "startTime": "12:00",
            "horses": [{"number": 1}, {"number": 2}, {"number": 3}],
        }}
        self.snapshot = {
            "raceID": "test-01", "raceDate": "2026-11-15",
            "startTime": "12:00", "capturedAtJST": "2026-11-15 11:50:00",
            "officialDatetime": "2026-11-15 11:48:00",
            "apiStatus": "middle", "betType": "ワイド", "apiType": 5,
            "frozenAtFirstSuccessfulCapture": True,
            "pairCount": 3,
            "pairs": [
                {"number1": 1, "number2": 2, "oddsLow": 2.0, "oddsHigh": 3.0},
                {"number1": 1, "number2": 3, "oddsLow": 6.0, "oddsHigh": 8.0},
                {"number1": 2, "number2": 3, "oddsLow": 7.0, "oddsHigh": 9.0},
            ],
        }

    def test_separate_snapshots_same_quotes_detected(self):
        early = json.loads(json.dumps(self.snapshot))
        early["capturedAtJST"] = "2026-11-15 11:28:00"
        result = compare_early_and_formal(early, self.snapshot)
        self.assertEqual(result["status"], "unchanged_across_separate_captures")
        self.assertEqual(result["changedPairs"], 0)
        self.assertEqual(result["pairsCompared"], 3)
        self.assertTrue(result["sameSourceTimestamp"])

    def test_early_and_formal_price_change_with_source_timestamp(self):
        early = json.loads(json.dumps(self.snapshot))
        early["capturedAtJST"] = "2026-11-15 11:28:00"
        early["officialDatetime"] = "2026-11-15 11:25:00"
        early["pairs"][0]["oddsLow"] = 4.0
        early["pairs"][0]["oddsHigh"] = 4.5
        result = compare_early_and_formal(early, self.snapshot)
        self.assertEqual(result["status"], "changed_across_separate_captures")
        self.assertEqual(result["changedPairs"], 1)
        self.assertFalse(result["sameSourceTimestamp"])

    def test_same_capture_not_counted_as_independent_observation(self):
        early = json.loads(json.dumps(self.snapshot))
        result = compare_early_and_formal(early, self.snapshot)
        self.assertEqual(result["status"], "same_or_near_capture")

    def test_valid_snapshot_has_no_alerts(self):
        result = audit_snapshot(self.snapshot, self.record)
        self.assertEqual(result["alerts"], [])
        self.assertEqual(result["sourceAgeMinutes"], 2.0)
        self.assertEqual(result["minutesBeforePost"], 10.0)
        self.assertFalse(result["oddsStalenessProven"])

    def test_old_source_timestamp_is_alert_not_proven_stale_odds(self):
        self.snapshot["officialDatetime"] = "2026-11-15 10:57:00"
        result = audit_snapshot(self.snapshot, self.record)
        self.assertIn("source_timestamp_age_alert", result["alerts"])
        self.assertEqual(result["sourceAgeMinutes"], 53.0)
        self.assertEqual(result["sourceTimestampSemantics"], "UNVERIFIED")
        self.assertFalse(result["oddsStalenessProven"])

    def test_duplicate_pair_detected(self):
        self.snapshot["pairs"][2] = dict(self.snapshot["pairs"][0])
        result = audit_snapshot(self.snapshot, self.record)
        self.assertIn("duplicate_pair", result["alerts"])
        self.assertIn("incomplete_or_extra_pairs", result["alerts"])

    def test_popular_pair_odds_anomaly_is_research_warning(self):
        for h, popularity in zip(self.record["prediction"]["horses"], [1, 2, 3]):
            h["popularity"] = popularity
        self.snapshot["pairs"][0]["oddsLow"] = 20.0
        self.snapshot["pairs"][0]["oddsHigh"] = 23.0
        result = audit_snapshot(self.snapshot, self.record)
        self.assertIn("favorite_pair_wide_price_alert", result["alerts"])
        self.assertEqual(result["favoritePairPriceOutliers"][0]["pair"], "0102")
        self.assertFalse(result["oddsStalenessProven"])

    def test_invalid_odds_detected(self):
        self.snapshot["pairs"][0]["oddsLow"] = 8.0
        self.snapshot["pairs"][0]["oddsHigh"] = 3.0
        self.assertIn("invalid_odds_range", audit_snapshot(self.snapshot, self.record)["alerts"])

    def test_race_identity_checked(self):
        self.snapshot["raceID"] = "another-race"
        self.assertIn("race_identity_mismatch", audit_snapshot(self.snapshot, self.record)["alerts"])

    def test_offset_aware_and_naive_jst_dates(self):
        self.assertEqual(
            age_minutes("2026-11-15T11:50:00+09:00", "2026-11-15 10:57:00"),
            53.0,
        )
        self.assertEqual(
            age_minutes("2026-11-15T02:50:00Z", "2026-11-15 10:57:00"),
            53.0,
        )

    def test_wide_reference_calibration_and_finish_matching(self):
        cohort = {"races": [{
            "raceDate": "2026-11-15", "raceID": "test-01",
            "resultState": "matched", "referenceSettled": True,
            "actualFinish": {"1": 1, "2": 3, "3": 2},
            "commonWideReference": [{
                "candidateID": "0102", "betType": "wide",
                "probabilityPct": 10.0, "oddsLow": 16.0,
                "expectedMultiplierLow": 1.6, "hit": True,
                "unitYen": 100, "payoutYen": 500,
            }],
        }]}
        result = audit_wide_references(cohort)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["bins"]["1.50+"]["tickets"], 1)
        self.assertEqual(result["bins"]["1.50+"]["hits"], 1)
        self.assertEqual(result["bins"]["1.50+"]["roiPct"], 500)

    def test_expected_value_mismatch_detected(self):
        cohort = {"races": [{
            "raceDate": "2026-11-15", "raceID": "test-01",
            "resultState": "matched", "referenceSettled": True,
            "actualFinish": {"1": 1, "2": 2, "3": 3},
            "commonWideReference": [{
                "candidateID": "0102", "probabilityPct": 10,
                "oddsLow": 16, "expectedMultiplierLow": 6.17,
                "hit": True, "unitYen": 100, "payoutYen": 1000,
            }],
        }]}
        result = audit_wide_references(cohort)
        self.assertIn(
            {"raceID": "test-01", "type": "expected_value_inconsistent"},
            result["errors"],
        )

    def test_fails_closed_when_aligned_counts_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState": "failed",
                "aligned": {"countsAreCurrent": False}, "races": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not verified"):
                audit_repository(Path(td))


if __name__ == "__main__":
    unittest.main()
