import unittest
import pandas as pd
from research_longshot_market_matched_contrast import contrasts

class ContrastTest(unittest.TestCase):
    def test_only_same_race_and_market_band_matched(self):
        d=pd.DataFrame({"日付":pd.to_datetime(["2026-01-04"]*4+["2026-01-05"]*2),
                        "レースID":["a"]*4+["b"]*2,
                        "marketTop3":[.11,.12,.21,.22,.11,.12],
                        "hit":[True,False,True,False,True,True],
                        "signal":[4.,2.,12.,10.,100.,100.]})
        r=contrasts(d,("signal",))["signal"]
        self.assertEqual(r["matchedHits"],2)
        self.assertEqual(r["matchedMisses"],2)
        self.assertEqual(r["matchedRaces"],1)
        self.assertAlmostEqual(r["hitMinusMissMatchedMean"],2.)
        self.assertAlmostEqual(r["matchedHitCoverage"],.5)
        self.assertIsNone(r["wholeDateBootstrap95"])

if __name__=="__main__":
    unittest.main()
