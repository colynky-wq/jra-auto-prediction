"""Offline regression tests for capture diagnostics."""
import json
import tempfile
import unittest
from pathlib import Path
from aligned_capture_diagnostics import inspect

class DiagnosticsTests(unittest.TestCase):
    def test_unavailable_audit_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(inspect(Path(d))["status"], "blocked")

    def test_missing_capture_not_promoted_and_late_schedule_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState": "verified", "aligned": {"countsAreCurrent": True},
                "quality": {"issues": [{"code": "capture_missing_after_window",
                                        "raceDate": "2026-11-08", "raceID": "202603030201"}]}
            }))
            folder = root / "reports" / "20261108"
            folder.mkdir(parents=True)
            (folder / "PaceRecordingSchedule.json").write_text(json.dumps({
                "observedAtJST": "2026-11-08T10:52:02+09:00",
                "races": [{"raceID": "202603030201",
                           "scheduledPostJST": "2026-11-08T09:45:00+09:00"}]
            }))
            report = inspect(root)
            self.assertEqual(report["status"], "audit_verified")
            self.assertEqual(report["missingCaptureIssueCount"], 1)
            self.assertTrue(report["races"][0]["scheduleFirstObservedTooLate"])
            self.assertFalse(report["races"][0]["frozenPortfolioFilePresent"])
            self.assertEqual(report["races"][0]["missingSourceKinds"], ["win", "trio", "wideOdds"])
            self.assertEqual(report["races"][0]["savedEvents"], [])

    def test_failed_audit_never_current(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState": "failed", "aligned": {"countsAreCurrent": False},
                "quality": {"issues": []}
            }))
            self.assertEqual(inspect(root)["status"], "latest_unverified")

if __name__ == "__main__":
    unittest.main()
