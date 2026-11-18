"""Synthetic tests for read-only full-field Wide probability validation."""
from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from wide_full_field_calibration_audit import (
    audit_repository, exact_pair_key, extract_race_pairs, score,
)


class FullFieldWideCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.rid="202605050101"
        self.race={
            "raceID":self.rid,"raceDate":"2026-11-07","startTime":"09:45",
            "resultState":"matched",
            "actualFinish":{"1":1,"2":2,"3":3,"4":4},
            "commonWideReference":[
                {"candidateID":"0102","probabilityPct":80.0}],
        }
        self.record={
            "formalPrediction":{
                "raceID":self.rid,"raceDate":"2026-11-07","startTime":"09:45",
                "captureQuality":"formal_10min",
                "capturedAtJST":"2026-11-07T09:34:00+09:00",
                "horseNumbers":[1,2,3,4],
            },
            "ticketProbabilities":{
                "wide":{
                    "0102":0.9,"0103":0.7,"0104":0.5,
                    "0203":0.5,"0204":0.3,"0304":0.1,
                }
            }
        }

    def test_complete_matrix_3_expected_3_observed(self):
        rows=extract_race_pairs(self.record,self.race)
        self.assertEqual(len(rows),6)
        self.assertAlmostEqual(sum(x["probability"] for x in rows),3.0)
        self.assertEqual(sum(x["hit"] for x in rows),3)

    def test_pair_score_compares_to_uniform_baseline(self):
        rows=extract_race_pairs(self.record,self.race)
        x=score(rows)
        self.assertEqual(x["pairs"],6)
        self.assertTrue(x["brierBetterThanUniform"])
        self.assertTrue(x["logLossBetterThanUniform"])
        self.assertAlmostEqual(x["uniformBrier"],0.25)

    def test_missing_pair_rejected(self):
        del self.record["ticketProbabilities"]["wide"]["0304"]
        with self.assertRaisesRegex(ValueError,"incomplete/extra"):
            extract_race_pairs(self.record,self.race)

    def test_extra_pair_rejected(self):
        self.record["ticketProbabilities"]["wide"]["0506"]=0.3
        with self.assertRaisesRegex(ValueError,"incomplete/extra"):
            extract_race_pairs(self.record,self.race)

    def test_bad_probability_normalization_rejected(self):
        self.record["ticketProbabilities"]["wide"]["0102"]=0.6
        with self.assertRaisesRegex(ValueError,"sum not three"):
            extract_race_pairs(self.record,self.race)

    def test_bad_probability_nan_rejected(self):
        self.record["ticketProbabilities"]["wide"]["0102"]=float("nan")
        with self.assertRaisesRegex(ValueError,"nonfinite"):
            extract_race_pairs(self.record,self.race)

    def test_incomplete_actual_finish_rejected(self):
        del self.race["actualFinish"]["3"]
        with self.assertRaisesRegex(ValueError,"missing a predicted horse"):
            extract_race_pairs(self.record,self.race)

    def test_duplicate_horse_number_rejected(self):
        self.record["formalPrediction"]["horseNumbers"]=[1,2,3,3]
        with self.assertRaisesRegex(ValueError,"duplicate"):
            extract_race_pairs(self.record,self.race)

    def test_wrong_formal_capture_fails_closed(self):
        self.record["formalPrediction"]["capturedAtJST"]="2026-11-07T09:20:00+09:00"
        with self.assertRaisesRegex(ValueError,"over 3 minutes"):
            extract_race_pairs(self.record,self.race,
               expected_capture="2026-11-07T09:34:00+09:00")

    def test_research_cohort_identity_and_model_difference(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"verified",
                "aligned":{"countsAreCurrent":True},
                "races":[self.race]
            }))
            day=root/"reports"/"20261107"
            pred=day/"ticket_shadow_v1"/"prediction"
            formal=day/"all_bet_v1"/"win"
            pred.mkdir(parents=True)
            formal.mkdir(parents=True)
            (pred/f"{self.rid}.json").write_text(json.dumps(self.record))
            (formal/f"{self.rid}.json").write_text(json.dumps({
                "prediction":{"capturedAtJST":"2026-11-07T09:34:00+09:00"}
            }))
            r=audit_repository(root)
            self.assertEqual(r["racesAudited"],1)
            self.assertEqual(r["racesExcluded"],0)
            self.assertEqual(r["totalPairsCompared"],6)
            self.assertEqual(r["fullFieldExpectedHits"],3)
            self.assertEqual(r["fullFieldObservedHits"],3)
            self.assertTrue(r["fullFieldExpectedHitTotalIsTautology"])
            self.assertEqual(r["referenceVsFullField"]["matchedPairs"],1)
            self.assertAlmostEqual(r["referenceVsFullField"]["maxAbsProbabilityDifferencePp"],10.0)
            self.assertFalse(r["referenceVsFullField"]["sameProbabilityModelAssumed"])
            self.assertFalse(r["oddsSourceFreshnessVerified"])

    def test_missing_shadow_reports_exclusion_not_a_fake_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"verified","aligned":{"countsAreCurrent":True},
                "races":[self.race]}))
            result=audit_repository(root)
            self.assertEqual(result["qualityGate"],"no_verified_full_field_records")
            self.assertEqual(result["excluded"][0]["reason"],"missing_frozen_ticket_shadow")

    def test_unverified_projection_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"AlignedPortfolioReview.json").write_text(json.dumps({
                "projectionState":"failed","aligned":{"countsAreCurrent":False},
                "races":[]}))
            with self.assertRaisesRegex(ValueError,"not verified/current"):
                audit_repository(root)


if __name__=="__main__":
    unittest.main()
