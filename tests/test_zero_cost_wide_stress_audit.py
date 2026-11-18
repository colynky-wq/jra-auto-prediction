"""Synthetic tests of hypothetical Wide quote stress; no result-based tuning."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from zero_cost_wide_stress_audit import collect_frozen_wide_reference,sensitivity


class ZeroCostWideStressTests(unittest.TestCase):
    def setUp(self):
        self.aligned={
            "projectionState":"verified",
            "aligned":{"countsAreCurrent":True},
            "races":[{
                "raceDate":"2026-11-07","raceID":"202605050107",
                "resultState":"matched","referenceSettled":True,
                "actualFinish":{"1":1,"2":2,"3":4,"4":3},
                "commonWideReference":[
                    {"candidateID":"0102","probabilityPct":20.0,
                     "oddsLow":7.5,"expectedMultiplierLow":1.5,"hit":True},
                    {"candidateID":"0103","probabilityPct":15.0,
                     "oddsLow":8.0,"expectedMultiplierLow":1.2,"hit":False},
                    {"candidateID":"0304","probabilityPct":10.0,
                     "oddsLow":10.0,"expectedMultiplierLow":1.0,"hit":False}
                ]
            }]
        }

    def test_settled_references_and_hits(self):
        xs,n=collect_frozen_wide_reference(self.aligned)
        self.assertEqual(n,0)
        self.assertEqual(len(xs),3)
        self.assertEqual(sum(x["hit"] for x in xs),1)

    def test_threshold_counts_fall_as_price_haircut_rises(self):
        report=sensitivity(self.aligned)
        one_point_one=[
            next(x for x in scenario["thresholds"]
                 if x["frozenReferenceEVThreshold"]==1.1)["candidateCount"]
            for scenario in report["scenarios"]
        ]
        self.assertEqual(one_point_one,[2,1,1,0,0])
        self.assertFalse(report["marketPriceAtT10Verified"])
        self.assertFalse(report["expectedValueAdvantageValidated"])
        self.assertFalse(report["productionModelChanged"])

    def test_daily_probability_calibration_is_diagnostic_only(self):
        result=sensitivity(self.aligned)
        days=result["byRaceDateCalibrationDiagnostic"]
        self.assertEqual(len(days),1)
        self.assertEqual(days[0]["raceDate"],"2026-11-07")
        self.assertEqual(days[0]["candidateCount"],3)
        self.assertEqual(days[0]["observedHits"],1)
        self.assertAlmostEqual(days[0]["sumFrozenPredictedHits"],0.45)

    def test_unsettled_reference_excluded_not_marked_loss(self):
        other=copy.deepcopy(self.aligned["races"][0])
        other["raceID"]="202605050108"
        other["referenceSettled"]=False
        other["resultState"]="pending"
        self.aligned["races"].append(other)
        out=sensitivity(self.aligned)
        self.assertEqual(out["referenceCandidatesSettled"],3)
        self.assertEqual(out["referenceCandidatesNotYetSettled"],3)
        self.assertEqual(out["allReferenceObservedHits"],1)

    def test_bad_alignment_gate_blocks_report(self):
        self.aligned["aligned"]["countsAreCurrent"]=False
        with self.assertRaisesRegex(ValueError,"not verified"):
            sensitivity(self.aligned)

    def test_mismatched_hit_fail_closed(self):
        self.aligned["races"][0]["commonWideReference"][0]["hit"]=False
        with self.assertRaisesRegex(ValueError,"hit mismatched"):
            sensitivity(self.aligned)

    def test_reported_ev_mismatch_fail_closed(self):
        self.aligned["races"][0]["commonWideReference"][0]["expectedMultiplierLow"]=4.0
        with self.assertRaisesRegex(ValueError,"EV mismatch"):
            sensitivity(self.aligned)

    def test_invalid_candidate_identity_rejected(self):
        self.aligned["races"][0]["commonWideReference"][0]["candidateID"]="0101"
        with self.assertRaisesRegex(ValueError,"candidate not resolved"):
            sensitivity(self.aligned)

    def test_forbid_negative_haircut(self):
        with self.assertRaisesRegex(ValueError,"invalid stress haircut"):
            sensitivity(self.aligned,haircuts=[-0.1])

    def test_results_alone_do_not_define_stress_thresholds(self):
        base=sensitivity(self.aligned)
        alternative=copy.deepcopy(self.aligned)
        alternative["races"][0]["actualFinish"]={"1":5,"2":6,"3":7,"4":8}
        for ref in alternative["races"][0]["commonWideReference"]:
            ref["hit"]=False
        changed=sensitivity(alternative)
        for i in range(len(base["scenarios"])):
            for k in range(len(base["scenarios"][i]["thresholds"])):
                self.assertEqual(
                    base["scenarios"][i]["thresholds"][k]["candidateCount"],
                    changed["scenarios"][i]["thresholds"][k]["candidateCount"]
                )


if __name__=="__main__":
    unittest.main()
