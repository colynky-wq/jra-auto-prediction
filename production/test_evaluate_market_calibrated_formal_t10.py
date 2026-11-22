"""Network-free safety tests for same-timestamp archived formal AI comparison."""
import unittest

import numpy as np

from evaluate_market_calibrated_formal_t10 import (
    calibrated_probabilities, evaluate,
)
from train_market_anchored_challenger import VERSION


def fixture():
    race={"raceDate":"2026-11-22","raceID":"202603030101",
          "startTime":"10:00","capturedAtJST":"2026-11-22T09:50:00+09:00",
          "predictionTiming":"live_10min","captureQuality":"formal_10min",
          "horses":[
              {"number":1,"marketProbability":60.0,"aiProbability":50.0,
               "marketTop2Probability":90.0,"aiTop2Probability":85.0,
               "marketTop3Probability":99.0,"aiTop3Probability":98.0},
              {"number":2,"marketProbability":30.0,"aiProbability":35.0,
               "marketTop2Probability":70.0,"aiTop2Probability":75.0,
               "marketTop3Probability":99.0,"aiTop3Probability":99.0},
              {"number":3,"marketProbability":10.0,"aiProbability":15.0,
               "marketTop2Probability":40.0,"aiTop2Probability":40.0,
               "marketTop3Probability":99.0,"aiTop3Probability":99.0},
          ]}
    audit={"projectionState":"verified","generatedAtJST":"2026-11-22T21:00:00+09:00",
           "aligned":{"countsAreCurrent":True,"racesObserved":1,"matchedResultRaces":1},
           "quality":{"resultExclusions":[]},
           "races":[{"raceDate":race["raceDate"],"raceID":race["raceID"],
                     "resultState":"matched","inputFingerprint":"SAMPLE",
                     "actualFinish":{"1":1,"2":2,"3":3}}]}
    strategy={"version":VERSION,"parametersSelectedFrom":"2024-only",
              "researchOnly":True,"candidatePromotionApproved":False,
              "autoBetEnabled":False,"formalPredictionAIChanged":False,
              "strategies":{"marketCalibrated":{
                  "marketPower":1.05,"historicalWeight":0.0,"method":"linear"}}}
    return race,audit,strategy


class FormalT10CalibrationTests(unittest.TestCase):
    def test_market_power_changes_distribution_but_keeps_rank_and_probability(self):
        p=calibrated_probabilities([1,2,3],[0.6,0.3,0.1],1.05)
        self.assertAlmostEqual(float(np.sum(p["win"])),1.0)
        self.assertAlmostEqual(float(np.sum(p["top2"])),2,places=5)
        self.assertAlmostEqual(float(np.sum(p["top3"])),3,places=5)
        self.assertGreater(p["win"][0],0.6)
        self.assertTrue(np.all(p["top2"]>=p["win"]))
        self.assertTrue(np.all(p["top3"]>=p["top2"]))

    def test_paused_unknown_or_nonmatching_predictions_never_promoted(self):
        race,audit,strategy=fixture()
        result=evaluate(".",audit,strategy,load_bundle=lambda *_:{
            "fingerprint":"SAMPLE","prediction":race})
        self.assertEqual(result["counts"]["validPairedRacesWin"],1)
        self.assertEqual(result["byEvent"]["win"]["pairedRaces"],1)
        self.assertFalse(result["autoBetEnabled"])
        self.assertFalse(result["candidatePromotionApproved"])
        self.assertTrue(result["selectionAndScoringSeparated"])
        audit["races"][0]["resultState"]="awaiting_result"
        audit["aligned"]["matchedResultRaces"]=0
        skipped=evaluate(".",audit,strategy)
        self.assertEqual(skipped["counts"]["validPairedRacesWin"],0)
        self.assertEqual(skipped["counts"]["unmatchedOrExcludedRaces"],1)

    def test_invalid_audit_and_frozen_prediction_block(self):
        race,audit,strategy=fixture()
        audit["projectionState"]="failed"
        with self.assertRaises(ValueError):
            evaluate(".",audit,strategy)
        audit["projectionState"]="verified"
        with self.assertRaisesRegex(ValueError,"fingerprint"):
            evaluate(".",audit,strategy,load_bundle=lambda *_:{
                "fingerprint":"CHANGED","prediction":race})
        strategy["parametersSelectedFrom"]="2026-after-results"
        with self.assertRaisesRegex(ValueError,"selection provenance"):
            evaluate(".",audit,strategy,load_bundle=lambda *_:{
                "fingerprint":"SAMPLE","prediction":race})


if __name__ == "__main__":
    unittest.main()
