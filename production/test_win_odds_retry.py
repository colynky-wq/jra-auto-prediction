import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import os
from unittest.mock import patch
import requests
from win_odds_retry import WinOddsFetchError, retryable_win_odds_failure
from win_odds_freshness import WinOddsFreshnessError


class RetryClassificationTests(unittest.TestCase):
    def test_stale_source_retries(self):
        self.assertTrue(retryable_win_odds_failure(
            WinOddsFreshnessError("win_odds_source_stale_over_30m")))

    def test_missing_source_time_retries(self):
        self.assertTrue(retryable_win_odds_failure(
            WinOddsFreshnessError("win_odds_source_timestamp_missing")))

    def test_transport_timeout_retries(self):
        self.assertFalse(retryable_win_odds_failure(requests.Timeout("unscoped network error")))

    def test_http_status_retries(self):
        self.assertTrue(retryable_win_odds_failure(WinOddsFetchError("odds HTTP 503")))

    def test_missing_odds_rows_retries(self):
        self.assertTrue(retryable_win_odds_failure(WinOddsFetchError("insufficient live win odds")))

    def test_model_failure_never_retries(self):
        self.assertFalse(retryable_win_odds_failure(ValueError("model feature mismatch")))

    def test_publication_failure_never_retries(self):
        self.assertFalse(retryable_win_odds_failure(RuntimeError("publication failed")))

    
class MonitorRetryOutcomeTests(unittest.TestCase):
    """Exercise the actual shared monitor loop without network or file writes."""

    def run_monitor(self, *, start, outcomes, second_race=False):
        import pandas as pd
        import live_monitor_dry_run as monitor

        now = datetime.fromisoformat("2026-11-23T" + start).replace(tzinfo=monitor.JST)
        race = {"race_id": "202605060601", "venue": "東京", "race_no": 1,
                "start_time": "11:00", "surface": "芝"}
        races = [race]
        if second_race:
            races.append(dict(race, race_id="202605060602", race_no=2))
        calls = []
        remaining_outcomes = iter(outcomes)

        def predict(race, *_):
            nonlocal now
            calls.append(race["race_id"])
            outcome = next(remaining_outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, int):
                now += timedelta(seconds=outcome)

        def sleep(seconds):
            nonlocal now
            now += timedelta(seconds=seconds)

        output = io.StringIO()
        with patch.multiple(monitor, MODE="live", TARGET_DATE8="20261123",
                            TARGET_RACE_ID="", MAX_RUNTIME_MINUTES=6,
                            POLL_SECONDS=15, WIN_ODDS_RETRY_SECONDS=15), \
             patch.dict(os.environ, {"KEIBAAI_CAPTURE_PRERACE": "0",
                                     "KEIBAAI_PUBLISH_RACEDATA": "0"}), \
             patch.object(monitor, "now_jst", side_effect=lambda: now), \
             patch.object(monitor, "fetch_schedule", return_value=races), \
             patch.object(monitor, "print_schedule"), \
             patch.object(monitor, "load_history",
                          return_value=pd.DataFrame({"日付": [pd.Timestamp("2026-11-22")]})), \
             patch.object(monitor, "predict_live_race", side_effect=predict), \
             patch.object(monitor.time, "sleep", side_effect=sleep), redirect_stdout(output):
            try:
                monitor.main()
                failed = False
            except SystemExit as exc:
                self.assertEqual(exc.code, 1)
                failed = True
        return failed, calls, output.getvalue()

    def test_recovered_retry_is_not_final_failure(self):
        failed, calls, log = self.run_monitor(
            start="10:50:00", outcomes=[WinOddsFetchError("temporary"), None])
        self.assertFalse(failed)
        self.assertEqual(len(calls), 2)
        self.assertIn("WIN_ODDS_RETRY_SCHEDULED", log)
        self.assertIn("failures: []", log)

    def test_unrecovered_retry_stays_failed(self):
        failed, calls, log = self.run_monitor(
            start="10:51:55", outcomes=[WinOddsFetchError("temporary")])
        self.assertTrue(failed)
        self.assertEqual(len(calls), 1)
        self.assertIn("MISSED window:", log)
        self.assertIn("WinOddsFetchError: temporary", log)

    def test_permanent_failure_not_cleared(self):
        failed, calls, log = self.run_monitor(
            start="10:50:00", outcomes=[ValueError("model error")])
        self.assertTrue(failed)
        self.assertEqual(len(calls), 1)
        self.assertIn("ValueError: model error", log)

    def test_clock_rechecked_between_races(self):
        failed, calls, log = self.run_monitor(
            start="10:51:55", outcomes=[10], second_race=True)
        self.assertFalse(failed)
        self.assertEqual(calls, ["202605060601"])
        self.assertIn("MISSED window: 202605060602", log)


if __name__ == "__main__":
    unittest.main()
