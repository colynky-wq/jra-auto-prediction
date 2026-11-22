"""Safety regression for first-T10 market-anchor recording."""
import unittest
from copy import deepcopy
from datetime import datetime
from tempfile import TemporaryDirectory

from market_shrink_shadow import JST, fingerprint
from no_current_odds_shadow import SHADOW_VERSION
from market_anchor_shadow import build, record_best_effort, output_path
from train_market_anchored_challenger import VERSION


def fixture():
    race={
      "raceDate":"2026-11-23", "raceID":"202601010101",
      "startTime":"10:00", "capturedAtJST":"2026-11-23T09:50:00+09:00",
      "predictionTiming":"live_10min", "captureQuality":"formal_10min",
      "horses":[
        {"number":1,"marketProbability":60.0},
        {"number":2,"marketProbability":30.0},
        {"number":3,"marketProbability":10.0},
      ]}
    strategy={
        "version":VERSION,"parametersSelectedFrom":"2024-only",
        "candidatePromotionApproved":False, "independentModelSHA256":"model-test",
        "strategies":{
          "marketCalibrated":{
            "marketPower":1.05,"historicalWeight":0.0,"method":"linear"},
          "historyAugmented":{
            "marketPower":1.05,"historicalWeight":0.02,"method":"linear"},
        },
    }
    snapshot={
        "version":SHADOW_VERSION, "raceDate":race["raceDate"],
        "raceID":race["raceID"], "capturedAtJST":race["capturedAtJST"],
        "sourcePredictionSha256":fingerprint(race),
        "researchOnly":True,"candidatePromotionApproved":False,
        "autoBetEnabled":False,"modelSha256":"model-test",
        "candidateHorses":[
           {"number":1,"winPct":30.0},
           {"number":2,"winPct":50.0},
           {"number":3,"winPct":20.0},
        ],
    }
    snapshot["recordFingerprint"]=fingerprint(snapshot)
    return race,strategy,snapshot


def now():
    return datetime(2026,11,23,9,50,5,tzinfo=JST)


class MarketAnchoredFirstT10Tests(unittest.TestCase):
    def test_market_calibration_saved_even_without_history_model(self):
        race,strategy,_=fixture()
        report=build(race,strategy,"frozen-SHA",clock=now)
        self.assertEqual(set(report["candidateHorsesByStrategy"]),{"marketCalibrated"})
        self.assertEqual(report["independentHistoricalStatus"],
                         "no_independently_frozen_no_odds_candidate")
        self.assertFalse(report["autoBetEnabled"])
        self.assertFalse(report["candidatePromotionApproved"])
        self.assertEqual(report["sourcePredictionSha256"],fingerprint(race))

    def test_independent_history_tilt_is_same_time_first_frozen(self):
        race,strategy,snapshot=fixture()
        r=build(race,strategy,"frozen-SHA",snapshot,clock=now)
        self.assertEqual(set(r["candidateHorsesByStrategy"]),
                         {"marketCalibrated","historyAugmented"})
        a=r["candidateHorsesByStrategy"]["marketCalibrated"]
        b=r["candidateHorsesByStrategy"]["historyAugmented"]
        self.assertNotEqual([x["winPct"] for x in a],
                            [x["winPct"] for x in b])
        self.assertEqual(r["sourceNoOddsShadowFingerprint"],
                         snapshot["recordFingerprint"])
        self.assertFalse(r["formalPredictionAIChanged"])

    def test_late_snapshot_forgery_and_backfill_are_blocked(self):
        race,strategy,snapshot=fixture()
        bad=deepcopy(snapshot)
        bad["capturedAtJST"]="2026-11-23T09:51:00+09:00"
        with self.assertRaisesRegex(ValueError,"identity conflict"):
            build(race,strategy,"frozen-SHA",bad,clock=now)
        bad=deepcopy(race)
        bad["capturedAtJST"]="2026-11-23T09:53:00+09:00"
        with self.assertRaisesRegex(ValueError,"timestamp"):
            build(bad,strategy,"frozen-SHA",clock=now)
        with TemporaryDirectory() as tmp:
            out=record_best_effort(tmp,race,first_formal_capture=False,clock=now)
            self.assertEqual(out["status"],"missing_first_capture_no_backfill")
            self.assertFalse(output_path(tmp,race).exists())


if __name__=="__main__":
    unittest.main()
