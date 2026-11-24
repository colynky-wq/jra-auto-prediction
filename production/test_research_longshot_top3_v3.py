import unittest
import numpy as np
import pandas as pd
from research_longshot_top3_v3 import fit,predict,FOCUS

class V3Test(unittest.TestCase):
    def test_training_boundary_and_prediction_without_finish(self):
        n=60
        d=pd.DataFrame({"日付":pd.Timestamp("2023-01-01"),"市場3着内基準":np.linspace(.05,.3,n),
            "着順":np.where(np.arange(n)%5==0,2,6)})
        for f in FOCUS:
            d[f]=np.arange(n)%7
        m=fit(d,"focused5",.01)
        p=predict(d.drop(columns=["着順"]),m)
        self.assertTrue(np.isfinite(p).all())
        self.assertTrue(((p>0)&(p<1)).all())
        with self.assertRaisesRegex(ValueError,"2026 forbidden"):
            fit(d.assign(日付=pd.Timestamp("2026-01-01")),"focused5",.01)

if __name__=="__main__":
    unittest.main()
