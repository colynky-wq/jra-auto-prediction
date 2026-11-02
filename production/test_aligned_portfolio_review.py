"""Acceptance and cohort regression tests using only temporary synthetic records."""
from copy import deepcopy
from datetime import timedelta
from itertools import combinations
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aligned_portfolio_review as review
import formal_portfolio_capture as capture
import portfolio_shadow as portfolio
from test_formal_portfolio_capture import seed,NOW
from test_triple_baseline import result


def prepare(root,skip=False):
    race,odds,_=seed(root)
    if skip:
        for h in race['horses']:h['aiProbability']=h['marketProbability']
        for family,kind in [('all_bet_v1','win'),('triple_v1','trio')]:
            p=root/'reports/20261107'/family/kind/(race['raceID']+'.json');r=json.loads(p.read_text())
            r['prediction']=deepcopy(race);r.pop('recordFingerprint');r['recordFingerprint']=capture.digest(r)
            p.write_text(json.dumps(r))
    capture.record_first(root,race,clock=lambda:NOW)
    return race


def settle(root,race):
    day=root/'reports/20261107';actual=result(race);actual['resultUpdatedAtJST']='2026-11-07 10:30:00'
    (day/'ResultData.json').write_text(json.dumps([actual]))
    names={h['number']:h['name'] for h in race['horses']};pairs=[]
    for a,b in combinations(range(1,9),2):
        hit={a,b}<={1,2,3}
        pairs.append({'number1':a,'number2':b,'name1':names[a],'name2':names[b],
                      'hit':hit,'payoutPer100Yen':500 if hit else 0})
    (day/'WideValidation.json').write_text(json.dumps({'races':[{'raceDate':race['raceDate'],
        'raceID':race['raceID'],'validationScope':'all_pairs','pairs':pairs}]}))
    for family,outfamily,kind,key,amount in [('all_bet_v1','all_bet_payouts_v1','win','01',110),
                                            ('triple_v1','triple_payouts_v1','trio','010203',900)]:
        record=json.loads((day/family/kind/(race['raceID']+'.json')).read_text())
        receipt={'recordFingerprint':record['recordFingerprint'],'sourceURL':'https://race.netkeiba.com/race/result.html?race_id='+race['raceID'],
                 'retrievedAtJST':'2026-11-07T10:31:00+09:00','payouts':{key:amount}}
        receipt['fingerprint']=capture.digest(receipt)
        p=day/outfamily/kind/(race['raceID']+'.json');p.parent.mkdir(parents=True);p.write_text(json.dumps(receipt))


class ReviewTests(unittest.TestCase):
    def test_zero_aligned_never_borrows_legacy_count_or_beta(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            r={'scope':'prospective','ruleVersion':portfolio.VERSION,'raceDate':'2026-10-24',
               'raceID':'202605040801','startTime':'10:05','planType':'skip'}
            source={'schemaVersion':1,'version':portfolio.VERSION,'status':'research_display_only',
                    'prospectiveStartDate':portfolio.PROSPECTIVE_START,'formalRecommendationsEnabled':False,
                    'autoBetEnabled':False,'stakeSizingEnabled':False,'races':[r]}
            (root/'PortfolioResearchData.json').write_text(json.dumps(source))
            p=root/'reports/20261024/all_bet_v1/win/202605040801.json';p.parent.mkdir(parents=True)
            pred={**r,'predictionTiming':'live_10min','capturedAtJST':'2026-10-24T09:54:00+09:00'}
            p.write_text(json.dumps({'schemaVersion':1,'betType':'win','prediction':pred}))
            out=review.build(root,NOW)
            self.assertEqual(out['legacy']['racesObserved'],1);self.assertEqual(out['aligned']['racesObserved'],0)
            self.assertEqual(out['acceptance']['status'],'awaiting_first_capture')
            self.assertFalse(out['betaDecision']['eligibleForResearchBeta'])

    def test_saved_capture_then_independent_results_and_true_trio_increment(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r=prepare(root)
            before=review.build(root,NOW+timedelta(seconds=1))
            self.assertEqual(before['aligned']['racesObserved'],1)
            self.assertEqual(before['aligned']['overall']['settledSelectedRaces'],0)
            self.assertEqual(before['acceptance']['status'],'awaiting_first_settlement')
            settle(root,r)
            out=review.build(root,NOW+timedelta(hours=1));o=out['aligned']['overall']
            self.assertEqual(o['settledSelectedRaces'],1);self.assertGreater(o['returnYen'],0)
            self.assertEqual(out['acceptance']['status'],'complete')
            self.assertFalse(out['acceptance']['restartAndPublicationLiveVerified'])
            inc=out['aligned']['first100']['diagnostics']['trioIncrement']
            self.assertEqual(inc['stakeYen'],100);self.assertEqual(inc['returnYen'],900)
            self.assertEqual(o['returnYen'],sum(l['payoutYen'] for l in out['races'][0]['legs']))
            self.assertEqual(len(out['notificationEvents']),2)
            self.assertFalse(out['betaDecision']['eligibleForResearchBeta'])

    def test_missing_or_inconsistent_payouts_stay_pending_never_become_losses(self):
        for mode in ['missing','inconsistent']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as t:
                root=Path(t);r=prepare(root);settle(root,r)
                p=root/'reports/20261107/WideValidation.json'
                if mode=='missing':p.unlink()
                else:
                    value=json.loads(p.read_text());value['races'][0]['pairs'][-1]['payoutPer100Yen']=999
                    p.write_text(json.dumps(value))
                out=review.build(root,NOW+timedelta(hours=1))
                self.assertEqual(out['aligned']['overall']['settledSelectedRaces'],0)
                self.assertFalse(out['acceptance']['firstSelectedSettlementVerified'])
                if mode=='inconsistent':self.assertEqual(out['quality']['status'],'attention_required')

    def test_no_unrecorded_race_is_promoted_and_window_not_prematurely_missing(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);seed(root)
            early=review.build(root,NOW)
            self.assertEqual(early['quality']['missingCaptureCount'],0)
            late=review.build(root,NOW+timedelta(minutes=4))
            self.assertEqual(late['quality']['missingCaptureCount'],1)
            self.assertEqual(late['aligned']['racesObserved'],0)

    def test_signal_rebinding_tamper_and_future_capture_are_not_counted(self):
        for mode in ['signal','future','source']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as t:
                root=Path(t);r=prepare(root)
                p=capture.path_for(root,r['raceDate'],r['raceID']);data=json.loads(p.read_text())
                if mode=='signal':
                    data['signal']['battle']['grade']='見送り';data.pop('fingerprint');data['fingerprint']=capture.digest(data);p.write_text(json.dumps(data))
                elif mode=='source':
                    q=root/next(iter(data['sources']));q.write_text(q.read_text()+' ')
                now=NOW-timedelta(seconds=1) if mode=='future' else NOW+timedelta(seconds=1)
                out=review.build(root,now)
                self.assertEqual(out['aligned']['racesObserved'],0);self.assertEqual(out['quality']['invalidCaptureCount'],1)

    def test_skipped_reference_is_scored_with_same_rule_and_zero_actual_stake(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r=prepare(root,skip=True);settle(root,r)
            out=review.build(root,NOW+timedelta(hours=1))
            diag=out['aligned']['first100']['diagnostics']
            self.assertEqual(out['aligned']['overall']['selectedRaces'],0)
            self.assertEqual(diag['sameWideRuleSkipped']['races'],1)
            self.assertGreater(diag['sameWideRuleSkipped']['tickets'],0)
            self.assertEqual(out['aligned']['overall']['stakeYen'],0)
            self.assertFalse(out['acceptance']['firstSelectedSettlementVerified'])

    def test_beta_boundary_and_each_quality_gate(self):
        self.assertFalse(review.beta_decision(299,299,True,[],0)['eligibleForResearchBeta'])
        self.assertTrue(review.beta_decision(300,300,True,[],0)['eligibleForResearchBeta'])
        for n,matched,accepted,issues,pending in [(300,299,True,[],0),(300,300,False,[],0),
                                                (300,300,True,[{'code':'bad'}],0),(300,300,True,[],1)]:
            self.assertFalse(review.beta_decision(n,matched,accepted,issues,pending)['eligibleForResearchBeta'])

    def test_first100_locks_survive_late_arrival_or_disappearing_input(self):
        rows=[{'raceDate':'2026-11-07','raceID':f'{i:012}','startTime':f'{i//60:02}:{i%60:02}',
               'inputFingerprint':str(i)} for i in range(1,202)]
        windows,locks,issues=review.lock_windows(rows,{})
        self.assertEqual(len(locks['first100']),100);self.assertEqual(len(locks['confirmation101to200']),100)
        late={'raceDate':'2026-11-03','raceID':'111111111111','startTime':'00:01','inputFingerprint':'late'}
        updated=[late]+rows[1:]
        windows2,locks2,issues=review.lock_windows(updated,{'cohortLocks':locks})
        self.assertEqual(locks2,locks);self.assertEqual(len(windows2['first100']),99)
        self.assertEqual(len(issues),1)

    def test_cli_audit_failure_revokes_stale_beta_readiness(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);_,prior=review.refresh(root,NOW)
            # Simulate a previously ready projection, then fail the next audit.
            prior['betaDecision']['eligibleForResearchBeta']=True
            prior['aligned']['racesObserved']=300
            prior['notificationEvents']=[{'event':'beta_research_ready','key':'prior-ready'}]
            lock=[{'raceDate':'2026-11-07','raceID':'202605040502','inputFingerprint':'prior-frozen'}]
            prior['cohortLocks']={'first100':lock}
            (root/review.JSON_NAME).write_text(json.dumps(prior))
            (root/'PortfolioResearchData.json').write_text('{broken legacy projection')
            run=subprocess.run([sys.executable,str(Path(review.__file__).resolve())],
                env={**os.environ,'KEIBAAI_ROOT':str(root)},capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            failed=json.loads((root/review.JSON_NAME).read_text())
            self.assertFalse(failed['betaDecision']['eligibleForResearchBeta'])
            self.assertEqual(failed['projectionState'],'failed')
            self.assertFalse(failed['aligned']['countsAreCurrent'])
            self.assertEqual(failed['cohortLocks']['first100'],lock)
            self.assertEqual(failed['notificationEvents'],[])

    def test_independent_workflow_publishes_blocked_state_before_legacy_failure(self):
        from test_live_bundle_publisher import command
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'repo';root.mkdir();remote=Path(t)/'remote.git'
            _,prior=review.refresh(root)
            prior['betaDecision']['eligibleForResearchBeta']=True
            (root/review.JSON_NAME).write_text(json.dumps(prior))
            bad='{broken legacy data'
            (root/'PortfolioResearchData.json').write_text(bad)
            command(root,'init','-b','main');command(root,'config','user.name','test')
            command(root,'config','user.email','test@example.invalid')
            command(root,'add','.');command(root,'commit','-m','initial')
            command(root,'clone','--bare',str(root),str(remote));command(root,'remote','add','origin',str(remote))
            workflow=(Path(review.__file__).parent.parent/'.github/workflows/keibaai-formal-review-live.yml').read_text()
            section=workflow.split('      - name: Publish aligned audit independently of legacy projections\n',1)[1]
            section=section.split('      - name: Refresh review, freeze v2 shadow, and publish material diagnostics',1)[0]
            script=section.split('        run: |\n',1)[1]
            script='\n'.join(line[10:] for line in script.splitlines())
            script=script.replace('python production/aligned_portfolio_review.py',
                                  sys.executable+' '+str(Path(review.__file__).resolve()))
            run=subprocess.run(['bash','-c',script],cwd=root,
                               env={**os.environ,'KEIBAAI_ROOT':str(root)},capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            published=json.loads(command(remote,'show','main:'+review.JSON_NAME))
            self.assertEqual(published['projectionState'],'failed')
            self.assertFalse(published['betaDecision']['eligibleForResearchBeta'])
            self.assertEqual(command(remote,'show','main:PortfolioResearchData.json'),bad)

    def test_failed_audit_without_prior_data_uses_unknown_counts_not_zero(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            changed,failed=review.record_audit_failure(root,ValueError('source unavailable'),NOW)
            self.assertTrue(changed);self.assertIsNone(failed['aligned']['racesObserved'])
            self.assertIsNone(failed['betaDecision']['verifiedAlignedRaces'])
            self.assertFalse(failed['betaDecision']['eligibleForResearchBeta'])
            before=(root/review.JSON_NAME).read_bytes()
            changed,_=review.record_audit_failure(root,ValueError('source unavailable'),NOW+timedelta(seconds=1))
            self.assertFalse(changed);self.assertEqual((root/review.JSON_NAME).read_bytes(),before)
            changed,recovered=review.refresh(root,NOW+timedelta(seconds=2))
            self.assertTrue(changed);self.assertEqual(recovered['projectionState'],'verified')
            self.assertTrue(recovered['aligned']['countsAreCurrent'])

    def test_unreadable_projection_does_not_silently_replace_unknown_cohort_locks(self):
        for broken in ['{broken','[]','0','{"x":NaN}']:
            with self.subTest(broken=broken),tempfile.TemporaryDirectory() as t:
                root=Path(t);(root/review.JSON_NAME).write_text(broken)
                _,failed=review.record_audit_failure(root,ValueError('unreadable state'),NOW)
                self.assertTrue(failed['cohortLockRecoveryRequired'])
                self.assertFalse(failed['betaDecision']['eligibleForResearchBeta'])
                with self.assertRaisesRegex(ValueError,'cohort locks'):
                    review.refresh(root,NOW+timedelta(seconds=1))

    def test_settled_refresh_is_stable_across_json_finish_number_keys(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r=prepare(root);settle(root,r)
            changed,first=review.refresh(root,NOW+timedelta(hours=1))
            self.assertTrue(changed)
            self.assertEqual(first['races'][0]['actualFinish']['1'],1)
            before=(root/review.JSON_NAME).read_bytes()
            again,_=review.refresh(root,NOW+timedelta(hours=1,seconds=1))
            self.assertFalse(again)
            self.assertEqual((root/review.JSON_NAME).read_bytes(),before)

    def test_refresh_is_read_only_for_inputs_and_does_not_churn_on_timestamp(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);r=prepare(root)
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with patch('requests.get',side_effect=AssertionError('network')):
                changed,first=review.refresh(root,NOW+timedelta(seconds=1))
                again,_=review.refresh(root,NOW+timedelta(seconds=2))
            self.assertTrue(changed);self.assertFalse(again)
            for p,data in before.items():self.assertEqual((root/p).read_bytes(),data)


if __name__=='__main__':unittest.main()
