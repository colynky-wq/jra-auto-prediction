import unittest
import numpy as np
import pandas as pd
from research_longshot_top3_v7 import grouped,picks,blend,hit_arrays,fit,FEATURES

def fixture():
    d=pd.DataFrame({"レースID":["b","a","a","b","a"],"馬番":[2,3,1,1,2],
        "日付":pd.to_datetime(["2024-01-02","2024-01-01","2024-01-01","2024-01-02","2024-01-01"]),
        "着順":[2,5,1,4,3]})
    for f in FEATURES:d[f]=0.
    return d

class RankingTests(unittest.TestCase):
    def test_contiguous_query_and_tie_break(self):
        d=fixture();g,n=grouped(d)
        self.assertEqual(n.tolist(),[3,2])
        self.assertEqual(g["馬番"].tolist(),[1,2,3,1,2])
        order,pos,_,_=picks(d,np.ones(len(d)))
        self.assertEqual(d.iloc[order[pos==0]]["馬番"].tolist(),[1,1])
        self.assertEqual(hit_arrays(d,np.ones(len(d)))[2][1].tolist(),[2,1])

    def test_no_result_required_for_rank_blend(self):
        d=fixture();p=np.array([.2,.1,.3,.4,.2]);s=np.array([1.,2.,3.,4.,5.])
        a=blend(d,p,s,.5)
        b=blend(d.drop(columns=["着順"]),p,s,.5)
        np.testing.assert_array_equal(a,b)
        np.testing.assert_array_equal(blend(d,p,s,0.),p)
        self.assertTrue(np.isfinite(blend(d,p,np.ones(len(d)),1.)).all())

    def test_reject_future_training(self):
        with self.assertRaises(ValueError):
            fit(fixture(),"lambdarank",80,2023)

if __name__=="__main__":
    unittest.main()
