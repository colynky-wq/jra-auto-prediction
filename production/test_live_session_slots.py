#!/usr/bin/env python3
"""No-network regression tests for serialized GitHub Actions live slots."""
from datetime import datetime, timedelta
import unittest

from live_session_slots import choose, JST, CUTOFFS

DAY = "20261115"


def clock(hour, minute, seconds=0, date=15):
    return datetime(2026, 11, date, hour, minute, seconds, tzinfo=JST)


class LiveSessionSlotTests(unittest.TestCase):
    def test_early_sessions_are_capped_not_truncated_at_old_handoff(self):
        # On-time morning jobs use at most 335 minutes per worker.
        for cron, start, expected in (
            ("15 20 * * 5,6,0", (5, 15), 335),
            ("15 21 * * 5,6,0", (6, 15), 335),
            ("45 22 * * *", (7, 45), 335),
            ("15 23 * * 5,6,0", (8, 15), 335),
            ("0 3 * * *", (12, 0), 330),
        ):
            with self.subTest(cron=cron):
                state = choose("schedule", cron, DAY, clock(*start))
                self.assertEqual((state["active"], state["minutes"]),
                                 (True, expected))

    def test_delayed_early_runner_can_bridge_verified_nov22_gap(self):
        early = "15 20 * * 5,6,0"
        # Actual Nov 22 early worker started at ~09:21. The old deadline
        # forced it to stop at 10:35, then the next worker started at 10:59.
        # It must be eligible to stay alive through that gap without creating
        # or backdating any formal prediction records.
        at_start = choose("schedule", early, DAY, clock(9, 21))
        self.assertEqual(at_start["minutes"], 335)
        self.assertTrue(choose("schedule", early, DAY, clock(10, 35))["active"])
        self.assertTrue(choose("schedule", early, DAY, clock(11, 7))["active"])
        self.assertTrue(choose("schedule", early, DAY, clock(12, 50))["active"])
        self.assertFalse(choose("schedule", early, DAY, clock(17, 29))["active"])

    def test_serialized_handoff_can_cover_rest_of_day(self):
        # With one active writer and one newest pending replacement, a worker
        # starting 09:21 can run its bounded 335 minutes until 14:56.
        # The queued heartbeat can then continue to the final cutoff.
        first = choose("schedule", "15 20 * * 5,6,0", DAY, clock(9, 21))
        self.assertEqual(first["minutes"], 335)
        second = choose("schedule", "7,22,37,52 0-7 * * 0,1,6",
                        DAY, clock(14, 56))
        self.assertEqual(second["minutes"], 154)
        self.assertFalse(choose("schedule", "0 3 * * *", DAY,
                                clock(17, 30))["active"])

    def test_start_after_phase_deadline_is_never_treated_as_live_success(self):
        for cron, end in CUTOFFS.items():
            hh, mm = [int(v) for v in end.split(":")]
            with self.subTest(cron=cron):
                self.assertFalse(choose("schedule", cron, DAY,
                                        clock(hh, mm))["active"])
                self.assertFalse(choose("schedule", cron, DAY,
                                        clock(hh, mm - 1))["active"])

    def test_staggered_heartbeats_serve_rest_of_day_without_replaying(self):
        morning = "7,22,37,52 22-23 * * 5,6,0"
        daytime = "7,22,37,52 0-7 * * 0,1,6"
        self.assertEqual(choose("schedule", morning, DAY, clock(7, 7))["minutes"], 335)
        self.assertEqual(choose("schedule", daytime, DAY, clock(10, 35))["minutes"], 335)
        self.assertEqual(choose("schedule", daytime, DAY, clock(16, 15))["minutes"], 75)
        self.assertFalse(choose("schedule", morning, DAY, clock(17, 29))["active"])
        self.assertFalse(choose("schedule", daytime, DAY, clock(17, 30))["active"])
        self.assertFalse(choose("schedule", daytime, DAY, clock(12, 0, date=16))["active"])

    def test_push_cannot_run_live_session(self):
        self.assertFalse(choose("push", "", "", clock(9, 30))["active"])

    def test_invalid_cron_date_and_manual_limits_are_fail_closed(self):
        with self.assertRaises(ValueError):
            choose("schedule", "wrong", DAY, clock(8, 0))
        with self.assertRaises(ValueError):
            choose("schedule", "15 20 * * 5,6,0", "20261199", clock(8, 0))
        self.assertFalse(choose("schedule", "15 20 * * 5,6,0",
                                DAY, clock(8, 0, date=16))["active"])
        self.assertEqual(choose("workflow_dispatch", "", DAY, clock(8, 0),
                                manual_minutes="120")["minutes"], 120)
        for invalid in ("0", "336", "text", "1;rm /"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                choose("workflow_dispatch", "", DAY, clock(8, 0),
                       manual_minutes=invalid)


if __name__ == "__main__":
    unittest.main()
