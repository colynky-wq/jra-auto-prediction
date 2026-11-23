import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from win_odds_freshness import (
    WinOddsFreshnessError, parse_official_datetime,
    validate_win_odds_freshness,
)

JST = ZoneInfo("Asia/Tokyo")
RECEIVED = datetime(2026, 11, 23, 15, 20, tzinfo=JST)


def payload(value):
    return {"data": {"official_datetime": value, "odds": {"1": {"1": [2.5]}}}}


class WinOddsFreshnessTests(unittest.TestCase):
    def test_current_source_accepted(self):
        result = validate_win_odds_freshness(payload("2026-11-23 15:10:00"), RECEIVED)
        self.assertEqual(result["sourceAgeSecondsAtReceipt"], 600)
        self.assertEqual(result["freshnessStatus"], "verified_within_30m")

    def test_exact_boundary_accepted(self):
        result = validate_win_odds_freshness(payload("2026/11/23 14:50"), RECEIVED)
        self.assertEqual(result["sourceAgeSecondsAtReceipt"], 1800)

    def test_stale_rejected_even_when_just_fetched(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "stale_over_30m"):
            validate_win_odds_freshness(payload("2026-11-23 14:49:59"), RECEIVED)

    def test_missing_rejected(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "timestamp_missing"):
            validate_win_odds_freshness({"data": {"odds": {}}}, RECEIVED)

    def test_future_rejected(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "in_future"):
            validate_win_odds_freshness(payload("2026-11-23 15:20:01"), RECEIVED)

    def test_time_only_rejected(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "unparseable"):
            validate_win_odds_freshness(payload("15:10"), RECEIVED)

    def test_naive_source_is_jst(self):
        self.assertEqual(parse_official_datetime("2026年11月23日 15:10").utcoffset(),
                         timedelta(hours=9))

    def test_utc_source_normalized(self):
        result = validate_win_odds_freshness(payload("2026-11-23T06:10:00Z"), RECEIVED)
        self.assertEqual(result["sourceAgeSecondsAtReceipt"], 600)

    def test_naive_receipt_rejected(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "receipt_timestamp_naive"):
            validate_win_odds_freshness(payload("2026-11-23 15:10"), RECEIVED.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
