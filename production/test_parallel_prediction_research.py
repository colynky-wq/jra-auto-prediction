#!/usr/bin/env python3
"""Network-free safety tests for the three-track prediction research summary."""
from copy import deepcopy
import unittest

from parallel_prediction_research import build, markdown


def fixture():
    audit = {
        "projectionState": "verified",
        "generatedAtJST": "2026-11-22T22:00:00+09:00",
        "aligned": {
            "countsAreCurrent": True,
            "racesObserved": 128,
            "matchedResultRaces": 124,
            "overall": {"resultsPendingSelectedRaces": 3},
        },
        "quality": {"status": "attention_required",
                    "missingCaptureCount": 24,
                    "issues": [{"code": "capture_missing_after_window"}]},
    }
    row = {
        "topPredictedHorseRaces": 80,
        "topPredictedHorsePlaced": 15,
        "topPredictedMarketHorsePlaced": 16,
        "horseProbabilities": {
            "ai": {"brierScore": 0.088},
            "market": {"brierScore": 0.084},
            "marketBlend50": {"brierScore": 0.082}},
        "pairedRaceUncertainty": {
            "ai": {"meanBrierDelta": 0.004, "bootstrap95Pct": [-0.001, 0.008]},
            "marketBlend50": {
                "meanBrierDelta": -0.002, "bootstrap95Pct": [-0.007, 0.002]},
        },
    }
    historical = {
        "source": {"generatedAtJST": audit["generatedAtJST"]},
        "coverage": {"matchedAuditedRaces": 124, "evaluatedRaces": 124},
        "quality": {"newAlignedBetaEligible": False},
        "status": "quality_limited_descriptive_only",
        "probabilities": {
            c: {e: deepcopy(row) for e in ("win", "top2", "top3")}
            for c in ("first100", "confirmation101to200")},
    }
    forward = {
        "sourceAuditGeneratedAtJST": audit["generatedAtJST"],
        "counts": {"frozenSnapshots": 20, "pairedAuditedRaces": 12},
        "status": "insufficient_prospective_evidence",
        "autoBetEnabled": False,
        "formalRecommendationsEnabled": False,
        "registeredFixedWeightsOnAI": {
            "marketOnly": 0.0, "marketBlend25": 0.25,
            "marketBlend50": 0.5, "existingAI": 1.0},
        "byEvent": {e: {"pairedRaces": 12,
                        "brierByModel": {"marketOnly": 0.08,
                                         "marketBlend25": 0.075,
                                         "marketBlend50": 0.09,
                                         "existingAI": 0.1}}
                    for e in ("win", "top2", "top3")},
    }
    return audit, historical, forward


class ParallelPredictionResearchTests(unittest.TestCase):
    def test_collect_analyze_and_prospective_remain_distinct(self):
        audit, historical, forward = fixture()
        out = build(audit, historical, forward)
        self.assertEqual(out["status"], "quality_limited_research_only")
        self.assertEqual(out["tracks"]["collection"]["additionalMatchedTo300"], 176)
        self.assertEqual(out["tracks"]["historicalDiagnostics"]["evaluatedRaces"], 124)
        self.assertEqual(out["tracks"]["prospectiveCandidateValidation"]["pairedAuditedRaces"], 12)
        self.assertFalse(out["candidatePromotionApproved"])
        self.assertFalse(out["autoBetEnabled"])
        self.assertFalse(out["betaAutomaticallyEnabled"])
        self.assertFalse(out["formalPredictionAIChanged"])
        doc = markdown(out)
        self.assertIn("Remaining matched races for the 300-R research gate: 176", doc)
        self.assertIn("100 prospective matched races is only a descriptive review milestone", doc)
        self.assertNotIn("model promoted", doc)

    def test_quality_issues_are_never_hidden(self):
        audit, historical, forward = fixture()
        audit["quality"]["issues"] = []
        result = build(audit, historical, forward)
        self.assertEqual(result["status"], "descriptive_research_only")
        self.assertEqual(result["tracks"]["collection"]["qualityIssueCount"], 0)
        self.assertFalse(result["candidatePromotionApproved"])
        self.assertFalse(result["betaAutomaticallyEnabled"])

    def test_stale_failed_audit_and_identity_recovery_fail_closed(self):
        audit, historical, forward = fixture()
        for action in ("failed", "stale", "identity"):
            bad = deepcopy(audit)
            if action == "failed":
                bad["projectionState"] = "failed"
            elif action == "stale":
                bad["aligned"]["countsAreCurrent"] = False
            else:
                bad["cohortLockRecoveryRequired"] = True
            with self.subTest(action=action), self.assertRaises(ValueError):
                build(bad, historical, forward)

    def test_mismatched_provenance_and_counts_cannot_publish(self):
        audit, historical, forward = fixture()
        for name in ("historical", "prospective", "count"):
            h, f = deepcopy(historical), deepcopy(forward)
            if name == "historical":
                h["source"]["generatedAtJST"] = "stale"
            elif name == "prospective":
                f["sourceAuditGeneratedAtJST"] = "stale"
            else:
                h["coverage"]["matchedAuditedRaces"] -= 1
            with self.subTest(name=name), self.assertRaises(ValueError):
                build(audit, h, f)

    def test_prospective_cannot_exceed_matched_evidence_or_enable_bets(self):
        audit, historical, forward = fixture()
        for name in ("fake_count", "auto_betting", "formal", "status"):
            f = deepcopy(forward)
            if name == "fake_count":
                f["counts"]["pairedAuditedRaces"] = 150
            elif name == "auto_betting":
                f["autoBetEnabled"] = True
            elif name == "formal":
                f["formalRecommendationsEnabled"] = True
            else:
                f["status"] = "candidate_approved"
            with self.subTest(name=name), self.assertRaises(ValueError):
                build(audit, historical, f)


if __name__ == "__main__":
    unittest.main()
