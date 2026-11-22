"""Verify that longshot first-T10 forecast cannot read a result or backfill."""
from copy import deepcopy
from datetime import datetime
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from market_shrink_shadow import JST, fingerprint
from train_longshot_top3 import FEATURES, VERSION, WEIGHTS
from longshot_top3_shadow import build, record_best_effort, path_for


class FakeBooster:
    def feature_name(self):
        return list(FEATURES)
    def predict(self,x,raw_score=False,pred_contrib=False,validate_features=True):
        assert validate_features
        assert "市場ロジット" not in x
        raw=np.repeat(.4,len(x))
        if pred_contrib:
            result=np.zeros((len(x),len(FEATURES)+1))
            result[:,0]=.4
            return result
        assert raw_score
        return raw


def samples():
    odds=[2.,4.,6.,8.,9.,10.,20.,30.]
    market3=[80.,65.,45.,35.,30.,25.,15.,10.]
    race={
        "raceDate":"2026-11-23","raceID":"202601010101",
        "startTime":"10:00","capturedAtJST":"2026-11-23T09:50:00+09:00",
        "predictionTiming":"live_10min","captureQuality":"formal_10min",
        "horses":[{
            "number":i+1,"name":f"horse{i+1}","popularity":i+1,
            "odds":odds[i],"marketTop3Probability":market3[i],
            "aiTop3Probability":market3[i],
        } for i in range(8)]
    }
    features={
        "featureNames":list(FEATURES),
        "horseNumbers":[i+1 for i in range(8)],
        "rows":[[float(i)]*len(FEATURES) for i in range(8)],
    }
    meta={
        "version":VERSION,
        "features":list(FEATURES),
        "selectedResidualMultiplier":1.0,
        "modelSha256":"test-only-SHA",
        "candidatePromotionApproved":False,
    }
    return race,features,meta


def clock():
    return datetime(2026,11,23,9,50,10,tzinfo=JST)


class LongshotFirstT10Tests(unittest.TestCase):
    def test_immutable_frozen_forecast_has_horse_specific_explanations(self):
        race,features,meta=samples()
        original=deepcopy(race)
        out=build(race,features,meta,FakeBooster(),clock=clock)
        self.assertEqual(race,original)
        self.assertEqual(out["qualifyingLongshotCount"],3)
        self.assertEqual([h["number"] for h in out["candidateHorses"]],[6,7,8])
        self.assertFalse(out["autoBetEnabled"])
        self.assertFalse(out["candidatePromotionApproved"])
        self.assertFalse(out["formalPredictionAIChanged"])
        self.assertEqual(out["sourcePredictionSha256"],fingerprint(race))
        self.assertGreater(out["candidateHorses"][0]["researchTop3Pct"],
                           out["candidateHorses"][0]["marketTop3Pct"])
        self.assertEqual(
            out["candidateHorses"][0]["positiveModelAssociations"][0]["feature"],
            FEATURES[0]
        )

    def test_first_capture_late_and_mismatched_horse_order_blocked(self):
        race,features,meta=samples()
        bad=deepcopy(race)
        bad["capturedAtJST"]="2026-11-23T09:53:00+09:00"
        with self.assertRaisesRegex(ValueError,"window expired"):
            build(bad,features,meta,FakeBooster(),clock=clock)
        bad=deepcopy(features)
        bad["horseNumbers"]=[8,7,6,5,4,3,2,1]
        with self.assertRaisesRegex(ValueError,"entrant ordering"):
            build(race,bad,meta,FakeBooster(),clock=clock)
        with TemporaryDirectory() as tmp:
            skipped=record_best_effort(tmp,race,features,
                                       first_formal_capture=False,clock=clock)
            self.assertEqual(skipped["status"],"missing_first_capture_no_backfill")
            self.assertFalse(path_for(tmp,race).exists())

    def test_zero_learned_weight_cannot_invent_model_reasons(self):
        race,features,meta=samples()
        meta["selectedResidualMultiplier"]=0.0
        self.assertIn(meta["selectedResidualMultiplier"],WEIGHTS)
        out=build(race,features,meta,FakeBooster(),clock=clock)
        for horse in out["candidateHorses"]:
            self.assertAlmostEqual(horse["marketTop3Pct"],horse["researchTop3Pct"],places=5)
            self.assertEqual(horse["positiveModelAssociations"],[])

    def test_no_longshots_is_recordable_research_not_failure(self):
        race,features,meta=samples()
        for h in race["horses"]:
            h["odds"]=5.
        out=build(race,features,meta,FakeBooster(),clock=clock)
        self.assertEqual(out["qualifyingLongshotCount"],0)
        self.assertEqual(out["candidateHorses"],[])


if __name__=="__main__":
    unittest.main()
