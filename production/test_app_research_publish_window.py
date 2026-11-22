import unittest
from datetime import datetime, timedelta, timezone
from app_research_publish_window import defer, JST


class PublishWindowTests(unittest.TestCase):
    races = [{"raceDate": "2026-11-22", "raceID": "202605050601"}]

    def now(self, hour, minute=0, day=22):
        return datetime(2026, 11, day, hour, minute, tzinfo=JST)

    def test_live_race_day_is_deferred(self):
        self.assertTrue(defer(self.now(14, 47), self.races))
        self.assertTrue(defer(self.now(8, 30), self.races))
        self.assertTrue(defer(self.now(17, 34), self.races))

    def test_evening_after_live_collection_is_allowed(self):
        self.assertFalse(defer(self.now(17, 35), self.races))
        self.assertFalse(defer(self.now(18, 50), self.races))
        self.assertFalse(defer(self.now(7, 45), self.races))

    def test_stale_races_do_not_defer(self):
        self.assertFalse(defer(self.now(12), [{"raceDate": "2026-11-21"}]))
        self.assertFalse(defer(self.now(12), []))

    def test_timezone_is_jst(self):
        self.assertTrue(defer(datetime(2026, 11, 22, 5, 47, tzinfo=timezone.utc), self.races))

    def test_invalid_racedata_is_rejected(self):
        with self.assertRaises(ValueError):
            defer(self.now(12), None)
        with self.assertRaises(ValueError):
            defer(datetime(2026, 11, 22, 12), self.races)


if __name__ == "__main__":
    unittest.main()
