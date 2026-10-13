import unittest
import pandas as pd
from research_prerace_pace import replay, references, prepare, FEATURES

def lap(rid,day,t=36):
    return dict(raceID=rid,raceDate=day,surface='芝',distanceM=1200,earlyRecordedDistanceM=600,earlyRecordedTimeSeconds=t,going='良',validation={'ok':True})

def history():
    rows=[]
    for day,rid,pos in [('2023-12-01','202305010101',1),('2023-12-10','202305010102',2),('2023-12-20','202305010103',3),('2024-01-01','202405010101',1),('2024-01-01','202405010102',3),('2024-01-02','202405010103',2)]:
        for n in range(1,4):rows.append({'レースID':rid,'日付':day,'馬番':n,'馬キー':str(n),'初角':pos,'頭数':3,'障害':False,'芝ダ':'芝','距離':1200})
    return pd.DataFrame(rows)

class TestPreracePace(unittest.TestCase):
    def test_current_results_do_not_change_features(self):
        h=history(); laps=[lap('202405010101','2024-01-01'),lap('202405010102','2024-01-01'),lap('202405010103','2024-01-02')]
        a=replay(h,laps)
        h.loc[h['日付']=='2024-01-01','初角']=3
        laps[0]['earlyRecordedTimeSeconds']=45
        b=replay(h,laps)
        pd.testing.assert_frame_equal(a.loc[a.day=='2024-01-01',FEATURES],b.loc[b.day=='2024-01-01',FEATURES])
        self.assertTrue((a.latestSourceDay<a.day).all())
        self.assertEqual(a.iloc[0].pastLapCoverage,0)
        self.assertEqual(a.iloc[1].pastLapCoverage,0)
        self.assertEqual(a.iloc[2].pastLapCoverage,1)
    def test_future_and_other_horse_keys_do_not_enter(self):
        h=history(); laps=[lap('202405010101','2024-01-01')]
        a=replay(h,laps)
        extra=h.iloc[:3].copy(); extra['日付']='2026-01-01'; extra['レースID']='202605010101'
        pd.testing.assert_frame_equal(a,replay(pd.concat([h,extra]),laps))
        h.loc[h['日付']<'2024-01-01','馬キー']='unrelated'
        self.assertFalse(replay(h,laps).eligible.iloc[0])
    def test_duplicates_rejected(self):
        h=history()
        with self.assertRaises(ValueError):replay(pd.concat([h,h.iloc[:1]]),[])
        r=lap('202405010101','2024-01-01')
        with self.assertRaises(ValueError):replay(h,[r,r])
    def test_going_does_not_change_input(self):
        h=history(); r=lap('202405010101','2024-01-01')
        a=replay(h,[r]); r['going']='重'
        pd.testing.assert_frame_equal(a,replay(h,[r]))
    def test_reference_minimum(self):
        rows=pd.DataFrame([dict(venue='05',surface='芝',distanceM=1200,earlyDistanceM=600,actualSeconds=float(35+i/100)) for i in range(30)])
        self.assertEqual(references(rows.iloc[:29]),{})
        self.assertEqual(len(references(rows)),1)
    def test_inputs_exclude_result_fields(self):
        self.assertFalse(set(FEATURES)&{'actualSeconds','初角','着順','going','単勝オッズ','上がり3F'})

if __name__=='__main__':unittest.main()
