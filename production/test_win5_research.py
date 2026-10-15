from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import win5_research as w
import all_bet_baseline as b
from test_all_bet_research import forecast,entry
from test_triple_baseline import result

NOW=datetime.fromisoformat('2026-10-17T12:00:00+09:00')
DAY='2026-10-17'


def official():
    return ('<h2>2026年11月30日（月曜）までのWIN5対象レース</h2><table>'
            '<tr><td>10月17日（土曜）</td><td>14時45分</td><td>京都10R</td><td>東京10R</td>'
            '<td>新潟11R</td><td>京都11R</td><td>東京11R</td></tr>'
            '<tr><td>10月18日（日曜）</td><td>14時40分</td><td>東京10R</td><td>京都10R</td>'
            '<td>新潟11R</td><td>東京11R</td><td>京都11R</td></tr></table>').encode()


def schedule():
    return [{'race_id':rid,'venue':venue,'race_no':no,'start_time':time,'surface':'芝','distance':1600}
            for rid,venue,no,time in [('202608040610','京都',10,'14:50'),('202605040610','東京',10,'15:00'),
            ('202604040111','新潟',11,'15:20'),('202608040611','京都',11,'15:30'),('202605040611','東京',11,'15:45')]]


def plan():return w.resolve_plan(w.official_targets(official(),DAY),schedule())


def leg(race):
    r=forecast();r.update(raceID=race['race_id'],startTime=race['start_time'],venue=race['venue'],raceNumber=race['race_no'])
    e=entry(r);e['capturedAtJST']=NOW.isoformat()
    payload={'status':'middle','data':{'official_datetime':NOW.isoformat(),
             'odds':{'1':{f'{i:02}':['5.0','0.0','1'] for i in range(1,9)}}}}
    return {'prediction':r,'entryEvidence':e,'predictionAvailableAtJST':NOW.isoformat(),
            'winOddsSource':{'payload':payload,'requestStartedAtJST':NOW.isoformat(),'receivedAtJST':NOW.isoformat()}}


def hashes():
    out={k:'a'*64 for k in ('lightgbm_fixed_2025.txt','lightgbm_top2_fixed_2025.txt','lightgbm_top3_fixed_2025.txt',
                           'production_history.parquet','feature_engine.py','live_monitor_topk.py')}
    for p in (Path(w.__file__),Path(b.__file__)):out[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class WIN5Tests(unittest.TestCase):
    def test_official_five_leg_date_order_and_published_deadline(self):
        p=plan();self.assertEqual(p['schedule'][0]['race_id'],'202608040610')
        self.assertEqual(p['deadlineJST'],'2026-10-17T14:45:00+09:00')
        self.assertEqual(w.official_targets(official(),'2026-10-18')['legs'][0]['venue'],'東京')
        with self.assertRaises(ValueError):w.official_targets(official(),'2027-10-17')
        with self.assertRaises(ValueError):w.official_targets(official(),'2026-12-01')
        with self.assertRaises(ValueError):w.official_targets(official().replace(b'14',b'--'),DAY)
        bad=schedule();bad[0]['start_time']='14:49'
        with self.assertRaises(ValueError):w.resolve_plan(w.official_targets(official(),DAY),bad)
        with self.assertRaises(ValueError):w.resolve_plan(w.official_targets(official(),DAY),schedule()[1:])

    def save(self,root,fn=leg,clock=lambda:NOW):
        return w.capture(root,plan(),fn,clock=clock,hashes=hashes())

    def test_capture_first_all_five_and_factorized_joint_without_enumerating(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.save(tmp);before=p.read_bytes();record=w.validate(json.loads(before))
            self.assertEqual(record['status'],'ready');self.assertEqual(len(record['predictions']),5)
            self.save(tmp,fn=lambda _:self.fail('first record refetched'))
            self.assertEqual(p.read_bytes(),before)
            actual={}
            for row in record['predictions']:
                r=result(row['prediction']);r['resultUpdatedAtJST']='2026-10-17 16:00:00'
                actual[(DAY,r['raceID'])]=r
            score=w.probability_score(record,actual)
            self.assertEqual(score['status'],'probability_matched')
            self.assertEqual(score['combinationCount'],8**5)
            self.assertAlmostEqual(score['aiActualOutcomeProbability'],1/8**5)
            self.assertTrue(score['aiTopOneAllFiveHit'])

    def test_any_input_after_first_common_deadline_is_never_ready(self):
        for mode in ('late_prediction','final_odds','old_entry','wrong_date','bad_market','missing_leg','missing_hash'):
            def predict(race):
                v=leg(race)
                if mode=='late_prediction':v['predictionAvailableAtJST']='2026-10-17T15:10:00+09:00'
                if mode=='final_odds':v['winOddsSource']['payload']['status']='result'
                if mode=='old_entry':v['entryEvidence']['capturedAtJST']='2026-10-17T11:00:00+09:00'
                if mode=='wrong_date':v['prediction']['raceDate']='2026-10-18'
                if mode=='bad_market':v['winOddsSource']['payload']['data']['odds']['1']['01'][0]='1.1'
                if mode=='missing_leg' and race['race_no']==11:raise OSError('no input')
                return v
            with tempfile.TemporaryDirectory() as tmp:
                h=hashes();h.pop('feature_engine.py') if mode=='missing_hash' else None
                p=w.capture(tmp,plan(),predict,clock=lambda:NOW,hashes=h)
                self.assertEqual(json.loads(p.read_text())['status'],'held')

    def test_day_deadline_and_fsync_boundary_never_backfill(self):
        for now in (NOW-timedelta(days=1),NOW+timedelta(days=1),NOW.replace(hour=14,minute=45)):
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(self.save(tmp,fn=lambda _:self.fail('late forecast'),clock=lambda:now))
        with tempfile.TemporaryDirectory() as tmp:
            times=iter([NOW]*7+[NOW.replace(hour=14,minute=45)])
            self.assertIsNone(self.save(tmp,clock=lambda:next(times)))
            self.assertEqual(list(Path(tmp).rglob('*.json')),[])

    def test_result_identity_changes_ties_and_unfinished_separate_from_losses(self):
        with tempfile.TemporaryDirectory() as tmp:
            record=json.loads(self.save(tmp).read_text())
            actual={}
            self.assertEqual(w.probability_score(record,actual)['status'],'awaiting_results')
            for row in record['predictions']:
                r=result(row['prediction']);r['resultUpdatedAtJST']='2026-10-17 16:00:00';actual[(DAY,r['raceID'])]=r
            first=next(iter(actual.values()));first['horses'][0]['name']='changed'
            self.assertEqual(w.probability_score(record,actual)['status'],'entrant_changed')
            first['horses'][0]['name']='horse1';first['horses'][-1]['finishPosition']=3
            self.assertEqual(w.probability_score(record,actual)['status'],'dead_heat_excluded')

    def test_validation_missing_sessions_future_and_no_payout_roi_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.save(root)
            out=w.build_validation(root,clock=lambda:NOW)
            self.assertEqual(out['counts']['awaiting_results'],1)
            self.assertEqual(out['payoutResearchStatus'],'dated_source_connected');self.assertIsNone(out['returnIndex'])
            self.assertEqual(w.build_validation(root,clock=lambda:NOW-timedelta(days=1))['counts']['invalid'],1)
            p.unlink();journal={'hasTarget':True,'raceDate':DAY,'status':'failed'}
            (p.parent.parent/'win5_status.json').write_text(json.dumps(journal))
            out=w.build_validation(root,clock=lambda:NOW)
            self.assertEqual(out['counts']['missingRecords'],1);self.assertEqual(out['status'],'attention_required')

    def test_failed_official_check_with_zero_races_is_attention_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reports/20261017/win5_status.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'raceDate':DAY,'hasTarget':None,'status':'failed'}))
            out=w.build_validation(Path(tmp),clock=lambda:NOW)
            self.assertEqual(out['counts']['records'],0)
            self.assertEqual(out['counts']['failedSessions'],1)
            self.assertEqual(out['status'],'attention_required');self.assertTrue(out['warnings'])


if __name__=='__main__':unittest.main()
