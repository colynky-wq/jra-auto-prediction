import unittest
import pandas as pd
import numpy as np
from longshot_v4_context_features import build,NEW

def fixture():
    rows=[]
    for day,rid,ranks in [("2023-01-01","202305010101",[1,2,3]),
                          ("2023-01-02","202305010201",[3,1,2]),
                          ("2023-01-02","202305010202",[2,3,1]),
                          ("2023-01-03","202305010301",[2,1,3])]:
        keys=["a","b","c"] if rid!="202305010202" else ["d","e","f"]
        for i,key in enumerate(keys):
            rows.append(dict(zip(("日付","レースID","馬番","馬キー","芝ダ","距離","頭数","障害",
                     "着順","初角","タイム秒","上がり3F"),
                    (day,rid,i+1,key,"芝",1600,3,False,ranks[i],i+1,96.+ranks[i]/10,34.+i/10))))
    return pd.DataFrame(rows)

class ContextTest(unittest.TestCase):
    def test_same_day_and_future_outcomes_do_not_change_features(self):
        h=fixture(); a=build(h)
        changed=h.copy()
        mask=changed["日付"]>="2023-01-02"
        changed.loc[mask,"着順"]=4-changed.loc[mask,"着順"]
        changed.loc[mask,"初角"]=4-changed.loc[mask,"初角"]
        changed.loc[mask,"タイム秒"]+=20
        changed.loc[mask,"上がり3F"]+=5
        b=build(changed)
        early=a["レースID"]!="202305010301"
        np.testing.assert_array_equal(a.loc[early,list(NEW)],b.loc[early,list(NEW)])
        self.assertTrue(np.isfinite(a[list(NEW)]).all().all())
        self.assertNotEqual(a.iloc[-1]["v4RecentResultSurprise"],b.iloc[-1]["v4RecentResultSurprise"])

    def test_first_race_neutral_history(self):
        d=build(fixture())
        self.assertEqual(d.iloc[0]["v4FormStarts"],0)
        self.assertEqual(d.iloc[0]["v4OwnPositionMean"],.5)
        self.assertEqual(d.iloc[3]["v4FormStarts"],1)

if __name__=="__main__":
    unittest.main()
