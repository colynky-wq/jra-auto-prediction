import unittest
import numpy as np
import pandas as pd
from longshot_v5_lap_features import build,check_record,NEW

def fixture():
    rows=[];laps={}
    for i in range(1,4):
        day=f"2024-01-0{i}";rid=f"20240501010{i}"
        times=[12.,11.,11.,12.,12.,12.]
        r={"raceID":rid,"raceDate":day,"surface":"芝","distanceM":1200,
           "courseDetail":"芝・左","going":"良","sourceURL":"https://www.jra.go.jp/example",
           "sourceSHA256":"a"*64,"lapDistancesM":[200]*6,"lapTimesSeconds":times,
           "earlyRecordedDistanceM":600,"earlyRecordedTimeSeconds":34.,
           "raceLast600mSeconds":36.,"winnerTimeSeconds":70.,
           "validation":dict.fromkeys(("headingMatches","distanceMatches","winnerTimeMatches","raceLast600mMatches"),True)}
        laps[rid]=r
        for n in (1,2,3):
            rows.append({"日付":day,"レースID":rid,"馬番":n,"馬キー":str(n),"芝ダ":"芝",
                        "距離":1200,"頭数":3,"障害":False,"着順":n,"タイム秒":70.+(n-1)*.1,
                        "上がり3F":36.+n*.1})
    return pd.DataFrame(rows),laps

class LapTests(unittest.TestCase):
    def test_no_current_race_or_future_lap_leakage(self):
        h,l=fixture();a,_=build(h,l)
        l.pop("202405010102")
        mask=h["レースID"]=="202405010102"
        h.loc[mask,["着順","タイム秒","上がり3F"]]=np.nan
        b,_=build(h,l)
        np.testing.assert_array_equal(a.iloc[:6][list(NEW)],b.iloc[:6][list(NEW)])
        self.assertEqual(a.iloc[6]["v5LapStarts"],2)
        self.assertEqual(b.iloc[6]["v5LapStarts"],1)

    def test_exact_endpoint_and_bad_identity(self):
        h,l=fixture()
        self.assertTrue(check_record(l["202405010101"]))
        l["202405010101"]["earlyRecordedDistanceM"]=700
        self.assertFalse(check_record(l["202405010101"]))
        a,_=build(h,l)
        self.assertEqual(a.iloc[3]["v5LapStarts"],0)
        self.assertEqual(a.iloc[6]["v5LapStarts"],1)

if __name__=="__main__":
    unittest.main()
