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
    def test_snapshot_identity_checked_before_result(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            o['raceDate']='2026-10-18';snap=folder/'sample.json';snap.write_text(json.dumps(o))
            pred['snapshotSHA256']=hashlib.sha256(snap.read_bytes()).hexdigest();(folder/'PacePrediction.json').write_text(json.dumps(pred))
            self.assertEqual(self.run_case(root,h)['counts']['invalid'],1)
    def test_empty_first_day_and_old_history(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);h=pd.DataFrame({'レースID':['202605040501'],'日付':['2026-10-12'],'馬番':[1],'障害':[False]})
            x=self.run_case(root,h);self.assertEqual(x['counts']['totalRaces'],0);self.assertIn('まだ保存',markdown(x));self.assertFalse(x['paceForecastEnabled'])
class TestRecordingFaults(unittest.TestCase):
    def setup_case(self, root):
        return TestRecordingStatus().setup_case(root)

    def board(self, root, history):
        return TestRecordingStatus().run_case(root, history)

    def saved_pair(self, root):
        import copy
        folder, pred, actual, history, observation = self.setup_case(root)
        (folder/'PacePrediction.json').write_text(json.dumps(pred))
        peer_id = pred['raceID'][:-2]+'02'
        peer_folder = folder.parent/peer_id
        peer_folder.mkdir()
        peer_obs = copy.deepcopy(observation)
        peer_obs['raceID'] = peer_id
        snapshot = peer_folder/'sample.json'
        snapshot.write_text(json.dumps(peer_obs))
        peer_pred = copy.deepcopy(pred)
        peer_pred.update(raceID=peer_id, snapshotSHA256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        (peer_folder/'PacePrediction.json').write_text(json.dumps(peer_pred))
        (peer_folder/'index.json').write_text('{}')
        extra = history.copy()
        extra['レースID'] = peer_id
        return folder, pred, actual, pd.concat([history, extra]), peer_id

    def test_bad_prediction_does_not_stop_healthy_race(self):
        for raw in [b'[1]', b'{}', b'null', b'"wrong"', b'{bad', b'\xff']:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as d:
                root=Path(d);folder,pred,actual,h,peer=self.saved_pair(root)
                (folder/'PacePrediction.json').write_bytes(raw)
                data=self.board(root,h)
                states={r['raceID']:r['recordingState'] for r in data['races']}
                self.assertEqual(states[pred['raceID']],'invalid')
                self.assertEqual(states[peer],'saved')

    def test_orphaned_prediction_survives_without_index_or_schedule(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            path=folder/'PacePrediction.json';path.write_text(json.dumps(pred));before=path.read_bytes()
            (folder/'index.json').unlink()
            data=self.board(root,h.iloc[:0])
            self.assertEqual(data['counts']['saved'],1)
            self.assertEqual(data['races'][0]['startTime'],'09:50')
            self.assertEqual(path.read_bytes(),before)

    def test_broken_index_does_not_erase_valid_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            path=folder/'PacePrediction.json';path.write_text(json.dumps(pred));before=path.read_bytes()
            (folder/'index.json').write_text('{unfinished')
            row=self.board(root,h)['races'][0]
            self.assertEqual(row['recordingState'],'saved')
            self.assertTrue(row['needsAttention']);self.assertTrue(row['issues'])
            self.assertEqual(path.read_bytes(),before)

    def test_malformed_event_keeps_its_race_visible(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            (folder/'index.json').unlink()
            events=folder/'events';events.mkdir();(events/'bad.json').write_text('{"detail":"partial"}')
            row=self.board(root,h.iloc[:0])['races'][0]
            self.assertEqual(row['raceID'],o['raceID'])
            self.assertTrue(row['needsAttention']);self.assertEqual(row['attemptEvents'],1)

    def test_bad_schedule_entry_does_not_hide_good_entries(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);day=root/'reports/20261017';day.mkdir(parents=True)
            good={'raceID':'202605040601','raceDate':'2026-10-17','venue':'東京','raceNumber':1,'startTime':'09:50','scheduledPostJST':(NOW+timedelta(minutes=10)).isoformat()}
            bad={**good,'raceID':'202605040602','raceNumber':2,'scheduledPostJST':'2026-10-17T09:50:00'}
            (day/'PaceRecordingSchedule.json').write_text(json.dumps({'raceDate':'2026-10-17','races':[{},good,bad]}))
            data=self.board(root,pd.DataFrame(columns=['レースID','日付','馬番','障害']))
            self.assertEqual(data['counts']['totalRaces'],1)
            self.assertEqual(data['races'][0]['recordingState'],'waiting')
            self.assertTrue(data['warnings']);self.assertTrue(data['races'][0]['needsAttention'])

    def test_unreadable_schedule_is_not_successful_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);day=root/'reports/20261017';day.mkdir(parents=True)
            (day/'PaceRecordingSchedule.json').write_text('{bad')
            data=self.board(root,pd.DataFrame(columns=['レースID','日付','馬番','障害']))
            self.assertEqual(data['status'],'recording_attention_required')
            self.assertIn('読み取れない',data['emptyMessage'])

    def test_bad_models_and_laps_do_not_stop_healthy_match(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,peer=self.saved_pair(root)
            (root/'models/broken.json').write_text('{"broken":true}')
            lapdir=root/'laps/20261017';lapdir.mkdir(parents=True)
            (lapdir/'RaceLapData.json').write_text(json.dumps({'races':[{},actual]}))
            data=self.board(root,h)
            self.assertEqual(data['counts']['saved'],2);self.assertEqual(data['counts']['matched'],1)
            self.assertTrue(data['warnings'])

    def test_invalid_probabilities_rejected_before_result(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            pred['probabilities']['middle']=5
            (folder/'PacePrediction.json').write_text(json.dumps(pred))
            self.assertEqual(self.board(root,h)['counts']['invalid'],1)

    def test_failed_later_attempt_preserves_saved_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,o=self.setup_case(root)
            path=folder/'PacePrediction.json';path.write_text(json.dumps(pred));before=path.read_bytes()
            events=folder/'events';events.mkdir()
            (events/'failure.json').write_text(json.dumps({'raceID':o['raceID'],'raceDate':o['raceDate'],'atJST':NOW.isoformat(),'stage':'prediction_failed','detail':'network'}))
            row=self.board(root,h)['races'][0]
            self.assertEqual(row['recordingState'],'saved');self.assertTrue(row['needsAttention'])
            self.assertEqual(path.read_bytes(),before)

    def test_bad_result_keeps_prediction_and_continues_evaluation(self):
        from evaluate_prospective_pace import run
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,peer=self.saved_pair(root)
            broken=dict(actual);broken.pop('distanceM')
            valid=dict(actual,raceID=peer)
            lapdir=root/'laps/20261017';lapdir.mkdir(parents=True)
            (lapdir/'RaceLapData.json').write_text(json.dumps({'races':[broken,valid]}))
            rows={r['raceID']:r for r in self.board(root,h)['races']}
            self.assertEqual(rows[pred['raceID']]['recordingState'],'saved')
            self.assertEqual(rows[pred['raceID']]['resultState'],'invalid')
            self.assertEqual(rows[peer]['resultState'],'matched')
            report=run(root/'reports',root/'laps',h,root/'models')
            self.assertEqual(report['counts']['evaluatedRaces'],1)
            self.assertEqual(report['counts']['invalidRecords'],1)

    def test_evaluation_isolates_corrupt_orphan_and_checks_pending_records(self):
        from evaluate_prospective_pace import run
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);folder,pred,actual,h,peer=self.saved_pair(root)
            (folder/'PacePrediction.json').write_text('[1]');(folder/'index.json').unlink()
            report=run(root/'reports',root/'laps',h,root/'models')
            self.assertEqual(report['counts']['invalidRecords'],1)
            self.assertEqual(report['counts']['awaitingCompletedResults'],1)

    def test_report_replace_failure_preserves_previous_bytes(self):
        from unittest.mock import patch
        from pace_recording_io import atomic_text
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'report.json';path.write_text('{"previous":true}\n');before=path.read_bytes()
            with patch('pace_recording_io.os.replace',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):atomic_text(path,'{"new":true}\n')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(Path(d).glob('*.tmp')),[])
            atomic_text(path,'{"new":true}\n');self.assertEqual(json.loads(path.read_text()),{'new':True})

    def test_abrupt_process_exit_never_publishes_partial_json(self):
        import subprocess,sys,textwrap
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'PacePrediction.json';path.write_text('{"first":"unchanged"}')
            before=path.read_bytes()
            code=textwrap.dedent('''
                import sys,os,json
                sys.path[:]=json.loads(sys.argv[1])
                from pathlib import Path
                import prerace_snapshot_runtime as module
                def interrupted_dump(payload, stream, **kwargs):
                    stream.write('{"partial":');stream.flush();os._exit(75)
                module.json.dump=interrupted_dump
                module.write_once_json(Path(sys.argv[2]), {'new':True})
            ''')
            for target in [path,root/'new.json']:
                result=subprocess.run([sys.executable,'-c',code,json.dumps(sys.path),str(target)],capture_output=True)
                self.assertEqual(result.returncode,75,result.stderr.decode())
            self.assertEqual(path.read_bytes(),before);self.assertFalse((root/'new.json').exists())
            self.assertTrue(list(root.glob('*.tmp')))

    def test_schedule_retry_and_diagnostic_failure_are_best_effort(self):
        from datetime import datetime
        from unittest.mock import patch
        import pace_recording_events as module
        with tempfile.TemporaryDirectory() as d, patch.object(module,'ROOT',Path(d)), patch.object(module,'datetime',wraps=datetime) as clock:
            clock.now.return_value=NOW
            race={'race_id':'202605040601','venue':'東京','race_no':1,'start_time':'09:50'}
            module.save_schedule('20261017',[race]);path=Path(d)/'reports/20261017/PaceRecordingSchedule.json';before=path.read_bytes()
            module.save_schedule('20261017',[]);self.assertEqual(path.read_bytes(),before)
            with patch.object(module,'write_once_json',side_effect=OSError('disk unavailable')):
                module.record_event(race['race_id'],'20261017','capture_failed','network error')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(path.parent.glob('prerace/*/events/*.json')),[])

if __name__=='__main__':unittest.main()
