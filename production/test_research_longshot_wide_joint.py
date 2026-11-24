import unittest
import numpy as np
import pandas as pd
from research_longshot_wide_joint import joint,attach_payout,settlement

class TestWideJoint(unittest.TestCase):
    def test_uniform_joint(self):
        p=joint(np.ones(8))
        np.testing.assert_allclose(p[np.triu_indices(8,1)],3/28)
        self.assertAlmostEqual(np.triu(p,1).sum(),3)

    def test_joint_not_independent(self):
        p=joint([.5,.3,.15,.05])
        self.assertTrue(np.allclose(p,p.T))
        self.assertTrue((p>=0).all())
        self.assertAlmostEqual(np.triu(p,1).sum(),3)

    def test_missing_payoff_not_loss(self):
        d=pd.DataFrame({"raceID":["a","a"],"date":pd.to_datetime(["2026-01-01"]*2),
            "a":[1,1],"b":[2,3],"nameA":["A","A"],"nameB":["B","C"],"hit":[1,0],
            "market":[.2,.1],"v2Joint":[.2,.1]})
        archive={"a":{"date":"2026-01-01","pairs":{(1,2):("A","B",320)}}}
        g,_=attach_payout(d,archive)
        self.assertEqual(len(g),1)
        out=settlement(g,("market","v2Joint"),True)
        self.assertEqual(out["1"]["market"]["returnPct"],320)
        self.assertNotIn("3",out)

if __name__=="__main__":
    unittest.main()
