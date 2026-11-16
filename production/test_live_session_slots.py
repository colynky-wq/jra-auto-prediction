#!/usr/bin/env python3
"""No-network regression tests for serialized GitHub Actions live slots."""
from datetime import datetime, timedelta
import unittest

from live_session_slots import choose, JST, CUTOFFS

DAY = "20261115"


def clock(hour, minute, seconds=0, date=15):
    return datetime(2026, 11, date, hour, minute, seconds, tzinfo=JST)


class LiveSessionSlotTests(unittest.TestCase):
    def test_original_primary_end_times_unchanged(self):
        for cron, start, expected in (
            ("15 20 * * 5,6,0", (5, 15), 320),
            ("45 22 * * *", (7, 45), 305),
            ("0 3 * * *", (12, 0), 330),
        ):
            with self.subTest(cron=cron):
                state = choose("schedule", cron, DAY, clock(*start))
                self.assertEqual((state["active"], state["minutes"]),
                                 (True, expected))

    def test_backups_share_phase_cutoff_and_cannot_extend_late_session(self):
        cases = (
            ("15 21 * * 5,6,0", clock(6, 15), 260, clock(10, 35)),
            ("15 23 * * 5,6,0", clock(8, 15), 275, clock(12, 50)),
        )
        for cron, start, expected, cutoff in cases:
            with self.subTest(cron=cron):
                self.assertEqual(choose("schedule", cron, DAY, start)["minutes"],
                                 expected)
                # A backup queued until the primary completes MUST skip.
                self.assertFalse(choose("schedule", cron, DAY, cutoff)["active"])
                self.assertFalse(choose("schedule", cron, DAY, cutoff +
                                        timedelta(minutes=5))["active"])

    def test_late_early_runner_does_not_block_noon_handoff(self):
        # 05:15 delayed until 11:07, as in the real Nov 15 run.
        self.assertFalse(choose("schedule", "15 20 * * 5,6,0",
                                DAY, clock(11, 7))["active"])
        # 07:45 phase starting at 10:35 finishes at 12:50, not 15:40.
        r = choose("schedule", "45 22 * * *", DAY, clock(10, 35))
        self.assertEqual(r["minutes"], 135)
        self.assertTrue(choose("schedule", "0 3 * * *",
                               DAY, clock(12, 50))["active"])
        self.assertEqual(choose("schedule", "0 3 * * *",
                                DAY, clock(12, 50))["minutes"], 280)

    def test_start_after_phase_deadline_is_never_treated_as_live_success(self):
        for cron, end in CUTOFFS.items():
            hh, mm = [int(v) for v in end.split(":")]
            with self.subTest(cron=cron):
                self.assertFalse(choose("schedule", cron, DAY,
                                        clock(hh, mm))["active"])
                self.assertFalse(choose("schedule", cron, DAY,
                                        clock(hh, mm - 1))["active"])

    def test_fifo_queued_backups_do_not_occupy_primary_handoff_windows(self):
        # GitHub queue:max permits multiple pending runs, but the live writer
        # remains serialized. Simulate timely primary and queued backups.
        crons = (
            "15 20 * * 5,6,0", "15 21 * * 5,6,0",
            "45 22 * * *", "15 23 * * 5,6,0", "0 3 * * *",
        )
        start = clock(5, 15)
        timeline = []
        for cron in crons:
            state = choose("schedule", cron, DAY, start)
            if state["active"]:
                end = start + timedelta(minutes=state["minutes"])
                timeline.append((cron, start.strftime("%H:%M"), end.strftime("%H:%M")))
                start = end
            else:
                timeline.append((cron, start.strftime("%H:%M"), "skipped"))
        self.assertEqual(timeline, [
            ("15 20 * * 5,6,0", "05:15", "10:35"),
            ("15 21 * * 5,6,0", "10:35", "skipped"),
            ("45 22 * * *", "10:35", "12:50"),
            ("15 23 * * 5,6,0", "12:50", "skipped"),
            ("0 3 * * *", "12:50", "17:30"),
        ])

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
