import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import race_simulator_learning as learning
from race_simulator_calibration import baseline, digest, engine_digest, load_registry, settings, validate_model
from race_simulator_v02 import HorseInput, simulate_race

SMALL = replace(learning.Policy(), min_training_races=4, min_training_days=2,
                min_validation_races=4, min_validation_days=2, training_runs=2,
                capture_runs=4, bootstrap_draws=200, min_condition_races=0)


def fixture(day='2026-11-07', number=1):
    rid = '20260505' + ('01' if day.endswith('07') else '02') + f'{number:02}'
    horses = [{'number':n, 'name':f'試験馬{n}', 'horseKey':f'試験馬{n}_2024', 'aiProbability':p}
              for n,p in [(1,70),(2,20),(3,10)]]
    race = {'raceID':rid,'raceDate':day,'startTime':'10:00','raceNumber':number,
            'raceName':'試験','venue':'東京','course':'芝1600m','horses':horses,
            'predictionTiming':'live_10min','captureQuality':'formal_10min',
            'capturedAtJST':day+'T09:48:00+09:00'}
    research = {'races':[{'raceID':rid,'raceDate':day,'horses':[
        {'number':n,'name':f'試験馬{n}',
         'styleTendency':{'dominantStyle':style,'sampleRaces':5,'distributionPct':{style:100},'latestHistoryDate':'2026-10-31'},
         'initialPositionHistory':{'meanRelativePosition':pos,'latestHistoryDate':'2026-10-31'}}
        for n,style,pos in [(1,'差し',.8),(2,'先行',.3),(3,'逃げ',.1)]]}]}
    profiles = {'profiles':{str(n):{'horseKey':h['horseKey'],'latestDate':'2026-10-31',
        'distanceStats':[{'distanceBand':'1600-1799m','starts':8,'smoothedPerformanceScore':30+20*n}]}
        for n,h in enumerate(horses,1)}}
    now=learning.timestamp(day+'T09:50:00+09:00')
    return race,research,profiles,now


def result_for(race):
    return {'raceID':race['raceID'],'raceDate':race['raceDate'],
        'horses':[{'number':n,'name':f'試験馬{n}','finishPosition':4-n,'passingOrder':str(4-n),'last3F':35.0}
                  for n in (1,2,3)],
        'raceLap':{'raceID':race['raceID'],'raceDate':race['raceDate'],'distanceM':1600,
                   'winnerTimeSeconds':98.0,'first600mSeconds':36.0,
                   'validation':{k:True for k in ('headingMatches','distanceMatches','winnerTimeMatches','raceLast600mMatches')}}}


def seal(record):
    record['recordSHA256']=digest({k:v for k,v in record.items() if k != 'recordSHA256'})
    return record


def challenger(training=('training-1',)):
    model={'schemaVersion':1,'settings':settings({'speedScale':.98}),'engineSHA256':engine_digest(),
        'createdAt':'2026-11-09T00:00:00+00:00','trainingCutoff':'2026-11-08T07:00:00+00:00',
        'trainingRaceIDs':list(training),'baselineModelID':baseline()['modelID'],'objectiveVersion':learning.OBJECTIVE}
    model['modelID']='rs-'+digest(model)[:24]
    return validate_model(model)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def record(self, day='2026-11-07', number=1):
        race,research,profiles,now=fixture(day,number)
        r=learning.capture_one(self.root,race,research,profiles,now,SMALL,clock=lambda:now+timedelta(seconds=1))
        return r,race,now

    def test_immutable_capture_and_no_retrospective_records(self):
        r,race,now=self.record()
        path=learning.journal_path(self.root,race);original=path.read_bytes()
        self.assertIsNone(learning.capture_one(self.root,race,{}, {},now,SMALL))
        self.assertEqual(path.read_bytes(),original)
        with self.assertRaises(ValueError): learning.write_json(path,{'changed':True},immutable=True)
        other=fixture(number=2)
        with self.assertRaises(ValueError):
            learning.capture_one(self.root,*other[:3],learning.post_time(other[0])+timedelta(seconds=1),SMALL)
        self.assertFalse(learning.journal_path(self.root,other[0]).exists())

    def test_deadline_during_inference_does_not_save(self):
        race,research,profiles,now=fixture()
        with self.assertRaises(ValueError):
            learning.capture_one(self.root,race,research,profiles,now,SMALL,clock=lambda:learning.post_time(race))
        self.assertFalse(learning.journal_path(self.root,race).exists())

    def test_live_display_and_batch_reuse_frozen_prediction(self):
        from build_race_simulation_data_v02 import build_batch
        record,race,now=self.record()
        learning.write_json(self.root/'RaceData.json',[race])
        learning.write_json(self.root/'AppResearchData.json',{'races':[]})
        report=learning.capture_upcoming(self.root,now,SMALL,clock=lambda:now)
        self.assertEqual(report['saved'],[])
        display=learning.read_json(self.root/'RaceSimulationData.json')['races'][0]
        self.assertEqual(display,record['displaySimulation'])
        changed=copy.deepcopy(race)
        changed['horses'][0]['aiProbability']=0.01
        batch=build_batch([changed],{'races':[]},self.root/'missing-profiles',runs=2,learning_root=self.root)
        self.assertEqual(batch['status'],'ok')
        self.assertEqual(batch['races'][0],record['displaySimulation'])

    def test_ai_market_changes_do_not_change_recorded_motion(self):
        race,research,profiles,now=fixture()
        first=learning.capture_one(self.root,race,research,profiles,now,SMALL,clock=lambda:now)
        with tempfile.TemporaryDirectory() as td:
            altered=copy.deepcopy(race)
            for h in altered['horses']:h.update(aiProbability=1,odds=999,popularity=18,marketProbability=99)
            second=learning.capture_one(td,altered,research,profiles,now,SMALL,clock=lambda:now)
        self.assertEqual(first['motion'],second['motion'])
        self.assertEqual(first['forecasts'],second['forecasts'])
        self.assertNotEqual(first['inputSHA256'],second['inputSHA256'])

    def test_future_profile_and_style_are_not_inputs(self):
        race,research,profiles,_=fixture()
        bad=copy.deepcopy(profiles);bad['profiles']['1']['latestDate']=race['raceDate']
        badresearch=copy.deepcopy(research);badresearch['races'][0]['horses'][0]['styleTendency']['latestHistoryDate']=race['raceDate']
        cleaned_r,cleaned_p=learning.clean_sources(race,badresearch,bad)
        self.assertNotIn('1',cleaned_p['profiles'])
        self.assertNotIn('styleTendency',cleaned_r['races'][0]['horses'][0])

    def test_capture_requires_formal_timing_and_matching_identity(self):
        race,research,profiles,now=fixture();race['captureQuality']='late_recovery'
        with self.assertRaises(ValueError): learning.capture_one(self.root,race,research,profiles,now,SMALL)
        race['captureQuality']='formal_10min';research['races'][0]['horses'][0]['name']='別馬'
        with self.assertRaises(ValueError): learning.capture_one(self.root,race,research,profiles,now,SMALL)

    def test_durable_prepost_commit_evidence_and_tamper_guard(self):
        r,_,_=self.record();made=learning.timestamp(r['recordedAt'])
        learning.validate_record(r,made)
        for invalid in (None,learning.timestamp(r['scheduledPost'])):
            with self.assertRaises(ValueError): learning.validate_record(r,invalid)
        r['motion']['horses'][0]['independent_strength']=1.5
        with self.assertRaises(ValueError): learning.validate_record(r,made)

    def test_first_git_commit_proves_prepost_publication(self):
        r,race,_=self.record();path=learning.journal_path(self.root,race)
        def git(*args,**kwargs):
            return subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True,**kwargs)
        git('init','-b','main');git('config','user.name','test');git('config','user.email','test@example.invalid')
        git('add','.')
        made=learning.timestamp(r['recordedAt'])
        env={**os.environ,'GIT_AUTHOR_DATE':learning.stamp(made),'GIT_COMMITTER_DATE':learning.stamp(made)}
        git('commit','-m','Freeze pre-post forecast',env=env)
        proved=learning.first_committed_at(self.root,path)
        learning.validate_record(r,proved)
        learning.write_json(self.root/'ResultData.json',[result_for(race)])
        records,held=learning.load_settled(self.root,learning.post_time(race)+timedelta(hours=1))
        self.assertEqual(len(records),1);self.assertEqual(held,[])
        r['seed']+=1;seal(r);learning.write_json(path,r)
        with self.assertRaisesRegex(ValueError,'modified since first commit'):
            learning.first_committed_at(self.root,path)

    def test_cancelled_changed_and_incomplete_results_are_held(self):
        r,race,_=self.record();now=learning.post_time(race)+timedelta(hours=1)
        good=result_for(race);learning.evaluate_record(r,good,now)
        for field,value in [('name','別馬'),('finishPosition',None),('number',8)]:
            invalid=copy.deepcopy(good);invalid['horses'][0][field]=value
            with self.assertRaises(ValueError): learning.evaluate_record(r,invalid,now)
        good['raceDate']='2026-11-08'
        with self.assertRaises(ValueError): learning.evaluate_record(r,good,now)

    def test_sectionals_use_exact_600m_and_missing_data_stays_missing(self):
        r,race,_=self.record();p=r['forecasts'][r['activeModelID']]['prediction'];actual=result_for(race)
        m=learning.metrics(p,actual)
        self.assertIsNotNone(m['first600MAPE']);self.assertIsNotNone(m['last600MAPE'])
        actual['raceLap']['first600mSeconds']=None
        actual['raceLap']['earlyRecordedDistanceM']=650;actual['raceLap']['earlyRecordedTimeSeconds']=38
        for horse in actual['horses']:horse['last3F']=None
        m=learning.metrics(p,actual)
        self.assertIsNone(m['first600MAPE']);self.assertIsNone(m['last600MAPE'])

    def test_bounds_and_clock_calibration_affect_real_engine(self):
        for bad in ({'speedScale':float('nan')},{'odds':1},{'speedScale':1.5},{'styleScale':True}):
            with self.assertRaises(ValueError): settings(bad)
        horses=[HorseInput(1,'A'),HorseInput(2,'B')]
        a=simulate_race(horses,1600,runs=3,seed=11)
        b=simulate_race(horses,1600,runs=3,seed=11,calibration={'speedScale':.9})
        self.assertGreater(b['flowPrediction']['meanFirst600Seconds'],a['flowPrediction']['meanFirst600Seconds'])
        self.assertTrue(all(h['averageLast600Seconds']>0 for h in b['horses']))

    def test_cycle_fits_challenger_but_never_promotes_on_training(self):
        records=[];results=[]
        for day in ('2026-11-07','2026-11-08'):
            for number in (1,2):
                r,race,_=self.record(day,number);records.append(r);results.append(result_for(race))
        learning.write_json(self.root/'ResultData.json',results)
        resolver=lambda p:learning.timestamp(learning.read_json(p)['recordedAt'])
        now=learning.timestamp('2026-11-09T00:00:00+00:00')
        report=learning.cycle(self.root,now,SMALL,resolver)
        self.assertEqual(report['settledProspectiveRaces'],4)
        state=load_registry(self.root);self.assertEqual(state['activeModelID'],baseline()['modelID'])
        candidate=learning.load_model(self.root,state['candidateModelID'])
        self.assertEqual(set(candidate['trainingRaceIDs']),{r['raceID'] for r in records})
        again=learning.cycle(self.root,now+timedelta(hours=1),SMALL,resolver)
        self.assertEqual(again['latestDecision']['status'],'awaiting_validation')
        self.assertEqual(load_registry(self.root)['candidateModelID'],candidate['modelID'])

    def validation_rows(self, model, bad_first=False):
        rows=[];current=baseline()
        for i in range(8):
            good=not bad_first or i>=4
            old={'winnerBrier':.6,'normalizedRankMAE':.3,'winnerTimeMAPE':.05,'first600MAPE':.08,'last600MAPE':.04,'loss':.5}
            new={'winnerBrier':.4 if good else .9,'normalizedRankMAE':.2 if good else .6,
                 'winnerTimeMAPE':.03 if good else .1,'first600MAPE':.06,'last600MAPE':.03,'loss':.3 if good else .7}
            record={'raceID':f'validation-{i}','raceDate':'2026-11-14' if i%2==0 else '2026-11-15',
                    'recordedAt':f'2026-11-{14+i%2:02}T00:00:00+00:00','recordSHA256':str(i),
                    'activeModelID':current['modelID'],'candidateModelID':model['modelID'],
                    'forecasts':{model['modelID']:{'model':model}}}
            rows.append({'record':record,'evaluation':{'scores':{current['modelID']:old,model['modelID']:new}}})
        return rows

    def test_promotion_requires_new_paired_cohort_and_improvement(self):
        model=challenger();rows=self.validation_rows(model)
        self.assertEqual(learning.promotion_decision(rows[:3],model,baseline(),SMALL)['status'],'awaiting_validation')
        decision=learning.promotion_decision(rows,model,baseline(),SMALL)
        self.assertEqual(decision['status'],'promote');self.assertEqual(decision['validationRaces'],4)
        self.assertGreater(decision['paired95LowerMeanImprovement'],0)
        contaminated=copy.deepcopy(model);contaminated['trainingRaceIDs']=['validation-'+str(i) for i in range(8)]
        self.assertEqual(learning.promotion_decision(rows,contaminated,baseline(),SMALL)['status'],'awaiting_validation')

    def test_rejected_first_cohort_cannot_be_rescued_by_peeking(self):
        model=challenger();decision=learning.promotion_decision(self.validation_rows(model,True),model,baseline(),SMALL)
        self.assertEqual(decision['status'],'reject')
        self.assertEqual(decision['validationRaceIDs'],[f'validation-{i}' for i in range(4)])

    def test_insufficient_clock_coverage_blocks_adoption(self):
        model=challenger();rows=self.validation_rows(model)
        for row in rows:
            for score in row['evaluation']['scores'].values():score['first600MAPE']=None
        decision=learning.promotion_decision(rows,model,baseline(),SMALL)
        self.assertEqual(decision['status'],'reject');self.assertIn('insufficient_sectional_coverage',decision['reasons'])

    def test_better_overall_score_cannot_hide_worse_race_flow(self):
        model=challenger();rows=self.validation_rows(model)
        for row in rows:row['evaluation']['scores'][model['modelID']]['first600MAPE']=.20
        decision=learning.promotion_decision(rows,model,baseline(),SMALL)
        self.assertEqual(decision['status'],'reject');self.assertIn('early_section_regressed',decision['reasons'])

    def test_stale_archive_cannot_replace_newer_official_result(self):
        r,race,_=self.record();old=result_for(race);new=copy.deepcopy(old)
        old['resultUpdatedAtJST']=race['raceDate']+' 10:20:00'
        new['resultUpdatedAtJST']=race['raceDate']+' 10:30:00';new['horses'][0]['last3F']=35.2
        learning.write_json(self.root/'ResultData.json',[new])
        learning.write_json(self.root/'reports'/race['raceDate'].replace('-','')/'ResultData.json',[old])
        resolver=lambda p:learning.timestamp(r['recordedAt'])
        settled,_=learning.load_settled(self.root,learning.post_time(race)+timedelta(hours=1),resolver)
        self.assertEqual(settled[0]['result'],new)

    def test_result_revisions_do_not_double_count_races(self):
        r,race,_=self.record();actual=result_for(race)
        learning.write_json(self.root/'ResultData.json',[actual])
        resolver=lambda p:learning.timestamp(r['recordedAt'])
        now=learning.post_time(race)+timedelta(hours=2)
        self.assertEqual(len(learning.load_settled(self.root,now,resolver)[0]),1)
        actual['horses'][0]['last3F']=35.1;learning.write_json(self.root/'ResultData.json',[actual])
        self.assertEqual(len(learning.load_settled(self.root,now,resolver)[0]),1)
        self.assertEqual(len(list((self.root/'reports').glob('*/race_simulator_evaluation/*/*.json'))),2)


if __name__=='__main__': unittest.main()
