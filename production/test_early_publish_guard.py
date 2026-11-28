import unittest
from datetime import datetime
from early_publish_guard import decision, capture_window

DAY = "2026-11-28"
ROWS = [{"raceDate": DAY, "startTime": "09:45"}]
PAYLOAD = {"raceDate": DAY, "snapshots": {
    "morning": {"capturedAtJST": DAY + "T06:00:00+09:00"},
    "previous_evening": {"capturedAtJST": "2026-11-27T20:00:00+09:00"}}}


class EarlyGuardTests(unittest.TestCase):
    def check(self, current=(), now="2026-11-28T07:00:00+09:00", stage="morning", payload=PAYLOAD):
        return decision(ROWS, list(current), payload, stage, datetime.fromisoformat(now))

    def test_capture_window(self):
        for clock, stage, expected in (
            ("2026-11-28T07:00:00+09:00", "morning", "capture"),
            ("2026-11-28T09:45:00+09:00", "morning", "first_post_deadline"),
            ("2026-11-28T13:00:00+09:00", "morning", "first_post_deadline"),
            ("2026-11-28T00:01:00+09:00", "previous_evening", "stage_clock_mismatch"),
            ("2026-11-27T20:00:00+09:00", "previous_evening", "capture"),
        ):
            self.assertEqual(capture_window(DAY, stage, ["10:05", "09:45"],
                                            datetime.fromisoformat(clock)), expected)
        with self.assertRaises(ValueError):
            capture_window(DAY, "morning", [], datetime.fromisoformat(DAY + "T07:00:00+09:00"))

    def test_old_feed_and_same_day_early_can_refresh(self):
        self.assertEqual(self.check([{"raceDate": "2026-11-23"}]), "publish")
        self.assertEqual(self.check(ROWS), "publish")

    def test_live_is_never_overwritten(self):
        for timing in ("live_10min", "live_late_recovery"):
            self.assertEqual(self.check([{"raceDate": DAY, "predictionTiming": timing}]), "existing_live_capture")

    def test_newer_meeting_is_never_overwritten(self):
        self.assertEqual(self.check([{"raceDate": "2026-11-29"}]), "newer_meeting")

    def test_deadline(self):
        self.assertEqual(self.check(now=DAY + "T09:45:00+09:00"), "first_post_deadline")

    def test_previous_evening(self):
        self.assertEqual(self.check(stage="previous_evening"), "publish")

    def test_stage_date_and_future_capture(self):
        bad = {"raceDate": DAY, "snapshots": {"morning": {"capturedAtJST": "2026-11-27T20:00:00+09:00"}}}
        self.assertEqual(self.check(payload=bad), "stage_clock_mismatch")
        self.assertEqual(self.check(now=DAY + "T05:59:59+09:00"), "stage_clock_mismatch")

    def test_mixed_date_rejected(self):
        with self.assertRaises(ValueError):
            decision(ROWS + [{"raceDate": "2026-11-29"}], [], PAYLOAD, "morning",
                     datetime.fromisoformat(DAY + "T07:00:00+09:00"))


if __name__ == "__main__":
    unittest.main()
