import unittest
import numpy as np
import pandas as pd
from longshot_v6_affinity_features import build,NEW,jockey_key

def fixture():
    rows=[]
    for day,rid in [("2025-01-01","202505010101"),("2025-01-02","202505010102"),
                    ("2025-01-03","202505010103")]:
        for n in range(1,5):
            rows.append(dict(zip(("日付","レースID","馬番","馬キー","芝ダ","距離","頭数","障害",
                "着順","単勝オッズ","騎手","出典","タイム秒"),
                (day,rid,n,str(n),"芝",1600,4,False,n,[2.,4.,10.,20.][n-1],
                 "騎手"+str(n),"JRA公式更新",95.+n/10))))
    return pd.DataFrame(rows)

class AffinityTests(unittest.TestCase):
    def test_prior_only_and_market_residual(self):
        d=fixture();a,_=build(d,{})
        changed=d.copy();mask=changed["日付"]=="2025-01-02"
        changed.loc[mask,"着順"]=5-changed.loc[mask,"着順"]
        changed.loc[mask,"単勝オッズ"]=[20.,10.,4.,2.]
        b,_=build(changed,{})
        np.testing.assert_array_equal(a.iloc[:8][list(NEW)],b.iloc[:8][list(NEW)])
        self.assertNotEqual(a.iloc[8]["v6PairMarketResidual"],b.iloc[8]["v6PairMarketResidual"])
        self.assertAlmostEqual(a.iloc[4]["v6HorseCourseLogStarts"],np.log(2))
        self.assertEqual(a.iloc[0]["v6PairLogStarts"],0)

    def test_jockey_source_era_separate(self):
        self.assertNotEqual(jockey_key("騎手1","旧データ"),jockey_key("騎手1","JRA公式更新"))
        self.assertIsNone(jockey_key(None,"JRA公式更新"))

    def test_unavailable_current_outcomes(self):
        d=fixture();a,_=build(d,{})
        d.loc[d["日付"]=="2025-01-03",["着順","単勝オッズ","タイム秒"]]=np.nan
        b,_=build(d,{})
        np.testing.assert_array_equal(a[list(NEW)],b[list(NEW)])

if __name__=="__main__":
    unittest.main()
