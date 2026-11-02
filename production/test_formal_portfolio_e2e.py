"""Full publisher -> immutable outbox -> resume -> result acceptance rehearsal.

All races/odds are synthetic, all repositories are temporary local directories,
network clients are denied, and no live collection or historical replay occurs.
This verifies software integration, not a real racing-session acceptance event.
"""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from datetime import datetime,timedelta
from itertools import combinations
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import all_bet_forward as added
import all_bet_runtime as added_runtime
import triple_forward as triple
import triple_research_runtime as triple_runtime
import formal_wide_odds_runtime as formal_wide
import wide_odds_runtime as wide
import build_signal_data_v2 as signals
import wide_axis_rules as axis
import ticket_shadow_forward as ticket
import formal_portfolio_capture as capture
import live_monitor_topk_publish as publisher
import aligned_portfolio_review as review
from live_bundle_publisher import Outbox
from live_recording_resume import published_race_ids
from test_all_bet_research import entry,payload
from test_aligned_portfolio_review import settle
from test_live_bundle_publisher import ControlledGit,command

NOW=datetime.fromisoformat('2026-11-07T10:09:00+09:00')


class Clock(datetime):
    current=NOW
    @classmethod
    def now(cls,tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def outputs():
    rows=[]
    for n,ap,top2,top3 in zip(range(1,9),[30,20,15,10,8,7,6,4],
                             [55,42,30,22,17,14,12,8],[75,60,45,35,30,25,20,10]):
        rows.append({'馬番':n,'馬名':f'horse{n}','年齢':3,'馬キー':f'horse{n}_2023',
                     '単勝オッズ':10.,'市場勝率(%)':12.5,'AI勝率(%)':ap,
                     '市場2着内率(%)':25.,'AI2着内率(%)':top2,
                     '市場3着内率(%)':37.5,'AI3着内率(%)':top3,
                     'AI÷市場':ap/12.5,'期待払戻指数':ap/10})
    return pd.DataFrame(rows)


class PortfolioE2E(unittest.TestCase):
    def test_actual_publisher_publication_retry_restart_and_independent_settlement(self):
        with tempfile.TemporaryDirectory() as temp,ExitStack() as stack,redirect_stdout(io.StringIO()) as log:
            Clock.current=NOW
            root=Path(temp)/'monitor';root.mkdir();remote=Path(temp)/'remote.git'
            command(root,'init','-b','main');command(root,'config','user.name','test')
            command(root,'config','user.email','test@example.invalid')
            (root/'README.test').write_text('Synthetic test repository only.\n')
            command(root,'add','.');command(root,'commit','-m','initial')
            command(root,'clone','--bare',str(root),str(remote));command(root,'remote','add','origin',str(remote))
            journal=Path(temp)/'outbox'
            race={'race_id':'202605040502','venue':'東京','race_no':2,'field_size':8,
                  'start_time':'10:20','surface':'芝','distance':1600,'race_name':'synthetic',
                  '_prediction_timing':'live_10min','_trigger_minutes_to_post':11.0}
            frame=outputs()
            # Construct the official-entry fixture from exactly the same table.
            entry_race={'raceDate':'2026-11-07','raceID':race['race_id'],'startTime':race['start_time'],
                        'horses':[{'number':int(r['馬番']),'name':r['馬名']} for r in frame.to_dict('records')]}
            e=entry(entry_race);e['capturedAtJST']=(NOW-timedelta(seconds=10)).isoformat()
            frame.attrs['allBetEntryEvidence']=e
            calls={'added':0,'triple':0,'wide':0}
            real_added,real_triple=added.record_first,triple.record_first
            real_wide=formal_wide.record_first_wide_snapshot
            real_portfolio=capture.record_first
            real_axis=axis.record_first

            def fetch_added(rid,kind):
                calls['added']+=1
                p=payload(entry_race,kind);p['data']['official_datetime']=NOW.isoformat()
                p['data']['odds'][str(added.model.TYPES[kind])]={k:['10.0','10.0'] for k in p['data']['odds'][str(added.model.TYPES[kind])]}
                return p

            def fetch_triple(rid,kind):
                calls['triple']+=1
                return {'status':'middle','data':{'official_datetime':NOW.isoformat(),
                    'odds':{str(triple.TYPES[kind]):{k:['20.0'] for k in triple.expected_keys(kind,range(1,9))}}}}

            def fetch_wide(rid,**kwargs):
                calls['wide']+=1
                return {'raceID':rid,'raceDate':kwargs['race_date'],'venue':kwargs['venue'],
                        'raceNumber':kwargs['race_number'],'startTime':kwargs['start_time'],
                        'capturedAtJST':NOW.strftime('%Y-%m-%d %H:%M:%S'),'minutesToPost':11,
                        'officialDatetime':NOW.strftime('%Y-%m-%d %H:%M:%S'),
                        'pairs':[{'number1':a,'number2':b,'oddsLow':10.,'oddsHigh':11.}
                                 for a,b in combinations(range(1,9),2)]}

            stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('real HTTP forbidden')))
            stack.enter_context(patch('requests.get',side_effect=AssertionError('real HTTP forbidden')))
            stack.enter_context(patch.object(triple,'urlopen',side_effect=AssertionError('real HTTP forbidden')))
            stack.enter_context(patch('urllib.request.urlopen',side_effect=AssertionError('real HTTP forbidden')))
            for obj,name,value in [(publisher,'RACEDATA_PATH',root/'RaceData.json'),
                    (publisher,'REPORTS_PATH',root/'reports'),(publisher,'CAPTURE_WIDE_ODDS',True),
                    (publisher,'REFRESH_SIGNALS',True),(publisher.base,'TARGET_DATE8','20261107'),
                    (publisher.base,'now_jst',lambda:Clock.now(capture.JST)),
                    (signals,'RACEDATA_PATH',root/'RaceData.json'),(signals,'OUT_PATH',root/'SignalData.json'),
                    (signals,'REPORTS_DIR',root/'reports'),(signals,'WIDE_ODDS_PATH',root/'WideOddsData.json'),
                    (wide,'REPORTS_DIR',root/'reports'),(axis,'datetime',Clock),(ticket,'datetime',Clock)]:
                stack.enter_context(patch.object(obj,name,value))
            stack.enter_context(patch.dict(added_runtime._futures,{},clear=True))
            stack.enter_context(patch.dict(triple_runtime._futures,{},clear=True))
            stack.enter_context(patch.object(added_runtime,'record_first',side_effect=lambda rt,r,k,pred,ev:
                    real_added(rt,r,k,pred,ev,fetch=fetch_added,clock=lambda:Clock.current)))
            stack.enter_context(patch.object(triple_runtime,'record_first',side_effect=lambda rt,r,k,pred:
                    real_triple(rt,r,k,pred,fetch=fetch_triple,clock=lambda:Clock.current)))
            stack.enter_context(patch.object(wide,'fetch_wide_snapshot',side_effect=fetch_wide))
            stack.enter_context(patch.object(formal_wide,'record_first_wide_snapshot',side_effect=lambda **kwargs:
                    real_wide(**kwargs,path=root/'WideOddsData.json')))
            stack.enter_context(patch.object(capture,'record_first',side_effect=lambda rt,r:
                    real_portfolio(rt,r,clock=lambda:Clock.current)))
            stack.enter_context(patch.object(axis,'record_first',side_effect=lambda rt,r,s,now:
                    real_axis(rt,r,s,Clock.current)))

            # Exercise the actual production publisher and both background workers.
            try:
                publisher.publish_race_to_racedata(race,frame)
            except Exception as exc:
                self.fail(str(exc)+'\n'+log.getvalue())
            published=json.loads((root/'RaceData.json').read_text())[0]
            rid=published['raceID'];bundle_path=capture.path_for(root,published['raceDate'],rid)
            bundle=capture.load(root,published['raceDate'],rid)
            self.assertEqual(calls,{'added':5,'triple':2,'wide':1})
            self.assertTrue(bundle['plan']['legs'])
            self.assertTrue(review.build(root,NOW)['acceptance']['firstCaptureVerified'])
            immutable={str(p.relative_to(root)):p.read_bytes() for p in (root/'reports').rglob('*.json')
                       if any(part in p.parts for part in ['all_bet_v1','triple_v1','portfolio_live10','wide_odds_live10'])}

            # A failed push leaves a durable outbox. A new instance publishes it.
            box=Outbox(root,journal,'20261107',git=ControlledGit(root,failures={'push':1}),today=lambda:'2026-11-07')
            box.capture();self.assertFalse(box.publish());self.assertTrue(box.state['pending'])
            box=Outbox(root,journal,'20261107',today=lambda:'2026-11-07')
            self.assertTrue(box.publish());self.assertFalse(box.state['pending'])
            remote_bundle=command(remote,'show','main:'+str(bundle_path.relative_to(root)))
            self.assertEqual(json.loads(remote_bundle)['fingerprint'],bundle['fingerprint'])

            # Simulate a new process after post. Resume requires the complete bundle.
            Clock.current=NOW+timedelta(hours=1)
            schedule=[{'race_id':rid,'start_time':published['startTime']}]
            self.assertEqual(published_race_ids(root,'20261107',schedule,require_research=True),{rid})
            added_runtime._futures.clear();triple_runtime._futures.clear()
            changed=deepcopy(frame);changed.loc[0,'AI勝率(%)']=1.
            publisher.publish_race_to_racedata(race,changed)
            self.assertEqual(calls,{'added':5,'triple':2,'wide':1},'restart fetched new odds')
            for name,data in immutable.items():self.assertEqual((root/name).read_bytes(),data)
            self.assertEqual(capture.load(root,published['raceDate'],rid)['fingerprint'],bundle['fingerprint'])

            # Independent result/payout fixtures are then consumed from published files.
            settle(root,published)
            box.capture();self.assertTrue(box.publish())
            consumer=Path(temp)/'consumer';command(root,'clone',str(remote),str(consumer))
            _,accepted=review.refresh(consumer,Clock.current)
            self.assertEqual(accepted['aligned']['racesObserved'],1)
            self.assertEqual(accepted['aligned']['overall']['settledSelectedRaces'],1)
            self.assertEqual(accepted['quality']['issues'],[])
            self.assertEqual(accepted['acceptance']['status'],'complete')
            self.assertFalse(accepted['acceptance']['restartAndPublicationLiveVerified'],
                             'synthetic rehearsal must not claim a real session was observed')
            self.assertFalse(accepted['betaDecision']['eligibleForResearchBeta'])
            selected=accepted['races'][0]['legs']
            self.assertEqual([l['candidateID'] for l in selected],[l['candidateID'] for l in bundle['plan']['legs']])
            self.assertEqual(accepted['aligned']['overall']['returnYen'],sum(l['payoutYen'] for l in selected))
            self.assertEqual(accepted['aligned']['first100']['diagnostics']['trioIncrement']['returnYen'],900)
            for name,data in immutable.items():self.assertEqual((consumer/name).read_bytes(),data)
            before=(consumer/review.JSON_NAME).read_bytes()
            changed,_=review.refresh(consumer,Clock.current+timedelta(seconds=1))
            self.assertFalse(changed);self.assertEqual((consumer/review.JSON_NAME).read_bytes(),before)


if __name__=='__main__':unittest.main()
