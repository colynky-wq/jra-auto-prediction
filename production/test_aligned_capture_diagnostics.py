"""Offline regression tests for capture diagnostics."""
import json
import tempfile
import unittest
from pathlib import Path
from aligned_capture_diagnostics import inspect

from datetime import datetime, timedelta


def uncovered_formal_windows(session_start, max_minutes, posts):
    """Offline scheduling check; does not run or reconstruct captures."""
    session_end = session_start + timedelta(minutes=max_minutes)
    return [(rid, post - timedelta(minutes=12), post - timedelta(minutes=8))
            for rid, post in posts.items()
            if post - timedelta(minutes=12) >= session_end]


class DiagnosticsTests(unittest.TestCase):
    def test_nov8_session_305_minutes_leaves_both_last_races_uncovered(self):
        jst = "+09:00"
        start = datetime.fromisoformat("2026-11-08T10:52:00" + jst)
        posts = {
            "202605050212": datetime.fromisoformat("2026-11-08T16:10:00" + jst),
            "202608050212": datetime.fromisoformat("2026-11-08T16:25:00" + jst),
        }
        uncovered = uncovered_formal_windows(start, 305, posts)
        self.assertEqual([row[0] for row in uncovered],
                         ["202605050212", "202608050212"])
        self.assertEqual(uncovered[0][1].strftime("%H:%M"), "15:58")
        self.assertEqual(uncovered[1][1].strftime("%H:%M"), "16:13")
        self.assertEqual(uncovered_formal_windows(start, 340, posts), [])


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
            self.assertTrue(report["races"][0]["scheduleSnapshotAfterT10Window"])
            self.assertFalse(report["races"][0]["frozenPortfolioFilePresent"])
            self.assertEqual(report["races"][0]["missingSourceKinds"], ["win", "trio", "wideOdds"])
            self.assertEqual(report["races"][0]["savedEvents"], [])

    def test_schedule_before_post_but_after_t10_window(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState": "verified", "aligned": {"countsAreCurrent": True},
                "quality": {"issues": [{"code": "capture_missing_after_window",
                                       "raceDate": "2026-11-08", "raceID": "202605050203"}]}
            }))
            folder = root / "reports" / "20261108"
            folder.mkdir(parents=True)
            (folder / "PaceRecordingSchedule.json").write_text(json.dumps({
                "observedAtJST": "2026-11-08T10:52:02+09:00",
                "races": [{"raceID": "202605050203",
                           "scheduledPostJST": "2026-11-08T10:55:00+09:00"}]
            }))
            row = inspect(root)["races"][0]
            self.assertFalse(row["scheduleFirstObservedTooLate"])
            self.assertTrue(row["scheduleSnapshotAfterT10Window"])
            self.assertEqual(row["classification"], "schedule_snapshot_after_t10_window")

    def test_post_window_monitor_missed_does_not_claim_collector_cause(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState": "verified", "aligned": {"countsAreCurrent": True},
                "quality": {"issues": [{"code": "capture_missing_after_window",
                                       "raceDate": "2026-11-08", "raceID": "202605050212"}]}
            }))
            folder = root / "reports" / "20261108"
            folder.mkdir(parents=True)
            (folder / "PaceRecordingSchedule.json").write_text(json.dumps({
                "observedAtJST": "2026-11-08T10:52:02+09:00",
                "races": [{"raceID": "202605050212",
                           "scheduledPostJST": "2026-11-08T16:10:00+09:00"}]
            }))
            events = folder / "prerace" / "202605050212" / "events"
            events.mkdir(parents=True)
            (events / "miss.json").write_text(json.dumps({
                "raceID": "202605050212", "stage": "monitor_missed",
                "atJST": "2026-11-08T18:45:57+09:00",
                "detail": "live capture window expired"
            }))
            row = inspect(root)["races"][0]
            self.assertFalse(row["scheduleSnapshotAfterT10Window"])
            self.assertEqual(row["monitorMissedAfterT10Window"], ["2026-11-08T18:45:57+09:00"])
            self.assertFalse(row["collectorRootCauseEstablished"])

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
