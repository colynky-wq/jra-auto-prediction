"""All synthetic capture fixtures stay inside temporary folders."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import triple_forward as f
from evaluate_triple_forward import build, saved_payouts
from test_triple_baseline import race, result

NOW = datetime.fromisoformat('2026-10-17T10:15:00+09:00')


def forecast():
    r = race(); r['raceDate'] = '2026-10-17'
    return r


def payload(kind):
    return {'status':'middle','data':{'official_datetime':'2026-10-17 10:14:00',
            'odds':{str(f.TYPES[kind]):{key:['1,792.4','0.0','1'] for key in f.expected_keys(kind,range(1,5))}}}}


def html(kind, triples=((1,2,3),), yen=(1230,)):
    numbers=''.join('<span>'+str(n)+'</span>' for t in triples for n in t)
    payments='<br>'.join(f'{n:,}円' for n in yen)
    return ('<meta property="og:url" content="https://race.netkeiba.com/race/result.html?race_id=202605040502">'
            f'<table><tr><th>{f.LABELS[kind]}</th><td>{numbers}</td><td>{payments}</td></tr></table>')


class ForwardTests(unittest.TestCase):
    def save(self, root, kind='trio', fetch=None, clock=None):
        return f.record_first(root,forecast(),kind,NOW.isoformat(),
            fetch=fetch or (lambda rid,k:payload(k)),clock=clock or (lambda:NOW))

    def test_both_full_coverages_and_comma_odds(self):
        for kind, count in [('trio',4),('trifecta',24)]:
            self.assertEqual(len(f.odds_map(payload(kind),kind,range(1,5))),count)
            self.assertEqual(next(iter(f.odds_map(payload(kind),kind,range(1,5)).values())),1792.4)
            d=payload(kind); d['status']='result'
            with self.assertRaises(ValueError):f.odds_map(d,kind,range(1,5))

    def test_missing_extra_invalid_and_wrong_type_tables(self):
        for mutation in ('missing','extra','nan','zero','repeated_horse','wrong_kind'):
            d=payload('trio'); table=d['data']['odds']['7']
            if mutation=='missing':table.pop(next(iter(table)))
            if mutation=='extra':table['010205']=['2.0']
            if mutation=='nan':table[next(iter(table))]=['NaN']
            if mutation=='zero':table[next(iter(table))]=['0']
            if mutation=='repeated_horse':table['010102']=table.pop(next(iter(table)))
            if mutation=='wrong_kind':d['data']['odds']={'8':table}
            with self.subTest(mutation=mutation),self.assertRaises((ValueError,KeyError)):
                f.odds_map(d,'trio',range(1,5))

    def test_first_record_immutable_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.save(Path(tmp)); before=p.read_bytes()
            self.save(Path(tmp),fetch=lambda *_:self.fail('existing record refetched'))
            self.assertEqual(before,p.read_bytes())
            d=json.loads(before);d['odds']['010203']=2
            with self.assertRaises(ValueError):f.validate(d)

    def test_old_future_day_and_after_post_never_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            for now in (NOW-timedelta(days=1),NOW+timedelta(days=1),NOW.replace(hour=10,minute=20)):
                self.assertIsNone(self.save(Path(tmp),fetch=lambda *_:self.fail('blocked capture fetched'),clock=lambda:now))
            self.assertEqual(list(Path(tmp).rglob('*.json')),[])

    def test_response_crossing_post_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            times=iter([NOW,NOW.replace(minute=20),NOW.replace(minute=20)])
            self.assertIsNone(self.save(Path(tmp),clock=lambda:next(times)))
            self.assertEqual(list(Path(tmp).rglob('*.json')),[])

    def test_unavailable_and_final_or_wrong_day_source_are_held(self):
        for mode in ('connection','final','wrong_day','future_source','missing_table'):
            def fetch(*_):
                if mode=='connection':raise OSError('temporary')
                d=payload('trio')
                if mode=='final':d['status']='result'
                if mode=='wrong_day':d['data']['official_datetime']='2026-10-16 10:14:00'
                if mode=='future_source':d['data']['official_datetime']='2026-10-17 10:16:00'
                if mode=='missing_table':d['data']['odds']={}
                return d
            with tempfile.TemporaryDirectory() as tmp:
                p=self.save(Path(tmp),fetch=fetch);d=json.loads(p.read_text())
                self.assertEqual(f.validate(d)['status'],'odds_unavailable')
                self.assertEqual(d['odds'],{})

    def test_payout_grouping_order_identity_and_deadheat_rows(self):
        self.assertEqual(f.payout_map(html('trio',((3,1,2),)),'202605040502','trio'),{'010203':1230})
        self.assertEqual(f.payout_map(html('trifecta',((3,1,2),)),'202605040502','trifecta'),{'030102':1230})
        self.assertEqual(len(f.payout_map(html('trifecta',((1,2,3),(2,1,3)),(1230,1850)),'202605040502','trifecta')),2)
        with self.assertRaises(ValueError):f.payout_map(html('trio'),'202605040503','trio')
        with self.assertRaises(ValueError):f.payout_map(html('trio',((1,2,3),(1,2,4)),(1230,)),'202605040502','trio')

    def test_forward_join_independent_actual_identity_and_no_roi(self):
        with tempfile.TemporaryDirectory() as tmp:
            record=json.loads(self.save(Path(tmp)).read_text());r=result(forecast())
            r['resultUpdatedAtJST']='2026-10-17 10:30:00'
            out=f.evaluate(record,r,{'010203':1230})
            self.assertEqual(out['status'],'matched');self.assertEqual(len(out['rows']),4)
            self.assertEqual(sum(x['hit'] for x in out['rows']),1)
            self.assertIsNone(out['returnIndex']);self.assertEqual(out['selectedBets'],0)
            r['horses'][0]['name']='changed'
            self.assertEqual(f.evaluate(record,r,{'010203':1230})['status'],'entrant_changed')

    def test_missing_payout_and_inconsistent_payout_never_losses(self):
        with tempfile.TemporaryDirectory() as tmp:
            record=json.loads(self.save(Path(tmp)).read_text());r=result(forecast())
            r['resultUpdatedAtJST']='2026-10-17 10:30:00'
            self.assertEqual(f.evaluate(record,r,None)['status'],'payouts_pending')
            with self.assertRaises(ValueError):f.evaluate(record,r,{'010204':1230})

    def test_summary_only_frozen_records_and_missing_record_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.save(root)
            (root/'RaceData.json').write_text(json.dumps([forecast()]))
            out=build(root,fetch=lambda *_:self.fail('unfinished result triggered a fetch'),clock=lambda:NOW)
            self.assertEqual(out['counts']['trio']['awaiting_results'],1)
            self.assertEqual(out['counts']['trifecta']['missingRecords'],1)
            self.assertEqual(out['status'],'attention_required')
            self.assertIsNone(out['returnIndex'])

    def test_result_cache_and_full_summary_keep_original_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.save(root);record=json.loads(p.read_text())
            r=result(forecast());r['resultUpdatedAtJST']='2026-10-18 10:30:00'
            (root/'reports/20261017/ResultData.json').write_text(json.dumps([r]))
            later=NOW+timedelta(days=1,minutes=30)
            fetch=lambda _:html('trio').encode()
            out=build(root,fetch=fetch,clock=lambda:later)
            self.assertEqual(out['counts']['trio']['matched'],1)
            self.assertEqual(out['races'][0]['raceDate'],'2026-10-17')
            self.assertIsNone(out['returnIndex'])
            self.assertEqual(saved_payouts(root,record,lambda _:self.fail('cache refetched'),lambda:later),{'010203':1230})
            self.assertEqual(p.read_text(),json.dumps(json.loads(p.read_text()),ensure_ascii=False)+'\n')

    def test_future_record_and_future_result_are_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.save(root)
            out=build(root,clock=lambda:NOW-timedelta(days=1))
            self.assertEqual(out['counts']['trio']['invalid'],1)
            r=result(forecast());r['resultUpdatedAtJST']='2026-10-18 10:30:00'
            (root/'reports/20261017/ResultData.json').write_text(json.dumps([r]))
            out=build(root,clock=lambda:NOW+timedelta(hours=1))
            self.assertEqual(out['counts']['trio']['invalid'],1)

    def test_background_queue_returns_without_fetch_and_freezes_inputs(self):
        import triple_research_runtime as runtime
        submitted=[]
        class Pool:
            def submit(self,*args):submitted.append(args)
        r=forecast()
        with patch.object(runtime,'_pool',Pool()),patch.object(runtime,'_submitted',set()):
            self.assertTrue(runtime.schedule(Path('/unused'),r))
            self.assertFalse(runtime.schedule(Path('/unused'),r))
            r['horses'][0]['name']='changed later'
            self.assertEqual(len(submitted),1)
            self.assertEqual(submitted[0][2]['horses'][0]['name'],'horse1')

    def test_write_crossing_deadline_leaves_no_record_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            times=iter([NOW,NOW,NOW,NOW.replace(minute=20)])
            self.assertIsNone(self.save(Path(tmp),clock=lambda:next(times)))
            self.assertEqual([p for p in Path(tmp).rglob('*') if p.is_file()],[])


if __name__=='__main__':unittest.main()
