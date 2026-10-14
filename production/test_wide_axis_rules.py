from datetime import datetime
import copy
import json
from pathlib import Path
import tempfile
import unittest

import wide_axis_rules as axis
import wide_axis_shadow as shadow


def fixture():
    market = [70,60,50,35,30,25,20,10]
    ai = [60,55,45,35,30,40,25,10]
    horses = [{'number':i+1,'name':'馬'+str(i+1),'marketTop3Probability':m,'aiTop3Probability':a,
               'aiProbability':99 if i == 0 else .1} for i,(m,a) in enumerate(zip(market,ai))]
    pairs = []
    for a in range(1,9):
        for b in range(a+1,9):
            p,low = 1,2
            if (a,b) == (1,6): p,low=28,4.1
            if (a,b) == (2,6): p,low=25,5
            if (a,b) == (3,6): p,low=20,6
            if (a,b) == (1,7): p,low=2,100
            pairs.append({'number1':a,'number2':b,'name1':'馬'+str(a),'name2':'馬'+str(b),
                          'aiWideProbability':p,'wideOddsLow':low,'wideOddsHigh':low+1})
    race={'raceID':'202605040601','raceDate':'2026-10-17','startTime':'10:00','horses':horses}
    signal={**{k:race[k] for k in ['raceID','raceDate','startTime']},'eligible':True,
            'wideEvaluation':{'oddsConnected':True,'capturedAtJST':'2026-10-17 09:50:00','validationPairs':pairs}}
    return race,signal


def write_actual_result(day, race):
    ranks = {1:1,6:2,4:3,2:4,3:5,5:6,7:7,8:8}
    rows = [{**h, 'finishPosition':ranks[h['number']], 'resultStatus':'確定'} for h in race['horses']]
    result = {k:race[k] for k in ('raceID','raceDate','startTime')}
    result.update(fieldSize=len(rows), resultUpdatedAtJST='2026-10-17 10:01:00', horses=rows)
    (day/'ResultData.json').write_text(json.dumps([result]))
    return result


class AxisChecks(unittest.TestCase):
    def test_top3_axis_not_win_longshot(self):
        race,signal=fixture()
        e=axis.evaluate(race,signal)
        self.assertEqual(e['axis']['number'],6)
        self.assertEqual(e['axis']['aiTop3Pct'],40)
        self.assertEqual(len(e['pairs']),3)
        self.assertGreaterEqual(e['minimumSingleHitReturnYen'],e['totalYen'])
        race['horses'][0]['aiProbability']=0
        race['horses'][5]['aiProbability']=100
        self.assertEqual(axis.evaluate(race,signal),e)

    def test_joint_not_independent_product(self):
        race,signal=fixture()
        e=axis.evaluate(race,signal)
        self.assertEqual(e['pairs'][0]['probabilityPct'],28)
        self.assertNotEqual(28,40*60/100)

    def test_refund_constraint_reduces_points(self):
        race,s=fixture()
        for p in s['wideEvaluation']['validationPairs']:
            if p['number2']==6 and p['number1']<=3:
                p['aiWideProbability']=40
                p['wideOddsLow']=2.9
                p['wideOddsHigh']=3
        e=axis.evaluate(race,s)
        self.assertEqual(len(e['pairs']),2)
        self.assertGreaterEqual(e['minimumSingleHitReturnYen'],e['totalYen'])

    def test_pending_invalid_and_missing_marginals(self):
        race,s=fixture();s['wideEvaluation']['oddsConnected']=False
        self.assertEqual(axis.evaluate(race,s)['status'],'pending')
        race,s=fixture();del race['horses'][5]['aiTop3Probability']
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')
        race,s=fixture();s['wideEvaluation']['validationPairs'][0]['aiWideProbability']=float('nan')
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')
        race,s=fixture();s['wideEvaluation']['validationPairs']=s['wideEvaluation']['validationPairs'][:20]
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')
        race,s=fixture();race['horses'][1]['number']=1
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')

    def test_timestamp_and_entrant_changes(self):
        race,s=fixture();s['wideEvaluation']['capturedAtJST']='2026-10-17 10:00:00'
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')
        race,s=fixture();s['wideEvaluation']['validationPairs'][0]['name1']='別馬'
        self.assertEqual(axis.evaluate(race,s)['status'],'invalid')

    def test_ties_have_shared_market_rank(self):
        race,s=fixture()
        race['horses'][4]['marketTop3Probability']=27.5
        race['horses'][5]['marketTop3Probability']=27.5
        e=axis.evaluate(race,s)
        self.assertEqual(e['axis']['number'],7)

    def test_record_is_immutable_and_no_post_backfill(self):
        race,s=fixture()
        with tempfile.TemporaryDirectory() as t:
            post=datetime.fromisoformat('2026-10-17T10:00:00+09:00')
            self.assertIsNone(axis.record_first(t,race,s,post))
            now=datetime.fromisoformat('2026-10-17T09:51:00+09:00')
            p=axis.record_first(t,race,s,now)
            first=p.read_bytes()
            self.assertEqual(axis.display_evaluation(t,race,s)['status'],'ready')
            race['horses'][0]['aiProbability']=0.02
            self.assertEqual(axis.display_evaluation(t,race,s)['status'],'ready')
            s['wideEvaluation']['validationPairs'][0]['wideOddsLow']=99
            axis.record_first(t,race,s,now)
            self.assertEqual(p.read_bytes(),first)
            self.assertEqual(axis.display_evaluation(t,race,s)['status'],'invalid')
            self.assertFalse(list(p.parent.glob('*.tmp')))

    def test_old_history_excluded_new_result_join_and_damage(self):
        race,s=fixture()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            old=root/'reports/20261012';old.mkdir(parents=True)
            (old/'WideValidation.json').write_text('{"races":[]}')
            self.assertEqual(shadow.build(root)['counts']['recordedRaces'],0)
            p=axis.record_first(root,race,s,datetime.fromisoformat('2026-10-17T09:51:00+09:00'))
            self.assertEqual(shadow.build(root)['counts']['awaitingResults'],1)
            rows=[]
            for x in s['wideEvaluation']['validationPairs']:
                key=(x['number1'],x['number2'])
                payouts={(1,6):410,(1,4):100,(4,6):450}
                rows.append({**x,'oddsLow':x['wideOddsLow'],'oddsHigh':x['wideOddsHigh'],
                             'hit':key in payouts,'payoutPer100Yen':payouts.get(key,0)})
            result={'races':[{'raceID':race['raceID'],'validationScope':'all_pairs',
                'snapshotCapturedAtJST':'2026-10-17 09:50:00','pairs':rows}]}
            q=p.parent.parent/'WideValidation.json';q.write_text(json.dumps(result))
            write_actual_result(p.parent.parent, race)
            report=shadow.build(root)
            self.assertEqual(report['counts']['matched'],1)
            self.assertAlmostEqual(report['returnIndex'],410/300,places=4)
            result['races'][0]['pairs'][0]['name1']='別馬'
            q.write_text(json.dumps(result))
            self.assertEqual(shadow.build(root)['counts']['entrantChanged'],1)
            p.write_text('{}')
            self.assertEqual(shadow.build(root)['counts']['invalid'],1)

    def test_actual_result_identity_and_cancellations_are_independent(self):
        race,s=fixture()
        with tempfile.TemporaryDirectory() as t:
            p=axis.record_first(t,race,s,datetime.fromisoformat('2026-10-17T09:51:00+09:00'))
            day=p.parent.parent
            actual=write_actual_result(day, race)
            # The saved forecast names stay unchanged while the actual roster changes.
            actual['horses'][0]['name']='結果側の別馬'
            (day/'ResultData.json').write_text(json.dumps([actual]))
            report=shadow.build(t)
            self.assertEqual(report['counts']['entrantChanged'],1)
            self.assertEqual(report['counts']['matched'],0)
            self.assertIsNone(report['returnIndex'])
            for status in ['取消','除外']:
                actual=write_actual_result(day, race)
                actual['horses'][5].update(finishPosition=None,resultStatus=status)
                (day/'ResultData.json').write_text(json.dumps([actual]))
                self.assertEqual(shadow.build(t)['counts']['entrantChanged'],1)
            for status in ['中止','失格']:
                actual=write_actual_result(day, race)
                actual['horses'][5].update(finishPosition=None,resultStatus=status)
                (day/'ResultData.json').write_text(json.dumps([actual]))
                post=datetime.fromisoformat('2026-10-17T10:00:00+09:00')
                self.assertIsNone(shadow.actual_result_state(day,json.loads(p.read_text()),post))

    def test_actual_result_missing_incomplete_corrupt_and_day_mismatch(self):
        race,s=fixture()
        with tempfile.TemporaryDirectory() as t:
            p=axis.record_first(t,race,s,datetime.fromisoformat('2026-10-17T09:51:00+09:00'))
            day=p.parent.parent
            self.assertEqual(shadow.build(t)['counts']['awaitingResults'],1)
            actual=write_actual_result(day, race)
            actual['horses'][0].update(finishPosition=None,resultStatus='不明')
            (day/'ResultData.json').write_text(json.dumps([actual]))
            self.assertEqual(shadow.build(t)['counts']['awaitingResults'],1)
            actual=write_actual_result(day, race);actual['raceDate']='2026-10-18'
            (day/'ResultData.json').write_text(json.dumps([actual]))
            self.assertEqual(shadow.build(t)['counts']['invalid'],1)
            actual=write_actual_result(day, race);actual['resultUpdatedAtJST']='2026-10-17 09:59:00'
            (day/'ResultData.json').write_text(json.dumps([actual]))
            self.assertEqual(shadow.build(t)['counts']['invalid'],1)
            actual=write_actual_result(day, race);actual['horses']={}
            (day/'ResultData.json').write_text(json.dumps([actual]))
            self.assertEqual(shadow.build(t)['counts']['invalid'],1)
            (day/'ResultData.json').write_text('{broken')
            self.assertEqual(shadow.build(t)['counts']['invalid'],1)

    def test_actual_result_next_day_refresh_preserves_race_day(self):
        race,s=fixture()
        with tempfile.TemporaryDirectory() as t:
            p=axis.record_first(t,race,s,datetime.fromisoformat('2026-10-17T09:51:00+09:00'))
            actual=write_actual_result(p.parent.parent, race)
            actual['resultUpdatedAtJST']='2026-10-18 00:10:00'
            (p.parent.parent/'ResultData.json').write_text(json.dumps([actual]))
            # The payout validation is still absent; identity can be checked without
            # counting it as a matched result or replacing the final payout.
            self.assertEqual(shadow.build(t)['counts']['awaitingResults'],1)


if __name__=='__main__':
    import sys
    if '--emit-contract' in sys.argv:
        index=sys.argv.index('--emit-contract'); target=Path(sys.argv[index+1]);del sys.argv[index:index+2]
        race,s=fixture()
        target.write_text(json.dumps({'race':race,'evaluation':axis.evaluate(race,s)},ensure_ascii=False))
    unittest.main()
