import json,tempfile,hashlib
from pathlib import Path
from datetime import timedelta
import unittest
import pandas as pd
from test_pace_prospective import fixture,NOW
from pace_prospective_runtime import forecast
from build_pace_recording_status import build,markdown
class TestRecordingStatus(unittest.TestCase):
    def setup_case(self,root):
        h,laps,o,m,actual=fixture();folder=root/'reports/20261017/prerace'/o['raceID'];folder.mkdir(parents=True)
        (folder/'index.json').write_text('{}');snap=folder/'sample.json';snap.write_text(json.dumps(o))
        models=root/'models';models.mkdir();mf=models/'model.json';mf.write_text(json.dumps(m))
        pred=forecast(o,h,laps,m,NOW);pred.update(snapshotFile='sample.json',snapshotSHA256=hashlib.sha256(snap.read_bytes()).hexdigest(),modelFileSHA256=hashlib.sha256(mf.read_bytes()).hexdigest())
        history=pd.DataFrame({'レースID':[o['raceID']]*3,'日付':[o['raceDate']]*3,'馬番':[1,2,3],'障害':[False]*3})
        return folder,pred,actual,history,o
    def run_case(self,root,history,now=NOW):return build(root/'reports',root/'laps',history,root/'models',now)
    def test_saved_then_matched(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root);(folder/'PacePrediction.json').write_text(json.dumps(pred))
            data=self.run_case(root,h);self.assertEqual(data['races'][0]['recordingState'],'saved');self.assertEqual(data['races'][0]['resultState'],'awaiting_result')
            p=root/'laps/20261017';p.mkdir(parents=True);(p/'RaceLapData.json').write_text(json.dumps({'races':[actual]}))
            self.assertEqual(self.run_case(root,h)['counts']['matched'],1)
    def test_missing_and_capture_failure_visible(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            self.assertEqual(self.run_case(root,h)['races'][0]['recordingState'],'missing')
            e=folder/'events';e.mkdir();(e/'event.json').write_text(json.dumps({'raceID':o['raceID'],'raceDate':o['raceDate'],'atJST':NOW.isoformat(),'stage':'capture_failed','detail':'x'}))
            self.assertEqual(self.run_case(root,h)['races'][0]['recordingState'],'failed')
    def test_waiting_roster_includes_races_without_observations(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'reports/20261017';p.mkdir(parents=True)
            r={'raceID':'202605040601','raceDate':'2026-10-17','venue':'東京','raceNumber':1,'startTime':'09:50','scheduledPostJST':(NOW+timedelta(minutes=10)).isoformat()}
            (p/'PaceRecordingSchedule.json').write_text(json.dumps({'raceDate':r['raceDate'],'races':[r]}))
            h=pd.DataFrame(columns=['レースID','日付','馬番','障害'])
            self.assertEqual(self.run_case(root,h)['races'][0]['recordingState'],'waiting')
            self.assertEqual(self.run_case(root,h,NOW+timedelta(minutes=11))['races'][0]['recordingState'],'missing')
    def test_corrupt_and_late_records_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root);p=folder/'PacePrediction.json'
            p.write_text('{bad');self.assertEqual(self.run_case(root,h)['counts']['invalid'],1)
            pred['predictedAtJST']=pred['scheduledPostJST'];p.write_text(json.dumps(pred));self.assertEqual(self.run_case(root,h)['counts']['invalid'],1)
    def test_held_reason_is_plain_language(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root);pred['status']='held';pred['holdReason']='insufficient_style_history';(folder/'PacePrediction.json').write_text(json.dumps(pred))
            data=self.run_case(root,h);self.assertEqual(data['counts']['held'],1);self.assertIn('脚質記録',data['races'][0]['reason'])
    def test_empty_first_day_and_old_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);h=pd.DataFrame({'レースID':['202605040501'],'日付':['2026-10-12'],'馬番':[1],'障害':[False]})
            x=self.run_case(root,h);self.assertEqual(x['counts']['totalRaces'],0);self.assertIn('まだ保存',markdown(x));self.assertFalse(x['paceForecastEnabled'])
if __name__=='__main__':unittest.main()
