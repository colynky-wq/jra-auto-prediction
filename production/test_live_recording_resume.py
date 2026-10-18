import copy
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from live_recording_resume import published_race_ids
import build_signal_data as signals
import wide_odds_runtime as wide


class ResumeChecks(unittest.TestCase):
    def race(self):
        return {'raceID':'202608040710','raceDate':'2026-10-18','startTime':'14:55',
                'horses':[{'number':n,'name':str(n),**{k:20. for k in
                    ('aiProbability','marketProbability','aiTop2Probability','marketTop2Probability',
                     'aiTop3Probability','marketTop3Probability')}} for n in (1,2)]}

    def test_only_same_day_complete_published_forecasts_resume(self):
        with tempfile.TemporaryDirectory() as t:
            r=self.race();schedule=[{'race_id':r['raceID'],'start_time':'14:55'}]
            p=Path(t)/'RaceData.json'
            self.assertEqual(published_race_ids(t,'20261018',schedule),set())
            p.write_text(json.dumps([r]));before=p.read_bytes()
            self.assertEqual(published_race_ids(t,'20261018',schedule),{r['raceID']})
            self.assertEqual(p.read_bytes(),before)
            self.assertEqual(published_race_ids(t,'20261019',schedule),set())
            for damaged in ({**r,'startTime':'14:56'}, {**r,'horses':[]},
                            {**r,'horses':[r['horses'][0]]*2}):
                p.write_text(json.dumps([damaged]))
                with self.assertRaises(ValueError):published_race_ids(t,'20261018',schedule)
            p.write_text(json.dumps([r,r]))
            with self.assertRaises(ValueError):published_race_ids(t,'20261018',schedule)

    def test_actual_monitor_does_not_predict_published_race_in_overlap_window(self):
        import live_monitor_dry_run as monitor
        import pandas as pd
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as t, ExitStack() as stack:
            (Path(t)/'RaceData.json').write_text(json.dumps([self.race()]))
            schedule=[{'race_id':self.race()['raceID'],'start_time':'14:55','surface':'ダ'}]
            for name,value in {'HERE':Path(t)/'production','TARGET_DATE8':'20261018','MODE':'live',
                               'TARGET_RACE_ID':'','fetch_schedule':lambda _:schedule,'print_schedule':lambda _:None,
                               'now_jst':lambda:datetime.fromisoformat('2026-10-18T14:46:00+09:00'),
                               'load_history':lambda _:pd.DataFrame({'日付':pd.to_datetime(['2026-10-17'])})}.items():
                stack.enter_context(patch.object(monitor,name,value))
            predict=stack.enter_context(patch.object(monitor,'predict_live_race'))
            stack.enter_context(patch.dict('os.environ',{'KEIBAAI_PUBLISH_RACEDATA':'true','KEIBAAI_CAPTURE_PRERACE':'false'}))
            with contextlib.redirect_stdout(io.StringIO()):monitor.main()
            predict.assert_not_called()

    def test_concurrent_display_writes_use_distinct_temporary_files(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'SignalData.json';barrier=Barrier(2);dump=json.dump
            def together(value,stream,**kwargs):
                dump(value,stream,**kwargs);stream.flush();barrier.wait(timeout=5)
            with patch.object(signals.json,'dump',together),ThreadPoolExecutor(2) as pool:
                futures=[pool.submit(signals.write_json,p,{'value':i}) for i in (1,2)]
                for f in futures:f.result()
            self.assertIn(json.loads(p.read_text())['value'],(1,2))
            self.assertFalse(list(Path(t).glob('*.tmp')))

    def test_archive_survives_rotating_cache_loss_without_new_fetch(self):
        with tempfile.TemporaryDirectory() as t,patch.object(wide,'REPORTS_DIR',Path(t)/'reports'):
            r=self.race();s={**{k:r[k] for k in ('raceID','raceDate','startTime')},
                'capturedAtJST':'2026-10-18 14:43:35','pairs':[{'number1':1,'number2':2,'oddsLow':2,'oddsHigh':3}]}
            archived=wide._archive_snapshot(s);before=archived.read_bytes()
            with patch.object(wide,'fetch_wide_snapshot',side_effect=AssertionError('new odds forbidden')):
                got=wide.record_first_wide_snapshot(race_id=r['raceID'],race_date=r['raceDate'],
                        start_time=r['startTime'],path=Path(t)/'WideOddsData.json')
            self.assertEqual(got['capturedAtJST'],s['capturedAtJST'])
            self.assertEqual(archived.read_bytes(),before)

    def test_first_archive_wins_simultaneous_fetch_and_cache_writers(self):
        with tempfile.TemporaryDirectory() as t,patch.object(wide,'REPORTS_DIR',Path(t)/'reports'):
            r=self.race();barrier=Barrier(2)
            def fetch(rid,**kwargs):
                barrier.wait(timeout=5)
                return {'raceID':rid,'raceDate':r['raceDate'],'startTime':r['startTime'],
                        'capturedAtJST':'2026-10-18 14:43:35','pairs':[{'oddsLow':len(rid)}]}
            def capture(rid):
                return wide.record_first_wide_snapshot(race_id=rid,race_date=r['raceDate'],
                        start_time=r['startTime'],path=Path(t)/'WideOddsData.json')
            with patch.object(wide,'fetch_wide_snapshot',side_effect=fetch),ThreadPoolExecutor(2) as pool:
                results=list(pool.map(capture,['202608040710','202608040711']))
            self.assertEqual(len(json.loads((Path(t)/'WideOddsData.json').read_text())['snapshots']),2)
            self.assertEqual(len(results),2)

    def test_wide_research_survives_display_write_failure(self):
        import live_monitor_topk_publish as publisher
        import build_signal_data_v2 as v2
        import wide_axis_rules as axis
        from test_wide_axis_rules import fixture
        race,signal=fixture()
        class Clock:
            @staticmethod
            def now(_):return datetime.fromisoformat('2026-10-17T09:51:00+09:00')
        with tempfile.TemporaryDirectory() as t,patch.object(publisher,'RACEDATA_PATH',Path(t)/'RaceData.json'),\
                patch.object(publisher,'CAPTURE_WIDE_ODDS',False),patch.object(publisher,'REFRESH_SIGNALS',True),\
                patch.object(v2,'build_live_race_signal',return_value=signal),\
                patch.object(v2,'main',side_effect=FileNotFoundError('simulated display collision')),\
                patch('datetime.datetime',Clock),contextlib.redirect_stdout(io.StringIO()):
            publisher._refresh_prediction_bundle(race)
            p=Path(t)/'reports/20261017/wide_axis_v1'/f"{race['raceID']}.json"
            self.assertTrue(p.exists())
            record=json.loads(p.read_text());digest=record.pop('recordFingerprint')
            self.assertEqual(digest,axis.fingerprint(record))


if __name__=='__main__':unittest.main()
