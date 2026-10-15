"""Reference UI contract from synthetic, immutable inputs in temp folders only."""
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_bet_research_data as ui
import all_bet_forward as added
import triple_forward as triple
from evaluate_all_bet_forward import build as evaluate_added
from test_all_bet_research import NOW, forecast, entry, payload, payouts_html
from test_triple_baseline import result
from test_triple_forward import html as triple_html
import test_win5_research as win5_tests
from test_wide_axis_rules import fixture as wide_fixture


def seed(root):
    race=forecast()
    for kind in ui.single.TYPES:
        added.record_first(root,race,kind,NOW.isoformat(),entry(race),
                           fetch=lambda _,k:payload(race,k),clock=lambda:NOW)
    for kind in triple.TYPES:
        source={'status':'middle','data':{'official_datetime':'2026-10-17 10:14:00',
                'odds':{str(triple.TYPES[kind]):{k:['1792.4'] for k in triple.expected_keys(kind,range(1,9))}}}}
        triple.record_first(root,race,kind,NOW.isoformat(),fetch=lambda *_:source,clock=lambda:NOW)
    r,s=wide_fixture()
    ui.wide.record_first(root,r,s,NOW.replace(hour=9,minute=51))
    win5_tests.WIN5Tests().save(root)
    return race


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def contract():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);seed(root)
        value=ui.build(root,clock=lambda:win5_tests.NOW)
        return {**value,'raceDate':'2026-10-17'}


class ProjectionTests(unittest.TestCase):
    def row(self,value,kind):return next(r for r in value['races'] if r['betType']==kind)

    def test_all_ten_from_frozen_inputs_no_network_and_no_input_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);seed(root);before=snapshot(root)
            with patch('urllib.request.urlopen',side_effect=AssertionError('projection fetched')):
                value=ui.build(root,clock=lambda:win5_tests.NOW)
            self.assertEqual(snapshot(root),before)
            self.assertEqual({r['betType'] for r in value['races']},{t[0] for t in ui.CATALOG})
            self.assertTrue(all(r['status']=='awaiting_results' for r in value['races']))
            self.assertTrue(all(len(r['candidates'])==3 for r in value['races']))
            self.assertTrue(value['referenceDisplayEnabled']);self.assertFalse(value['formalRecommendationsEnabled'])
            self.assertFalse(value['autoBetEnabled']);self.assertFalse(value['stakeSizingEnabled'])
            self.assertEqual(self.row(value,'support')['candidates'][0]['unitYen'],200)
            self.assertIsNone(self.row(value,'win5')['candidates'][0]['oddsLow'])
            self.assertAlmostEqual(self.row(value,'wide')['candidates'][0]['probabilityPct'],28)

    def test_zero_old_history_and_missing_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=forecast();old['raceDate']='2026-10-12'
            (root/'RaceData.json').write_text(json.dumps([old]))
            value=ui.build(root,clock=lambda:NOW)
            self.assertEqual(value['races'],[])
            self.assertTrue(all(t['cumulative']['records']==0 for t in value['catalog']))
            (root/'RaceData.json').write_text(json.dumps([forecast()]))
            value=ui.build(root,clock=lambda:NOW)
            self.assertEqual(len(value['races']),9)
            self.assertTrue(all(r['status']=='missing_record' and not r['candidates'] for r in value['races']))
            self.assertEqual(value['collectionStatus'],'attention_required')

    def test_invalid_held_and_failed_session_never_display_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);seed(root)
            p=next(root.glob('reports/*/all_bet_v1/win/*.json'));p.write_text('{}')
            p=next(root.glob('reports/*/all_bet_v1/place/*.json'));p.unlink()
            added.record_first(root,forecast(),'place',NOW.isoformat(),None,clock=lambda:NOW)
            p=next(root.glob('reports/*/win5_v1/session.json'));p.unlink()
            (p.parent.parent/'win5_status.json').write_text(json.dumps({'raceDate':'2026-10-17','hasTarget':None,'status':'failed'}))
            value=ui.build(root,clock=lambda:win5_tests.NOW)
            for kind,state in [('win','invalid'),('place','held'),('win5','failed')]:
                r=self.row(value,kind);self.assertEqual(r['status'],state);self.assertFalse(r['candidates'])
            self.assertEqual(value['collectionStatus'],'attention_required')

    def test_cached_payouts_support_partial_return_and_deterministic_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);r=seed(root);actual=result(r);actual['resultUpdatedAtJST']='2026-10-17 10:30:00'
            day=root/'reports/20261017';(day/'ResultData.json').write_text(json.dumps([actual]))
            source=''.join(payouts_html(r,k,sorted(ui.single.truth_keys(r,k,entry(r),(1,2,3)))) for k in ui.single.TYPES)
            evaluate_added(root,fetch=lambda _:source.encode(),clock=lambda:win5_tests.NOW)
            from evaluate_triple_forward import build as eval_triple
            source=triple_html('trio')+triple_html('trifecta')
            eval_triple(root,fetch=lambda _:source.encode(),clock=lambda:win5_tests.NOW)
            before=snapshot(root);value=ui.build(root,clock=lambda:win5_tests.NOW)
            self.assertEqual(snapshot(root),before)
            self.assertTrue(all(self.row(value,k)['status']=='matched' for k in (*ui.single.TYPES,'trio','trifecta','support')))
            package=self.row(value,'support')['candidates'][1]
            self.assertTrue(package['hit']);self.assertLess(package['payoutYen'],200)
            self.assertEqual(package['unitYen'],200)
            # Selection is the saved AI order; never sorted after seeing payouts.
            self.assertEqual([c['id'] for c in self.row(value,'trifecta')['candidates']],['010203','010204','010205'])
            actual['horses'][0]['name']='changed'
            (day/'ResultData.json').write_text(json.dumps([actual]))
            value=ui.build(root,clock=lambda:win5_tests.NOW)
            self.assertEqual(self.row(value,'win')['status'],'entrant_changed')
            self.assertFalse(self.row(value,'win')['candidates'])

    def test_corrupt_or_wrong_day_result_does_not_become_waiting_or_zero(self):
        for mode in ('broken','wrong_day','future'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);r=seed(root);actual=result(r);actual['resultUpdatedAtJST']='2026-10-17 10:30:00'
                if mode=='wrong_day':actual['raceDate']='2026-10-18'
                if mode=='future':actual['resultUpdatedAtJST']='2026-10-18 10:30:00'
                (root/'reports/20261017/ResultData.json').write_text('{broken' if mode=='broken' else json.dumps([actual]))
                value=ui.build(root,clock=lambda:win5_tests.NOW)
                for kind in (*ui.single.TYPES,'trio','trifecta','wide','win5'):
                    self.assertEqual(self.row(value,kind)['status'],'invalid')
                    self.assertFalse(self.row(value,kind)['candidates'])

    def test_win5_top_three_heap_agrees_with_exhaustive_small_distribution(self):
        tiny=ui.candidate('01',['馬'],1e-15,1e-16)
        self.assertGreater(tiny['probabilityPct'],0)
        self.assertGreater(tiny['marketProbabilityPct'],0)
        predictions=[]
        for i in range(5):
            r=forecast(2);r['raceNumber']=i+1;r['venue']='試験'
            for h,ai in zip(r['horses'],[60,40]):h.update(aiProbability=ai,marketProbability=50)
            predictions.append({'prediction':r})
        actual=ui.best_win5({'predictions':predictions})
        weights=sorted((.6**numbers.count(1)*.4**numbers.count(2) for numbers in product([1,2],repeat=5)),reverse=True)[:3]
        self.assertEqual(len({c['id'] for c in actual}),3)
        for c,p in zip(actual,weights):self.assertAlmostEqual(c['probabilityPct'],p*100,places=7)
        self.assertTrue(all(c['oddsLow'] is None and c['payoutYen'] is None for c in actual))

    def test_wide_actual_payouts_match_frozen_pairs_and_reject_disagreement(self):
        from test_wide_axis_rules import write_actual_result
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);race,signal=wide_fixture()
            p=ui.wide.record_first(root,race,signal,NOW.replace(hour=9,minute=51))
            write_actual_result(p.parent.parent,race)
            pairs=[]
            for value in signal['wideEvaluation']['validationPairs']:
                key=(value['number1'],value['number2']);yen={(1,6):410,(1,4):100,(4,6):450}.get(key,0)
                pairs.append({**value,'oddsLow':value['wideOddsLow'],'oddsHigh':value['wideOddsHigh'],
                              'hit':yen>0,'payoutPer100Yen':yen})
            data={'races':[{'raceID':race['raceID'],'validationScope':'all_pairs',
                  'snapshotCapturedAtJST':signal['wideEvaluation']['capturedAtJST'],'pairs':pairs}]}
            cache=p.parent.parent/'WideValidation.json';cache.write_text(json.dumps(data))
            row=self.row(ui.build(root,clock=lambda:NOW),'wide')
            self.assertEqual(row['status'],'matched');self.assertEqual(row['candidates'][0]['payoutYen'],410)
            data['races'][0]['snapshotCapturedAtJST']='2026-10-17 09:49:00';cache.write_text(json.dumps(data))
            row=self.row(ui.build(root,clock=lambda:NOW),'wide')
            self.assertEqual(row['status'],'invalid');self.assertFalse(row['candidates'])


if __name__=='__main__':
    import sys
    if '--emit-contract' in sys.argv:
        i=sys.argv.index('--emit-contract');target=Path(sys.argv[i+1]);del sys.argv[i:i+2]
        target.write_text(json.dumps(contract(),ensure_ascii=False))
    unittest.main()
