#!/usr/bin/env python3
"""Network-free tests for the isolated frozen-horse probability diagnostic."""
from copy import deepcopy
from pathlib import Path
import unittest

from inspect_frozen_win_probabilities import build


def fixture():
    horses = []
    for n in range(1, 5):
        horses.append({
            "number": n,
            "aiProbability": 40 if n == 1 else 20,
            "aiTop2Probability": 55 if n == 1 else 35,
            "aiTop3Probability": 80 if n == 1 else 70,
            "marketProbability": 25,
            "marketTop2Probability": 50,
            "marketTop3Probability": 75,
        })
    record = {"fingerprint": "frozen1", "prediction": {
        "raceDate": "2026-11-07", "raceID": "202603030101",
        "predictionTiming": "live_10min", "horses": horses}}
    audit = {
        "projectionState": "verified", "generatedAtJST": "2026-11-08T10:00:00+09:00",
        "protocolVersion": "frozen-protocol",
        "aligned": {
            "countsAreCurrent": True, "racesObserved": 2,
            "matchedResultRaces": 1,
            "first100": {"raceIDs": ["202603030101"], "races": 1},
            "confirmation101to200": {"raceIDs": ["202603030102"], "races": 1},
        },
        "quality": {
            "issues": [{"code": "capture_missing_after_window"}],
            "resultExclusions": [{"raceDate": "2026-11-07",
                                  "raceID": "202603030102", "reason": "entrant_changed"}]
        },
        "races": [
            {"raceDate": "2026-11-07", "raceID": "202603030101",
             "resultState": "matched", "inputFingerprint": "frozen1",
             "actualFinish": {"1": 2, "2": 1, "3": 3, "4": 4}},
            {"raceDate": "2026-11-07", "raceID": "202603030102",
             "resultState": "entrant_changed", "inputFingerprint": "frozen2",
             "actualFinish": {"1": 4, "2": 1, "3": 3}},
        ],
    }
    protocol = {"version": "frozen-protocol", "policy": {"noV1Changes": True}}
    return audit, protocol, record


class FrozenWinProbabilitiesTests(unittest.TestCase):
    def test_valid_subset_does_not_convert_exclusions_into_losses(self):
        audit, protocol, record = fixture()
        used = []
        def load(root, day, rid):
            used.append((day, rid))
            return record
        out = build(Path("."), audit, protocol, load_bundle=load)
        self.assertEqual(used, [("2026-11-07", "202603030101")])
        self.assertEqual(out["status"], "quality_limited_descriptive_only")
        self.assertEqual(out["coverage"]["evaluatedRaces"], 1)
        self.assertEqual(out["coverage"]["pendingOrExcludedRaces"], 1)
        self.assertEqual(out["probabilities"]["first100"]["win"]["topPredictedHorsePlaced"], 0)
        self.assertEqual(out["probabilities"]["first100"]["top2"]["topPredictedHorsePlaced"], 1)
        self.assertIsNone(out["probabilities"]["confirmation101to200"]["win"]
                          ["horseProbabilities"]["ai"])
        self.assertFalse(out["quality"]["newAlignedBetaEligible"])

    def test_unverified_stale_lock_and_fingerprint_fail_closed(self):
        audit, protocol, record = fixture()
        for action in ("unverified", "stale", "lock", "bad_fingerprint", "bad_protocol"):
            a = deepcopy(audit)
            r = deepcopy(record)
            p = deepcopy(protocol)
            if action == "unverified":
                a["projectionState"] = "failed"
            elif action == "stale":
                a["aligned"]["countsAreCurrent"] = False
            elif action == "lock":
                a["cohortLockRecoveryRequired"] = True
            elif action == "bad_fingerprint":
                r["fingerprint"] = "wrong"
            elif action == "bad_protocol":
                p["version"] = "changed"
            with self.subTest(action=action), self.assertRaises(ValueError):
                build(Path("."), a, p, load_bundle=lambda *_: r)

    def test_unknown_finish_is_excluded_per_event_not_counted_as_loss(self):
        audit, protocol, record = fixture()
        bad = deepcopy(audit)
        bad["races"][0]["actualFinish"]["3"] = None
        out = build(Path("."), bad, protocol, load_bundle=lambda *_: record)
        self.assertEqual(out["probabilities"]["first100"]["win"]["topPredictedHorseRaces"], 1)
        self.assertEqual(out["probabilities"]["first100"]["top2"]["topPredictedHorseRaces"], 1)
        self.assertEqual(out["probabilities"]["first100"]["top3"]["topPredictedHorseRaces"], 0)
        self.assertEqual(out["coverage"]["unknownFinishRacesByEvent"]["top3"], ["202603030101"])

        bad["races"][0]["actualFinish"]["2"] = None
        out = build(Path("."), bad, protocol, load_bundle=lambda *_: record)
        self.assertEqual(out["probabilities"]["first100"]["win"]["topPredictedHorseRaces"], 1)
        self.assertEqual(out["probabilities"]["first100"]["top2"]["topPredictedHorseRaces"], 0)
        self.assertEqual(out["coverage"]["unknownFinishRacesByEvent"]["top2"], ["202603030101"])

    def test_matched_races_beyond_200_do_not_pollute_locked_windows(self):
        audit, protocol, record = fixture()
        # Add 198 unmatched observations inside the two locked windows
        # and one newly matched frozen race outside both windows.
        more = []
        for n in range(1000, 1198):
            rid = "20260303" + str(n)
            more.append({
                "raceDate": "2026-11-07", "raceID": rid,
                "resultState": "awaiting_result", "inputFingerprint": "unused",
            })
        beyond_id = "202603039999"
        beyond = {
            "raceDate": "2026-11-07", "raceID": beyond_id,
            "resultState": "matched", "inputFingerprint": "laterfreeze",
            "actualFinish": {"1": 1, "2": 2, "3": 3},
        }
        audit["races"] = audit["races"] + more + [beyond]
        audit["aligned"]["racesObserved"] = 201
        audit["aligned"]["matchedResultRaces"] = 2
        audit["aligned"]["first100"]["raceIDs"] += [
            r["raceID"] for r in more[:99]]
        audit["aligned"]["first100"]["races"] = 100
        audit["aligned"]["confirmation101to200"]["raceIDs"] += [
            r["raceID"] for r in more[99:]]
        audit["aligned"]["confirmation101to200"]["races"] = 100
        after = deepcopy(record)
        after["fingerprint"] = "laterfreeze"
        after["prediction"]["raceID"] = beyond_id

        def load(root, day, rid):
            return after if rid == beyond_id else record

        report = build(Path("."), audit, protocol, load_bundle=load)
        self.assertEqual(report["coverage"]["matchedOutsideFirst200Races"], 1)
        self.assertEqual(report["coverage"]["matchedAuditedRaces"], 2)
        self.assertEqual(report["probabilities"]["first100"]["win"]
                         ["topPredictedHorseRaces"], 1)
        self.assertIsNone(report["probabilities"]["confirmation101to200"]
                          ["win"]["horseProbabilities"]["ai"])

    def test_invalid_probability_still_fails_closed(self):
        audit, protocol, record = fixture()
        bad_record = deepcopy(record)
        bad_record["prediction"]["horses"][0]["aiProbability"] = None
        with self.assertRaises(ValueError):
            build(Path("."), audit, protocol, load_bundle=lambda *_: bad_record)


if __name__ == "__main__":
    unittest.main()
