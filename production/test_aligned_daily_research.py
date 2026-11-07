#!/usr/bin/env python3
"""Offline, network-free safety tests for research-only aligned daily reporting."""
import copy
import unittest

from aligned_daily_research import build, render


def fixture():
    protocol = {
        "version": "frozen-test",
        "startDate": "2026-11-03",
        "sourceRuleVersion": "frozen-selection",
        "policy": {"noV1Changes": True},
    }
    wide = {
        "betType": "wide", "candidateID": "0102", "probabilityPct": 25.0,
        "oddsLow": 3.0, "hit": True, "payoutYen": 250, "unitYen": 100,
    }
    trio = {
        "betType": "trio", "candidateID": "010203", "probabilityPct": 10.0,
        "oddsLow": 12.0, "hit": False, "payoutYen": 0, "unitYen": 100,
    }
    skipped = {
        "betType": "wide", "candidateID": "0203", "probabilityPct": 12.0,
        "oddsLow": 10.0, "hit": False, "payoutYen": 0, "unitYen": 100,
    }
    first = {
        "raceDate": "2026-11-07", "raceID": "202603030101",
        "venue": "福島", "battleGrade": "S", "chaosLevel": 4,
        "scope": "prospective", "ruleVersion": "frozen-selection",
        "planType": "wide_plus_trio", "resultState": "matched",
        "settled": True, "totalStakeYen": 200, "returnYen": 250,
        "legs": [wide, trio], "commonWideReference": [wide],
        "referenceSettled": True,
    }
    second = {
        "raceDate": "2026-11-07", "raceID": "202603030102",
        "venue": "福島", "battleGrade": "B", "chaosLevel": 2,
        "scope": "prospective", "ruleVersion": "frozen-selection",
        "planType": "skip", "resultState": "matched", "settled": False,
        "totalStakeYen": 0, "returnYen": None, "legs": [],
        "commonWideReference": [skipped], "referenceSettled": True,
    }
    audit = {
        "projectionState": "verified", "generatedAtJST": "2026-11-07T21:00:00+09:00",
        "protocolVersion": "frozen-test", "protocolFingerprint": "frozen",
        "aligned": {
            "countsAreCurrent": True, "racesObserved": 2,
            "matchedResultRaces": 2,
            "overall": {
                "selectedRaces": 1, "skippedRaces": 1,
                "settledSelectedRaces": 1, "resultsPendingSelectedRaces": 0,
                "stakeYen": 200, "returnYen": 250,
            },
        },
        "quality": {"issues": [], "invalidCaptureCount": 0,
                    "missingCaptureCount": 0},
        "betaDecision": {"eligibleForResearchBeta": False},
        "races": [first, second],
    }
    return audit, protocol


class AlignedDailyResearchTests(unittest.TestCase):
    def test_selected_and_skipped_reference_and_trio_increment(self):
        audit, protocol = fixture()
        data = build(audit, protocol)
        self.assertEqual(data["status"], "verified_research_only")
        self.assertEqual(data["cohort"]["racesObserved"], 2)
        self.assertEqual(data["selection"]["actualSelectedTickets"]["tickets"], 2)
        self.assertEqual(data["selection"]["actualSelectedTickets"]["returnYen"], 250)
        self.assertEqual(data["selection"]["actualSelectedTickets"]["expectedWinningTickets"], 0.35)
        h1 = data["selection"]["preRegisteredH1SameWideReference"]
        self.assertEqual(h1["selectedRaceReference"]["tickets"], 1)
        self.assertEqual(h1["skippedRaceReference"]["tickets"], 1)
        self.assertEqual(h1["skippedRaceReference"]["returnYen"], 0)
        h4 = data["selection"]["preRegisteredH4TrioIncrement"]
        self.assertEqual(h4["trioIncrement"]["stakeYen"], 100)
        self.assertEqual(h4["trioIncrement"]["returnYen"], 0)
        self.assertEqual(h4["wideLegsInSameTrioRaces"]["returnYen"], 250)
        self.assertIn("H1", render(data))
        self.assertFalse(data["rules"]["automaticBetting"])

    def test_unverified_and_stale_and_quality_fail_closed(self):
        audit, protocol = fixture()
        for mod in ("failed", "stale", "issues", "lock"):
            bad = copy.deepcopy(audit)
            if mod == "failed":
                bad["projectionState"] = "failed"
            elif mod == "stale":
                bad["aligned"]["countsAreCurrent"] = False
            elif mod == "issues":
                bad["quality"]["issues"] = [{"code": "settlement_integrity"}]
            else:
                bad["cohortLockRecoveryRequired"] = True
            with self.subTest(mod=mod), self.assertRaisesRegex(ValueError, "BLOCKED"):
                build(bad, protocol)

    def test_no_mutation_and_stable_output(self):
        audit, protocol = fixture()
        before = copy.deepcopy(audit)
        output = build(audit, protocol)
        self.assertEqual(audit, before)
        self.assertEqual(output, build(audit, protocol))

    def test_pending_selected_not_falsely_scored_as_loss(self):
        audit, protocol = fixture()
        race = audit["races"][0]
        race["resultState"] = "awaiting_results"
        race["settled"] = False
        race["returnYen"] = None
        race["legs"] = [{**l, "hit": None, "payoutYen": None} for l in race["legs"]]
        race["referenceSettled"] = False
        race["commonWideReference"] = [{**l, "hit": None, "payoutYen": None}
                                        for l in race["commonWideReference"]]
        audit["aligned"]["matchedResultRaces"] = 1
        audit["aligned"]["overall"].update(
            settledSelectedRaces=0, resultsPendingSelectedRaces=1,
            stakeYen=0, returnYen=0,
        )
        report = build(audit, protocol)
        self.assertEqual(report["cohort"]["pendingSelectedRaces"], 1)
        self.assertEqual(report["cohort"]["settledSelectedRaces"], 0)
        self.assertEqual(report["selection"]["actualSelectedTickets"]["tickets"], 0)
        self.assertEqual(report["selection"]["actualSelectedTickets"]["returnIndex"], None)
        self.assertEqual(report["cohort"]["referenceUnsettledRaces"], 1)

    def test_frozen_protocol_and_integrity_guard(self):
        audit, protocol = fixture()
        audit["races"][0]["legs"][0]["payoutYen"] = 1000
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            build(audit, protocol)
        audit, protocol = fixture()
        protocol["sourceRuleVersion"] = "retrospectively-improved"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            build(audit, protocol)
        audit, protocol = fixture()
        audit["races"][0]["raceDate"] = "2026-10-01"
        with self.assertRaisesRegex(ValueError, "BLOCKED"):
            build(audit, protocol)

    def test_empty_cohort_valid_research_not_fake_profit(self):
        audit, protocol = fixture()
        audit["races"] = []
        audit["aligned"]["racesObserved"] = 0
        audit["aligned"]["matchedResultRaces"] = 0
        audit["aligned"]["overall"].update(
            selectedRaces=0, skippedRaces=0, settledSelectedRaces=0,
            resultsPendingSelectedRaces=0, stakeYen=0, returnYen=0,
        )
        data = build(audit, protocol)
        self.assertEqual(data["cohort"]["racesObserved"], 0)
        self.assertIsNone(data["selection"]["actualSelectedTickets"]["returnIndex"])


if __name__ == "__main__":
    unittest.main()
