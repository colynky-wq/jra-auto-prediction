"""Regression tests: late scheduled aggregations must not seek live results."""
import unittest
from datetime import datetime, timedelta, timezone

from forward_aggregate_schedule import target_date, JST


class AggregateDateTests(unittest.TestCase):
    def test_delayed_november_21_job_started_next_morning(self):
        # Observed failing run #44932377732, 02:52 JST on 2026-11-22:
        # old code mistakenly requested the still-incomplete Nov 22 races.
        now = datetime(2026, 11, 22, 2, 52, tzinfo=JST)
        self.assertEqual(target_date("", "schedule", now), "20261121")

    def test_on_time_scheduled_run(self):
        self.assertEqual(target_date("", "schedule", datetime(2026, 11, 21, 20, tzinfo=JST)),
                         "20261121")

    def test_before_evening_window_on_schedule_is_previous_day(self):
        self.assertEqual(target_date("", "schedule", datetime(2026, 11, 23, 19, 59, tzinfo=JST)),
                         "20261122")

    def test_explicit_dispatch_keeps_requested_date(self):
        self.assertEqual(target_date("20261115", "workflow_dispatch",
                                     datetime(2026, 11, 22, 7, tzinfo=JST)), "20261115")

    def test_manual_blank_dispatch_keeps_today(self):
        self.assertEqual(target_date("", "workflow_dispatch", datetime(2026, 11, 22, 2, tzinfo=JST)),
                         "20261122")

    def test_utc_day_boundary_is_jst(self):
        now = datetime(2026, 11, 21, 17, 52, tzinfo=timezone.utc)
        self.assertEqual(target_date("", "schedule", now), "20261121")

    def test_bad_explicit_date_or_naive_clock_rejected(self):
        for bad in ("20261301", "2026111", "2026-11-21", "20260230"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                target_date(bad, "workflow_dispatch", datetime.now(JST))
        with self.assertRaises(ValueError):
            target_date("", "schedule", datetime(2026, 11, 21, 20))


if __name__ == "__main__":
    unittest.main()
