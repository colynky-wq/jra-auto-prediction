"""Regression tests for read-only, prospective ticket calibration."""
import unittest
from production.research_ticket_calibration import evaluate


def audit(**overrides):
    data = {
        "projectionState": "verified",
        "aligned": {"countsAreCurrent": True, "racesObserved": 3},
        "cohortLocks": {},
        "races": [
            {"scope": "prospective", "settled": True, "resultState": "matched",
             "raceID": "A", "legs": [
                 {"betType": "wide", "probabilityPct": 20.0, "hit": True},
                 {"betType": "wide", "probabilityPct": 10.0, "hit": False}]},
            {"scope": "prospective", "settled": False, "resultState": "pending",
             "raceID": "B", "legs": [{"betType": "wide", "probabilityPct": 99, "hit": None}]},
            {"scope": "legacy", "settled": True, "resultState": "matched",
             "raceID": "OLD", "legs": [{"betType": "wide", "probabilityPct": 99, "hit": True}]},
            {"scope": "prospective", "settled": True, "resultState": "matched",
             "raceID": "C", "legs": [{"betType": "win", "probabilityPct": None, "hit": False}]},
        ],
    }
    data.update(overrides)
    return data


class CalibrationTests(unittest.TestCase):
    def test_matched_prospective_only(self):
        result = evaluate(audit())
        self.assertEqual(result["overall"]["tickets"], 2)
        self.assertEqual(result["overall"]["expectedHits"], 0.3)
        self.assertEqual(result["overall"]["actualHits"], 1)
        self.assertEqual(result["byBetType"]["wide"]["tickets"], 2)

    def test_failed_audit_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(audit(projectionState="failed"))

    def test_stale_counts_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(audit(aligned={"countsAreCurrent": False}))

    def test_recovery_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(audit(cohortLockRecoveryRequired=True))
        with self.assertRaises(ValueError):
            evaluate(audit(cohortLocks={"cohortLockRecoveryRequired": True}))

    def test_no_observations_not_zero_hits(self):
        result = evaluate(audit(races=[]))
        self.assertEqual(result["overall"]["tickets"], 0)
        self.assertIsNone(result["overall"]["actualHits"])
        self.assertIsNone(result["overall"]["observedPct"])

    def test_invalid_and_unknown_not_misses(self):
        document = audit()
        document["races"][0]["legs"].extend([
            {"betType": "wide", "probabilityPct": 12, "hit": None},
            {"betType": "wide", "probabilityPct": 101, "hit": False},
            {"betType": "wide", "probabilityPct": float("nan"), "hit": True},
        ])
        self.assertEqual(evaluate(document)["overall"]["tickets"], 2)


if __name__ == "__main__":
    unittest.main()
