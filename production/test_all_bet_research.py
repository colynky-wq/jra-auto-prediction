"""Boundary and end-to-end fixtures are synthetic and confined to temp roots."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import all_bet_baseline as b
import all_bet_forward as f
from evaluate_all_bet_forward import build,package_rows,saved_payouts
from test_triple_baseline import race,result

NOW = datetime.fromisoformat('2026-10-17T10:15:00+09:00')


def forecast(n=8):
    value=race(n); value['raceDate']='2026-10-17'; return value


def entry(r):
    rid=r['raceID']; day=r['raceDate'].replace('-','')
    token='pw01dde01'+rid[4:6]+rid[:4]+rid[6:]+day+'/AC'
    return {'raceID':rid,'raceDate':r['raceDate'],'scheduledPostJST':r['raceDate']+'T'+r['startTime']+':00+09:00',
            'capturedAtJST':(NOW-timedelta(minutes=1)).isoformat(),'sourceSHA256':'a'*64,
            'sourceURL':'https://www.jra.go.jp/JRADB/accessD.html?CNAME='+token,
            'runners':[{'number':h['number'],'name':h['name'],'frame':min(8,(h['number']+1)//2),
                        'withdrawn':False} for h in r['horses']]}


def payload(r,kind):
    keys=b.probabilities(r['horses'],'aiProbability',kind,entry(r))
    return {'status':'middle','data':{'official_datetime':'2026-10-17 10:14:00',
            'odds':{str(b.TYPES[kind]):{k:['1.4','2.9' if kind=='place' else '0.0','2'] for k in keys}}}}


def payouts_html(r,kind,keys,amounts=None):
    amounts=amounts or [110+10*i for i in range(len(keys))]
    numbers=' '.join(str(int(k[i:i+2])) for k in keys for i in range(0,len(k),2))
    return (f'<meta property="og:url" content="https://race.netkeiba.com/race/result.html?race_id={r["raceID"]}">'
            f'<table><tr><th>{b.LABELS[kind]}</th><td>{numbers}</td><td>'+
            '<br>'.join(f'{x:,}円' for x in amounts)+'</td></tr></table>')


class MathTests(unittest.TestCase):
    def test_uniform_all_pools_probability_mass_and_same_frame(self):
        for n in (5,7,8,18):
            r=forecast(n); e=entry(r)
            for kind in b.TYPES:
                values=b.probabilities(r['horses'],'aiProbability',kind,e)
                self.assertAlmostEqual(sum(values.values()),(2 if n<=7 else 3) if kind=='place' else 1)
                if kind=='win':self.assertEqual(len(values),n)
                if kind=='exacta':self.assertEqual(len(values),n*(n-1))
                if kind=='quinella':self.assertEqual(len(values),math.comb(n,2))
                if kind=='place':self.assertAlmostEqual(values['01'],(2 if n<=7 else 3)/n)
                if kind=='frame_quinella':
                    self.assertIn('0101',values)
                    self.assertAlmostEqual(values['0101'],2/(n*(n-1)))

    def test_nonuniform_pair_order_and_grouping(self):
        r=forecast(4)
        for h,p in zip(r['horses'],(40,30,20,10)):h['aiProbability']=p
        exact=b.probabilities(r['horses'],'aiProbability','exacta')
        unordered=b.probabilities(r['horses'],'aiProbability','quinella')
        self.assertAlmostEqual(exact['0102'],.4*.3/.6)
        self.assertAlmostEqual(unordered['0102'],exact['0102']+exact['0201'])
        self.assertNotAlmostEqual(exact['0102'],.4*.3)

    def test_place_terms_never_follow_smaller_active_field(self):
        r=forecast(8); e=entry(r)
        self.assertEqual(b.place_count(e,r['horses']),3)
        e['runners'][-1]['withdrawn']=True
        with self.assertRaises(ValueError):b.place_count(e,r['horses'])
        r['horses'].pop()
        with self.assertRaises(ValueError):b.place_count(e,r['horses'])
        r=forecast(7); self.assertEqual(b.place_count(entry(r),r['horses']),2)
        r['horses'][-1]['number']=8
        with self.assertRaises(ValueError):b.place_count(entry(r),r['horses'])

    def test_small_fields_and_invalid_weights_frames(self):
        r=forecast(2); self.assertEqual(len(b.probabilities(r['horses'],'aiProbability','win')),2)
        for kind in ('place','exacta','quinella'):
            with self.assertRaises(ValueError):b.probabilities(r['horses'],'aiProbability',kind,entry(r))
        r=forecast(8); e=entry(r);e['runners'][0]['frame']=None
        with self.assertRaises(ValueError):b.probabilities(r['horses'],'aiProbability','frame_quinella',e)
        for invalid in (True,0,-1,float('nan'),float('inf'),'12.5'):
            r=forecast();r['horses'][0]['aiProbability']=invalid
            with self.assertRaises(ValueError):b.win_weights(r['horses'],'aiProbability')


class ForwardTests(unittest.TestCase):
    def save(self,root,kind='win',r=None,fetch=None,clock=lambda:NOW,evidence=True):
        r=r or forecast()
        return f.record_first(root,r,kind,NOW.isoformat(),entry(r) if evidence else None,
                              fetch=fetch or (lambda _,k:payload(r,k)),clock=clock)

    def test_all_five_capture_once_tamper_and_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for kind in b.TYPES:
                path=self.save(root,kind); before=path.read_bytes(); value=f.validate(json.loads(before))
                self.assertEqual(value['status'],'ready')
                self.save(root,kind,fetch=lambda *_:self.fail('refetch'))
                self.assertEqual(path.read_bytes(),before)
                value['apiStatus']='result';value.pop('recordFingerprint');value['recordFingerprint']=f.digest(value)
                with self.assertRaises(ValueError):f.validate(value)

    def test_odds_coverage_ranges_and_commas(self):
        table={'01':['1,234.5','2,345.6']}
        self.assertEqual(f.parse_table(table,{'01'},'place')['01'],{'low':1234.5,'high':2345.6})
        for values in (['2','1'],['NaN','3'],['0','1'],[True,'2'],['1'],['inf','inf']):
            with self.assertRaises(ValueError):f.parse_table({'01':values},{'01'},'place')
        with self.assertRaises(ValueError):f.parse_table(table,{'01','02'},'place')

    def test_first_failure_and_missing_entry_terms_stay_held(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for kind in ('place','frame_quinella'):
                path=self.save(root,kind,evidence=False,fetch=lambda *_:self.fail('missing terms fetched'))
                self.assertEqual(json.loads(path.read_text())['reason'],'entry_terms_unconfirmed')
                self.save(root,kind)
                self.assertEqual(json.loads(path.read_text())['status'],'held')
            path=self.save(root,'win',fetch=lambda *_:(_ for _ in ()).throw(OSError('offline')))
            self.assertEqual(json.loads(path.read_text())['reason'],'odds_unavailable')

    def test_final_wrong_day_future_and_incomplete_odds_saved_as_holds(self):
        for mode in ('result','yesterday','future','incomplete','wrong_type','list'):
            def fetch(_,kind):
                value=payload(forecast(),kind)
                if mode=='result':value['status']='result'
                if mode=='yesterday':value['data']['official_datetime']='2026-10-16 10:14:00'
                if mode=='future':value['data']['official_datetime']='2026-10-17 10:16:00'
                if mode=='incomplete':value['data']['odds']['1'].pop('01')
                if mode=='wrong_type':value['data']['odds']={}
                if mode=='list':return []
                return value
            with tempfile.TemporaryDirectory() as temp:
                self.assertEqual(json.loads(self.save(Path(temp),fetch=fetch).read_text())['status'],'held')

    def test_no_after_post_day_or_fsync_deadline_backfill(self):
        for now in (NOW-timedelta(days=1),NOW+timedelta(days=1),NOW.replace(minute=20)):
            with tempfile.TemporaryDirectory() as temp:
                self.assertIsNone(self.save(Path(temp),clock=lambda:now,fetch=lambda *_:self.fail('blocked fetch')))
        with tempfile.TemporaryDirectory() as temp:
            times=iter([NOW,NOW,NOW,NOW.replace(minute=20)])
            self.assertIsNone(self.save(Path(temp),clock=lambda:next(times)))
            self.assertEqual(list(Path(temp).rglob('*.json')),[])

    def test_entry_source_date_route_and_entrant_guards(self):
        r=forecast()
        for mode in ('url','post','future','hash','date','name'):
            e=entry(r)
            if mode=='url':e['sourceURL']=e['sourceURL'].replace('jra.go.jp','example.invalid')
            if mode=='post':e['scheduledPostJST']=e['scheduledPostJST'].replace('10:20','10:21')
            if mode=='future':e['capturedAtJST']='2026-10-17T10:16:00+09:00'
            if mode=='hash':e['sourceSHA256']=''
            if mode=='date':e['raceDate']='2026-10-16'
            if mode=='name':e['runners'][0]['name']='wrong'
            with tempfile.TemporaryDirectory() as temp:
                path=f.record_first(temp,r,'place',NOW.isoformat(),e,fetch=lambda *_:payload(r,'place'),clock=lambda:NOW)
                self.assertEqual(json.loads(path.read_text())['status'],'held')

    def test_independent_actual_and_each_payout_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); r=forecast(); actual=result(r);actual['resultUpdatedAtJST']='2026-10-17 10:30:00'
            for kind in b.TYPES:
                record=json.loads(self.save(root,kind).read_text())
                truth=b.truth_keys(r,kind,entry(r),(1,2,3))
                parsed=f.payout_map(payouts_html(r,kind,sorted(truth)),r['raceID'],kind)
                joined=f.evaluate(record,actual,parsed)
                self.assertEqual(joined['status'],'matched')
                self.assertEqual(sum(v['hit'] for v in joined['rows']),len(truth))
                self.assertIsNone(joined['returnIndex'])
                with self.assertRaises(ValueError):f.evaluate(record,actual,{'1818':100})
                self.assertEqual(f.evaluate(record,None,None)['status'],'awaiting_results')
                actual['horses'][0]['name']='changed'
                self.assertEqual(f.evaluate(record,actual,None)['status'],'entrant_changed')
                actual['horses'][0]['name']='horse1'
            actual['horses'][-1]['finishPosition']=3
            self.assertEqual(f.evaluate(record,actual,None)['status'],'dead_heat_excluded')

    def test_payout_order_frame_double_place_and_identity(self):
        r=forecast()
        self.assertEqual(f.payout_map(payouts_html(r,'frame_quinella',['0101']),r['raceID'],'frame_quinella'),{'0101':110})
        self.assertEqual(f.payout_map(payouts_html(r,'exacta',['0201']),r['raceID'],'exacta'),{'0201':110})
        self.assertEqual(len(f.payout_map(payouts_html(r,'place',['01','02']),r['raceID'],'place')),2)
        with self.assertRaises(ValueError):f.payout_map(payouts_html(r,'win',['01']),'202605040503','win')

    def test_summary_real_paths_original_date_cache_and_package_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); r=forecast(); actual=result(r);actual['resultUpdatedAtJST']='2026-10-17 10:30:00'
            for kind in b.TYPES:self.save(root,kind)
            (root/'reports/20261017/ResultData.json').write_text(json.dumps([actual]))
            # Pick the requested pool using its immutable cache's path via the
            # source parser; the same result document contains all pools.
            source=''.join(payouts_html(r,k,sorted(b.truth_keys(r,k,entry(r),(1,2,3)))) for k in b.TYPES)
            report=build(root,fetch=lambda _:source.encode(),clock=lambda:NOW+timedelta(days=1))
            self.assertTrue(all(c['matched']==1 for c in report['counts'].values()))
            package=next(row for row in report['races'] if row['betType']=='support')
            self.assertEqual(package['costPerPackageYen'],200)
            candidate=package['candidates'][1]
            self.assertTrue(candidate['hit']);self.assertLess(candidate['payoutYen'],200)
            self.assertLess(candidate['profitYen'],0)
            build(root,fetch=lambda _:self.fail('cache refetched'),clock=lambda:NOW+timedelta(days=2))
            self.assertEqual(report['races'][0]['raceDate'],'2026-10-17')
            self.assertIsNone(report['returnIndex'])

    def test_summary_missing_payout_pending_future_and_invalid_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=self.save(root); (root/'RaceData.json').write_text(json.dumps([forecast()]))
            report=build(root,fetch=lambda _:self.fail('result not ready'),clock=lambda:NOW)
            self.assertEqual(report['counts']['win']['awaiting_results'],1)
            self.assertEqual(report['counts']['place']['missingRecords'],1)
            self.assertEqual(report['status'],'attention_required')
            actual=result(forecast());actual['resultUpdatedAtJST']='2026-10-17 10:30:00'
            (root/'reports/20261017/ResultData.json').write_text(json.dumps([actual]))
            report=build(root,fetch=lambda _:(_ for _ in ()).throw(OSError()),clock=lambda:NOW+timedelta(hours=1))
            self.assertEqual(report['counts']['win']['payouts_pending'],1)
            report=build(root,clock=lambda:NOW-timedelta(days=1))
            self.assertEqual(report['counts']['win']['invalid'],1)
            value=json.loads(path.read_text());value['odds']['01']['low']=99;path.write_text(json.dumps(value))
            self.assertEqual(build(root,clock=lambda:NOW)['counts']['win']['invalid'],1)

    def test_background_copy_once_without_waiting(self):
        import all_bet_runtime as runtime
        submitted=[]
        class Future:
            def done(self):return False
        class Pool:
            def submit(self,*args):
                submitted.append(args)
                return Future()
        r=forecast();e=entry(r)
        with patch.object(runtime,'_pool',Pool()),patch.object(runtime,'_futures',{}):
            first=runtime.schedule('.',r,e,NOW.isoformat())
            second=runtime.schedule('.',r,e,NOW.isoformat())
            self.assertIs(first,second)
            r['horses'][0]['name']='changed';e['runners'][0]['frame']=8
            self.assertEqual(len(submitted),1)
            self.assertEqual(submitted[0][2]['horses'][0]['name'],'horse1')
            self.assertEqual(submitted[0][3],NOW.isoformat())
            self.assertEqual(submitted[0][4]['runners'][0]['frame'],1)


if __name__=='__main__':unittest.main()
