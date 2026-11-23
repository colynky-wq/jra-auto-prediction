import unittest
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


if __name__ == "__main__":
    unittest.main()
