"""Network-free regression: no hindsight calibration and honest paired ranking."""
import hashlib
import unittest

import numpy as np
import pandas as pd

from train_longshot_top3 import VERSION as LONGSHOT_VERSION, FEATURES
from research_longshot_market_calibration_ablation import (
    OFFSET_GRID, choose_offset, simple_market_calibration,
    score_period, verify_meta
)


class LongshotBaselineAblationTests(unittest.TestCase):
    def test_market_intercept_cannot_change_order_and_zero_is_identity(self):
        raw=np.array([.05,.1,.2,.01])
        np.testing.assert_allclose(simple_market_calibration(raw,0.0),raw)
        for offset in OFFSET_GRID:
            x=simple_market_calibration(raw,offset)
            self.assertEqual(np.argsort(x).tolist(),np.argsort(raw).tolist())
            self.assertTrue((x>0).all() and (x<1).all())
        with self.assertRaisesRegex(ValueError,"unregistered"):
            simple_market_calibration(raw,0.57)
        with self.assertRaisesRegex(ValueError,"invalid"):
            simple_market_calibration([0.3,float("nan")],0.2)

    def test_offset_selector_uses_only_2024(self):
        n=2000
        d=pd.DataFrame({
            "日付":pd.to_datetime(["2024-01-06"]*n),
            "着順":np.where(np.arange(n)%13==0,3,10),
            "市場3着内基準":np.repeat(.05,n)
        })
        choice,grid=choose_offset(d)
        self.assertIn(choice["logitIntercept"],OFFSET_GRID)
        self.assertEqual(len(grid),len(OFFSET_GRID))
        d.loc[0,"日付"]=pd.Timestamp("2025-01-01")
        with self.assertRaisesRegex(ValueError,"2024 exclusively"):
            choose_offset(d)

    def test_labeled_race_ranking_is_paired_and_unchanged_by_calibration(self):
        sample=pd.DataFrame({
            "レースID":["R1"]*3+["R2"]*3,
            "馬番":[1,2,3,1,2,3],
            "着順":[3,10,12,5,2,10],
            "市場3着内基準":[.25,.10,.04,.18,.12,.04],
        })
        # Different favorite in each race: AI switches to market's true winner
        # in R2, but switches away from it in R1.
        predictions=np.asarray([.20,.30,.10,.12,.25,.05])
        out=score_period(sample,predictions,.2,"test")
        self.assertEqual(out["races"],2)
        self.assertEqual(out["horseRows"],6)
        self.assertEqual(out["rankingAmongLongshots"]["changedTopHorse"],2)
        self.assertEqual(out["rankingAmongLongshots"]["marketOnlyHit"],1)
        self.assertEqual(out["rankingAmongLongshots"]["candidateOnlyHit"],1)
        self.assertEqual(out["topRankedLongshotPodiumHits"],
                         {"marketAndSimple":1,"longshotResidualAI":1})
        self.assertEqual(out["pairedRaceBootstrap95"],{})
        self.assertFalse(np.isnan(out["aiMinusSimpleMarketBrier"]))

    def test_model_provenance_blocks_2026_leakage_and_activation(self):
        raw=b"frozen-only"
        metadata={
            "version":LONGSHOT_VERSION,
            "modelSha256":hashlib.sha256(raw).hexdigest(),
            "features":list(FEATURES),
            "trainThroughFor2025Test":"2023-12-31",
            "yearUsedForEarlyStoppingAndMultiplier":2024,
            "refitThroughFor2026Test":"2025-12-31",
            "selectedResidualMultiplier":1.0,
            "candidatePromotionApproved":False,"autoBetEnabled":False,
            "formalPredictionAIChanged":False,
        }
        verify_meta(metadata,raw)
        metadata["refitThroughFor2026Test"]="2026-11-23"
        with self.assertRaisesRegex(ValueError,"untrusted"):
            verify_meta(metadata,raw)


if __name__=="__main__":
    unittest.main()
