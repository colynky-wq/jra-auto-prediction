"""Synthetic pre-start capture/settlement tests; never fetch real odds."""
from copy import deepcopy
from datetime import datetime,timedelta
from itertools import combinations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import formal_portfolio_capture as capture
import formal_10min_evidence as evidence
import all_bet_forward as added
import triple_forward as triple
import build_signal_data_v2 as signals
from test_all_bet_research import forecast,entry,payload

NOW=datetime.fromisoformat('2026-11-07T10:09:00+09:00')


def seed(root):
    r=forecast();r.update(raceDate='2026-11-07',predictionTiming='live_10min',
                         capturedAtJST=NOW.isoformat(),venue='東京',raceNumber=2,course='芝1600m')
    for h,ap,tp in zip(r['horses'],[30,20,15,10,8,7,6,4],[75,60,45,35,30,25,20,10]):
        h.update(aiProbability=ap,aiTop3Probability=tp,marketTop3Probability=37.5)
    e=entry(r);e['capturedAtJST']=(NOW-timedelta(seconds=10)).isoformat()
    def fetch(_,kind):
        p=payload(r,kind);p['data']['official_datetime']=NOW.isoformat()
        p['data']['odds'][str(added.model.TYPES[kind])] = {k:['10.0','10.0'] for k in p['data']['odds'][str(added.model.TYPES[kind])]}
        return p
    added.record_first(root,r,'win',NOW.isoformat(),e,fetch=fetch,clock=lambda:NOW)
    p={'status':'middle','data':{'official_datetime':NOW.isoformat(),'odds':{'7':{k:['20.0'] for k in triple.expected_keys('trio',range(1,9))}}}}
    triple.record_first(root,r,'trio',NOW.isoformat(),fetch=lambda *_:p,clock=lambda:NOW)
    o={k:r[k] for k in ['raceDate','raceID','startTime','venue','raceNumber']}
    o.update(capturedAtJST=NOW.strftime("%Y-%m-%d %H:%M:%S"),minutesToPost=11,frozenAtFirstSuccessfulCapture=True,
             pairs=[{'number1':a,'number2':b,'oddsLow':10.0,'oddsHigh':11.0} for a,b in combinations(range(1,9),2)])
    path=Path(root)/'reports/20261107/wide_odds_live10'/(r['raceID']+'.json');path.parent.mkdir(parents=True);path.write_text(json.dumps(o))
    return r,o,path


class CaptureTests(unittest.TestCase):
    def test_immutable_plan_exact_inputs_and_settlement_does_not_reselect(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r,o,p=seed(root)
            with patch('requests.get',side_effect=AssertionError('network not allowed')):
                dest=capture.record_first(root,r,clock=lambda:NOW+timedelta(seconds=1))
            before=dest.read_bytes();saved=capture.load(root,r['raceDate'],r['raceID'])
            self.assertTrue(saved['plan']['legs'])
            self.assertTrue(evidence.inspect_formal_10min_evidence(root,r)['portfolioInputsVerified'])
            # Restart after post reuses a frozen plan; it cannot select again.
            self.assertEqual(dest,capture.record_first(root,r,clock=lambda:NOW+timedelta(hours=1)))
            self.assertEqual(before,dest.read_bytes())
            payouts={}
            for l in saved['plan']['legs']:
                if l['betType']=='wide':
                    cid=l['candidateID'];payouts[(r['raceID'],int(cid[:2]),int(cid[2:]))]={'hit':True,'payoutPer100Yen':500}
            projected=capture.project(root,r['raceDate'],r['raceID'],{},payouts)
            self.assertEqual([x['candidateID'] for x in projected['legs']],[x['candidateID'] for x in saved['plan']['legs']])
            self.assertEqual(before,dest.read_bytes())
            changed=deepcopy(r);changed['horses'][0]['aiProbability']+=1
            with self.assertRaises(ValueError):capture.record_first(root,changed,clock=lambda:NOW)
            p.write_text(p.read_text()+' ')
            with self.assertRaises(ValueError):capture.load(root,r['raceDate'],r['raceID'])
            self.assertFalse(evidence.inspect_formal_10min_evidence(root,r)['eligible'])

    def test_no_late_first_write_and_no_missing_odds_fallback(self):
        for mode in ['late','missing','morning','different_race','different_prediction','incomplete']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as t:
                root=Path(t);r,o,p=seed(root)
                if mode=='missing':p.unlink()
                if mode=='morning':o['capturedAtJST']='2026-11-07T09:00:00+09:00';p.write_text(json.dumps(o))
                if mode=='different_race':o['raceID']='202605040503';p.write_text(json.dumps(o))
                if mode=='different_prediction':r['horses'][0]['aiProbability']+=1
                if mode=='incomplete':o['pairs'].pop();p.write_text(json.dumps(o))
                with self.assertRaises((ValueError,FileNotFoundError)):
                    capture.record_first(root,r,clock=lambda:NOW+timedelta(minutes=4) if mode=='late' else NOW)
                self.assertFalse(capture.path_for(root,r['raceDate'],r['raceID']).exists())

    def test_expensive_work_cannot_cross_window_before_atomic_install(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r,_,_=seed(root)
            ticks=iter([NOW,NOW+timedelta(minutes=4)])
            with self.assertRaises(ValueError):capture.record_first(root,r,clock=lambda:next(ticks))
            self.assertFalse(capture.path_for(root,r['raceDate'],r['raceID']).exists())

    def test_formal_display_cache_never_overrides_archive_or_falls_back(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r,o,p=seed(root)
            stale={**o,'capturedAtJST':'2026-11-07T08:00:00+09:00'}
            cache=root/'WideOddsData.json';cache.write_text(json.dumps({'snapshots':[stale]}))
            with patch.object(signals,'REPORTS_DIR',root/'reports'),patch.object(signals,'WIDE_ODDS_PATH',cache):
                self.assertEqual(signals._load_wide_by_race([r])[r['raceID']],o)
                p.unlink()
                self.assertNotIn(r['raceID'],signals._load_wide_by_race([r]))

    def test_projection_uses_archive_when_display_signal_is_missing_or_changed(self):
        import portfolio_shadow as portfolio
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r,_,_=seed(root)
            capture.record_first(root,r,clock=lambda:NOW)
            first=portfolio.build(root)
            self.assertEqual(len(first['races']),1)
            plan=first['races'][0]
            # Altering a later display cannot change the frozen selection or price.
            signal={**capture.load(root,r['raceDate'],r['raceID'])['signal'], 'battle':{'grade':'見送り'}}
            (root/'reports/20261107/SignalData.json').write_text(json.dumps({'races':[signal]}))
            again=portfolio.build(root)['races'][0]
            self.assertEqual(plan,again)

    def test_new_win_sidecar_alone_cannot_qualify_as_formal_portfolio(self):
        with tempfile.TemporaryDirectory() as t:
            r,_,_=seed(Path(t))
            out=evidence.inspect_formal_10min_evidence(Path(t),r)
            self.assertFalse(out['eligible']);self.assertEqual(out['reason'],'formal_portfolio_inputs_missing_or_invalid')


if __name__=='__main__':unittest.main()
