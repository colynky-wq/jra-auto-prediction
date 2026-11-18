"""Synthetic tests for race-level Wide calibration; no data-driven retuning."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from wide_reference_calibration_audit import (
    bootstrap_by_cluster,group_by_race,moment,report,
)


class WideReferenceCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.aligned={
            "projectionState":"verified",
            "aligned":{"countsAreCurrent":True},
            "races":[
                {
                    "raceDate":"2026-11-07","raceID":"202605050107",
                    "resultState":"matched","referenceSettled":True,
                    "actualFinish":{"1":1,"2":2,"3":4,"4":3},
                    "commonWideReference":[
                        {"candidateID":"0102","probabilityPct":20.0,
                         "oddsLow":7.5,"expectedMultiplierLow":1.5,"hit":True},
                        {"candidateID":"0103","probabilityPct":15.0,
                         "oddsLow":8.0,"expectedMultiplierLow":1.2,"hit":False},
                        {"candidateID":"0304","probabilityPct":10.0,
                         "oddsLow":10.0,"expectedMultiplierLow":1.0,"hit":False},
                    ],
                },
                {
                    "raceDate":"2026-11-08","raceID":"202605050108",
                    "resultState":"matched","referenceSettled":True,
                    "actualFinish":{"1":3,"2":4,"3":1,"4":2},
                    "commonWideReference":[
                        {"candidateID":"0102","probabilityPct":30.0,
                         "oddsLow":5.0,"expectedMultiplierLow":1.5,"hit":False},
                        {"candidateID":"0203","probabilityPct":8.0,
                         "oddsLow":15.0,"expectedMultiplierLow":1.2,"hit":False},
                    ]
                },
            ]
        }

    def test_report_uses_reference_events_and_counts_race_clusters(self):
        x=report(self.aligned,reps=50)
        self.assertEqual(x["settledReferenceRaceCount"],2)
        self.assertEqual(x["settledReferenceDayCount"],2)
        self.assertEqual(x["allCandidateStatistics"]["candidateCount"],5)
        self.assertEqual(x["allCandidateStatistics"]["predictedHitCount"],0.83)
        self.assertEqual(x["allCandidateStatistics"]["observedHitCount"],1)
        self.assertEqual(x["raceClusterBootstrap"]["clusterCount"],2)
        self.assertFalse(x["candidateEventsIndependentAssumption"])
        self.assertFalse(x["marketExpectedValueValidated"])
        self.assertFalse(x["productionModelChanged"])

    def test_uses_fixed_probability_bands_before_result_data(self):
        bands=report(self.aligned,reps=10)["fixedProbabilityBins"]
        self.assertEqual([x["candidateCount"] for x in bands],[0,1,2,2])
        self.assertEqual(bands[1]["predictedHitCount"],0.08)
        self.assertEqual(bands[3]["observedHitCount"],1)

    def test_datewise_leave_one_out(self):
        x=report(self.aligned,reps=10)
        exclude_first=x["leaveOneDayOut"][0]
        self.assertEqual(exclude_first["excludedRaceDate"],"2026-11-07")
        self.assertEqual(exclude_first["candidateCount"],2)
        self.assertEqual(exclude_first["predictedHitCount"],0.38)
        self.assertEqual(exclude_first["observedHitCount"],0)
        self.assertEqual(x["byDay"][0]["observedHitCount"],1)

    def test_race_bootstrap_keeps_overlapping_pairs_together(self):
        # A one-race cluster always resamples both outcomes as a single unit.
        same_race={
            ("2026-11-07","R1"):[
                {"raceDate":"2026-11-07","raceID":"R1",
                 "candidateID":"0102","probability":0.2,"hit":True},
                {"raceDate":"2026-11-07","raceID":"R1",
                 "candidateID":"0103","probability":0.3,"hit":False},
            ]
        }
        b=bootstrap_by_cluster(same_race,reps=50)
        self.assertEqual(b["observedToPredictedHitRatioDescriptivePercentiles"],[2.0,2.0])
        self.assertEqual(b["hitRateGapPpDescriptivePercentiles"],[25.0,25.0])

    def test_full_date_block_resampling_has_a_separate_label(self):
        result=report(self.aligned,reps=50)
        dates=result["dayBlockBootstrap"]
        races=result["raceClusterBootstrap"]
        self.assertEqual(dates["clusterCount"],2)
        self.assertEqual(races["clusterCount"],2)
        self.assertIn("entire_race_date",dates["resampleUnit"])
        self.assertIn("entire_race_keeping",races["resampleUnit"])
        self.assertFalse(dates["formalConfidenceInterval"])

    def test_unknown_resample_unit_rejected(self):
        with self.assertRaisesRegex(ValueError,"cluster unit"):
            bootstrap_by_cluster({},unit="ticket")

    def test_bootstrap_is_seeded_and_reproducible(self):
        rows=[
            {"raceDate":"2026-11-07","raceID":"R1","candidateID":"0102",
             "probability":0.2,"hit":True},
            {"raceDate":"2026-11-08","raceID":"R2","candidateID":"0103",
             "probability":0.3,"hit":False},
        ]
        groups=group_by_race(rows)
        self.assertEqual(bootstrap_by_cluster(groups,reps=100,seed=81),
                         bootstrap_by_cluster(groups,reps=100,seed=81))

    def test_duplicate_pair_within_race_is_rejected(self):
        xs,_=self._rows()
        with self.assertRaisesRegex(ValueError,"duplicated Wide"):
            group_by_race(xs+[copy.deepcopy(xs[0])])

    def test_invalid_alignment_gate_stops_audit(self):
        self.aligned["projectionState"]="failed"
        with self.assertRaisesRegex(ValueError,"not verified"):
            report(self.aligned)

    def test_wrong_hit_label_is_rejected(self):
        self.aligned["races"][0]["commonWideReference"][0]["hit"]=False
        with self.assertRaisesRegex(ValueError,"hit mismatched"):
            report(self.aligned)

    def test_no_formal_significance_claims(self):
        x=report(self.aligned,reps=20)
        self.assertFalse(x["formalStatisticalSignificanceEstablished"])
        self.assertFalse(x["raceClusterBootstrap"]["formalConfidenceInterval"])
        self.assertFalse(x["raceClusterBootstrap"]["preregisteredCalibrationTest"])

    def test_reps_must_be_positive(self):
        with self.assertRaisesRegex(ValueError,"bad bootstrap"):
            report(self.aligned,reps=0)

    def test_moment_empty_band_no_division_by_zero(self):
        x=moment([])
        self.assertIsNone(x["observedToPredictedHitRatio"])
        self.assertIsNone(x["brierScoreSelectedCandidates"])

    def _rows(self):
        from zero_cost_wide_stress_audit import collect_frozen_wide_reference
        return collect_frozen_wide_reference(self.aligned)


if __name__=="__main__":
    unittest.main()
