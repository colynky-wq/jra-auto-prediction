import unittest
import numpy as np
import pandas as pd
from evaluate_no_current_odds_longshot_2026 import evaluate, METHODS

class LongshotTests(unittest.TestCase):
    def test_equal_shortlist_count_and_no_false_losses(self):
        d=pd.DataFrame({"レースID":["a"]*4+["b"]*3,"日付":pd.to_datetime(["2026-01-04"]*4+["2026-01-05"]*3),
            "馬番":[1,2,3,4,1,2,3],"人気":[6,7,8,1,6,7,2],"単勝オッズ":[12.,22.,60.,2.,15.,30.,4.],
            "着順":[4,2,3,1,1,4,2]})
        market=np.array([.3,.2,.1,.9,.3,.2,.8])
        independent=np.array([.1,.4,.3,.9,.4,.2,.8])
        result=evaluate(d,{"market":market,"formalReplay":market,"independent":independent,
                           "independentMinusMarket":independent-market})["all"]
        self.assertEqual(result["horses"],5)
        self.assertEqual(result["races"],2)
        s=result["fixedShortlistSizes"]
        self.assertEqual(s["market"][1]["hits"],1)
        self.assertEqual(s["independent"][1]["hits"],2)
        self.assertEqual(s["independent"][1]["selections"],s["market"][1]["selections"])
        self.assertEqual(s["market"][3]["selections"],5)
        self.assertEqual(s["independent"][1]["pairedRaceHitDeltaVsMarket"],.5)
        self.assertIsNone(s["independent"][1]["wholeDateBootstrap95"])

if __name__=="__main__":
    unittest.main()
