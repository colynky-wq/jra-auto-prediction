"""After-race research may not publish partial or mismatched results."""
from datetime import datetime
import unittest
from after_race_result_gate import JST, NotReady, check


def race(day, id):
    return {"raceDate": day, "raceID": id}


class AfterRacingPublicationTests(unittest.TestCase):
    d = "2026-11-22"
    r = [race(d, "202605050501"), race(d, "202608050601")]

    def now(self, hour, minute=0):
        return datetime(2026, 11, 22, hour, minute, tzinfo=JST)

    def test_racing_day_blocks_early_or_partial_data(self):
        for hour, minute in ((15, 28), (17, 34)):
            with self.subTest(hour=hour), self.assertRaises(NotReady):
                check(self.r, self.r, self.now(hour,minute))
        with self.assertRaises(NotReady):
            check(self.r, self.r[:1], self.now(19))

    def test_all_official_results_after_races_admitted(self):
        d = check(self.r, list(reversed(self.r)), self.now(19,35))
        self.assertEqual(d["races"], 2)
        self.assertFalse(d["formalT10Reconstruction"])
        self.assertTrue(d["researchOnly"])

    def test_other_day_never_fakes_current_settlement(self):
        with self.assertRaises(NotReady):
            check(self.r, [race("2026-11-21",r["raceID"]) for r in self.r], self.now(19))
        with self.assertRaises(NotReady):
            check([race("2026-11-21",r["raceID"]) for r in self.r], self.r, self.now(19))

    def test_invalid_identity_fails_closed(self):
        with self.assertRaises(ValueError):
            check(self.r, self.r+self.r[:1], self.now(19))
        with self.assertRaises(ValueError):
            check([{"raceDate":self.d, "raceID":"123"}], [{"raceDate":self.d,"raceID":"123"}], self.now(19))

    def test_invalid_timestamp_or_type(self):
        with self.assertRaises(ValueError):
            check(self.r, self.r, datetime(2026,11,22,19))
        with self.assertRaises(ValueError):
            check(None, self.r, self.now(19))


if __name__=="__main__":
    unittest.main()
