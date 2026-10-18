"""Coverage includes failed forecasts and midnight, using temporary files only."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from all_bet_recording_coverage import expected_races
import build_bet_research_data as ui
from evaluate_all_bet_forward import build as added
from evaluate_triple_forward import build as triple
from test_all_bet_research import NOW, forecast
from test_bet_research_data import snapshot
import wide_axis_shadow as wide


def schedule(root, race=None):
    race = race or forecast()
    row = {k:race[k] for k in ('raceDate','raceID','startTime')}
    row.update(venue='東京',raceNumber=int(race['raceID'][-2:]))
    row['scheduledPostJST'] = race['raceDate']+'T'+race['startTime']+':00+09:00'
    data = {'schemaVersion':1,'raceDate':race['raceDate'],
            'observedAtJST':NOW.replace(hour=9).isoformat(),'races':[row]}
    path = root/'reports'/race['raceDate'].replace('-','')/'PaceRecordingSchedule.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data))
    return path, data


class CoverageTests(unittest.TestCase):
    def test_failed_forecast_counts_in_every_pool_and_app_without_creating_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);r=forecast();schedule(root,r)
            folder=root/'reports/20261017/prerace'/r['raceID']/'events';folder.mkdir(parents=True)
            event={'raceDate':r['raceDate'],'raceID':r['raceID'],'stage':'monitor_failed',
                   'atJST':NOW.isoformat(),'detail':'entry/odds count mismatch: 8 / 9'}
            (folder/'first.json').write_text(json.dumps(event))
            before=snapshot(root);now=NOW.replace(hour=18)
            a=added(root,fetch=lambda _:self.fail('network'),clock=lambda:now)
            t=triple(root,fetch=lambda _:self.fail('network'),clock=lambda:now)
            w=wide.build(root,clock=lambda:now);u=ui.build(root,clock=lambda:now)
            self.assertEqual(snapshot(root),before)
            for out in (a,t):
                self.assertEqual(out['status'],'attention_required')
                self.assertTrue(all(c['records']==0 and c['missingRecords']==1 for c in out['counts'].values()))
                self.assertTrue(all(x['status']=='missing_record' for x in out['races']))
            self.assertEqual(w['counts']['missingRecords'],1)
            self.assertEqual(len(u['races']),9)
            self.assertTrue(all(x['status']=='missing_record' and not x['hasRecord'] and not x['candidates'] for x in u['races']))
            self.assertTrue(all('8 / 9' in x['reason'] for x in u['races']))
            self.assertTrue(all(c['cumulative']['missingRecords']==1 for c in u['catalog'] if c['id']!='win5'))

    def test_future_races_wait_and_elapsed_races_remain_on_original_day(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);schedule(root)
            self.assertEqual(expected_races(root,ui.added.START,NOW)[0],[])
            value=ui.build(root,clock=lambda:NOW)
            self.assertEqual(value['races'],[])
            for now in (NOW.replace(minute=20),NOW+timedelta(days=1)):
                races,warnings=expected_races(root,ui.added.START,now)
                self.assertEqual(len(races),1);self.assertFalse(warnings)
                self.assertEqual(races[0]['raceDate'],'2026-10-17')

    def test_saved_forecast_and_schedule_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);r=forecast();schedule(root,r)
            (root/'RaceData.json').write_text(json.dumps([r]))
            races,warnings=expected_races(root,ui.added.START,NOW.replace(hour=18))
            self.assertEqual(len(races),1);self.assertFalse(warnings)
            self.assertEqual(len(ui.build(root,clock=lambda:NOW.replace(hour=18))['races']),9)

    def test_corrupt_wrong_day_duplicate_or_late_schedule_is_attention_not_no_races(self):
        for mode in ('corrupt','wrong_day','duplicate','late','future_observation'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);path,data=schedule(root)
                if mode=='wrong_day':data['raceDate']='2026-10-18'
                if mode=='duplicate':data['races']*=2
                if mode=='late':data['observedAtJST']=NOW.replace(hour=12).isoformat()
                if mode=='future_observation':data['observedAtJST']=(NOW+timedelta(days=1)).isoformat()
                path.write_text('{broken' if mode=='corrupt' else json.dumps(data))
                races,warnings=expected_races(root,ui.added.START,NOW.replace(hour=18))
                self.assertFalse(races);self.assertTrue(warnings)
                value=ui.build(root,clock=lambda:NOW.replace(hour=18))
                self.assertEqual(value['collectionStatus'],'attention_required')
                self.assertTrue(value['warnings'])

    def test_wrong_identity_or_future_failure_event_is_not_attributed_to_race(self):
        for mode in ('wrong_race','future'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);r=forecast();schedule(root,r)
                folder=root/'reports/20261017/prerace'/r['raceID']/'events';folder.mkdir(parents=True)
                e={'raceDate':r['raceDate'],'raceID':r['raceID'],'stage':'monitor_failed',
                   'atJST':NOW.isoformat(),'detail':'must not attach'}
                if mode=='wrong_race':e['raceID']='202605040503'
                else:e['atJST']=(NOW+timedelta(days=1)).isoformat()
                (folder/'first.json').write_text(json.dumps(e))
                value=ui.build(root,clock=lambda:NOW.replace(hour=18))
                self.assertTrue(all('must not attach' not in x['reason'] for x in value['races']))


if __name__=='__main__':unittest.main()
