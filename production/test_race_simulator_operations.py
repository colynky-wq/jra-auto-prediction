import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import race_simulator_learning as learning
from race_simulator_calibration import baseline, load_registry
from race_simulator_guards import conditions, comparisons, monitor_decision
from race_simulator_operations import build_status, note_attempt
import test_race_simulator_learning as fixtures
SMALL,challenger,fixture,result_for=fixtures.SMALL,fixtures.challenger,fixtures.fixture,fixtures.result_for

POLICY=replace(SMALL,min_condition_races=1,min_condition_days=1,max_validation_races=8,
               min_monitor_races=4,min_monitor_days=2)


class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def rows(self,model):
        rows=fixtures.LearningTests.validation_rows(self,model)
        for i,row in enumerate(rows):
            row['record']['conditions']={'surface':'turf' if i%2==0 else 'dirt',
                'distanceBand':('short','mile','middle','long')[i%4]}
            row['evaluation']['resultSHA256']=str(i)
        return rows

    def monitor_rows(self,model,harm=False):
        rows=self.rows(model)
        for row in rows:
            r=row['record'];r.update(activeModelID=model['modelID'],candidateModelID=None,monitorModelID=baseline()['modelID'])
            r['forecasts'][baseline()['modelID']]={'model':baseline()}
            if harm:row['evaluation']['scores'][model['modelID']]['loss']=.7
        return rows

    def monitoring(self,model):
        return {'activeModelID':model['modelID'],'referenceModelID':baseline()['modelID'],
                'adoptedAt':'2026-11-10T00:00:00+00:00','checkedRaceIDs':[],'checkedCohorts':0}

    def save_state(self,model,monitoring=None,candidate=False):
        learning.write_json(self.root/'research/race_simulator/models'/(model['modelID']+'.json'),model)
        state=load_registry(self.root)
        state.update(activeModelID=baseline()['modelID'] if candidate else model['modelID'],
                     candidateModelID=model['modelID'] if candidate else None,monitoring=monitoring)
        learning.write_json(self.root/'research/race_simulator/state.json',state)

    def test_conditions_use_only_prepost_course_and_fixed_boundaries(self):
        for distance,band in [(1400,'short'),(1401,'mile'),(1800,'mile'),(1801,'middle'),(2201,'long')]:
            self.assertEqual(conditions({'course':'ダート'},distance)['distanceBand'],band)
        for race in ({'course':'障芝3000m'},{'course':'不明'}):
            with self.assertRaises(ValueError):conditions(race,3000)

    def test_missing_conditions_wait_without_using_score_to_choose_window(self):
        model=challenger();rows=self.rows(model)
        for row in rows[:4]:row['record']['conditions']={'surface':'turf','distanceBand':'mile'}
        decision=learning.promotion_decision(rows[:4],model,baseline(),POLICY)
        self.assertEqual(decision['status'],'awaiting_validation')
        self.assertTrue(any(c['status']=='insufficient_data' for c in decision['conditionCoverage']))
        decision=learning.promotion_decision(rows,model,baseline(),POLICY)
        self.assertEqual(decision['status'],'promote');self.assertEqual(decision['validationRaces'],8)

    def test_insufficient_coverage_at_fixed_limit_rejects(self):
        model=challenger();rows=self.rows(model)
        for row in rows:row['record']['conditions']={'surface':'turf','distanceBand':'mile'}
        d=learning.promotion_decision(rows,model,baseline(),POLICY)
        self.assertEqual(d['status'],'reject');self.assertIn('insufficient_condition_coverage',d['reasons'])

    def test_overall_improvement_cannot_hide_dirt_regression(self):
        model=challenger();rows=self.rows(model)
        for row in rows:
            row['evaluation']['scores'][model['modelID']]['loss']=.6 if row['record']['conditions']['surface']=='dirt' else .1
        d=learning.promotion_decision(rows,model,baseline(),POLICY)
        self.assertGreater(d['relativeImprovement'],0)
        self.assertEqual(d['status'],'reject');self.assertIn('condition_regressed:surface:dirt',d['reasons'])

    def test_post_adoption_waits_for_multiple_races_and_days(self):
        model=challenger();rows=self.monitor_rows(model,True);m=self.monitoring(model)
        self.assertEqual(monitor_decision(rows[:1],m,model,baseline(),POLICY)['status'],'awaiting_monitor_results')
        for row in rows:row['record']['raceDate']='2026-11-14'
        self.assertEqual(monitor_decision(rows,m,model,baseline(),POLICY)['status'],'awaiting_monitor_results')

    def test_sustained_degradation_rolls_back_but_healthy_window_does_not(self):
        model=challenger();m=self.monitoring(model)
        good=monitor_decision(self.monitor_rows(model),m,model,baseline(),POLICY)
        self.assertEqual(good['status'],'monitor_pass')
        bad=monitor_decision(self.monitor_rows(model,True),m,model,baseline(),POLICY)
        self.assertEqual(bad['status'],'rollback');self.assertGreater(bad['pairedLowerMeanDegradation'],0)
        m['checkedRaceIDs']=bad['checkedRaceIDs']
        following=monitor_decision(self.monitor_rows(model),m,model,baseline(),POLICY)
        self.assertFalse(set(following['checkedRaceIDs'])&set(m['checkedRaceIDs']))

    def test_condition_degradation_alone_can_trigger_rollback(self):
        model=challenger();rows=self.monitor_rows(model)
        for row in rows:row['evaluation']['scores'][model['modelID']]['loss']=.7 if row['record']['conditions']['surface']=='dirt' else .1
        d=monitor_decision(rows,self.monitoring(model),model,baseline(),POLICY)
        self.assertLess(d['relativeDegradation'],0)
        self.assertEqual(d['status'],'rollback');self.assertIn('condition_degradation:surface:dirt',d['reasons'])

    def test_cycle_persists_rollback_and_does_not_immediately_refit(self):
        model=challenger();self.save_state(model,self.monitoring(model))
        with patch.object(learning,'load_settled',return_value=(self.monitor_rows(model,True),[])),patch.object(learning,'fit_candidate') as fit:
            report=learning.cycle(self.root,learning.timestamp('2026-11-16T00:00:00+00:00'),POLICY)
        state=load_registry(self.root)
        self.assertEqual(state['activeModelID'],baseline()['modelID']);self.assertIsNone(state['monitoring'])
        self.assertIn(model['modelID'],state['rolledBackModelIDs']);fit.assert_not_called()
        self.assertEqual(report['latestMonitorDecision']['status'],'rollback')
        self.assertTrue(list((self.root/'research/race_simulator/decisions').glob('*.json')))
        with patch.object(learning,'load_settled',return_value=(self.monitor_rows(model,True),[])),patch.object(learning,'fit_candidate') as fit:
            learning.cycle(self.root,learning.timestamp('2026-11-16T01:00:00+00:00'),POLICY)
        fit.assert_not_called()

    def test_promotion_starts_shadow_monitor_and_pauses_new_training(self):
        model=challenger();self.save_state(model,candidate=True)
        with patch.object(learning,'load_settled',return_value=(self.rows(model),[])),patch.object(learning,'fit_candidate') as fit:
            learning.cycle(self.root,learning.timestamp('2026-11-16T00:00:00+00:00'),POLICY)
        state=load_registry(self.root)
        self.assertEqual(state['activeModelID'],model['modelID']);self.assertEqual(state['monitoring']['referenceModelID'],baseline()['modelID'])
        fit.assert_not_called()

    def test_capture_saves_reference_forecast_before_post(self):
        model=challenger();self.save_state(model,self.monitoring(model))
        race,research,profiles,_=fixture();race['raceDate']='2026-11-14';race['capturedAtJST']='2026-11-14T09:48:00+09:00'
        research['races'][0]['raceDate']=race['raceDate'];now=learning.timestamp('2026-11-14T09:50:00+09:00')
        record=learning.capture_one(self.root,race,research,profiles,now,SMALL,clock=lambda:now)
        self.assertEqual(record['monitorModelID'],baseline()['modelID'])
        self.assertEqual(set(record['forecasts']),{model['modelID'],baseline()['modelID']})
        learning.validate_record(record,now)

    def test_schedule_reveals_missing_race_even_without_racedata(self):
        race,_,_,now=fixture()
        learning.write_json(self.root/'research/race_simulator/state.json',
            {**load_registry(self.root),'operationsStartedAt':'2026-11-06T00:00:00+00:00'})
        learning.write_json(self.root/'reports/20261107/PaceRecordingSchedule.json',{'raceDate':race['raceDate'],'races':[race]})
        report=build_status(self.root,now+timedelta(hours=1))
        self.assertTrue(report['expectedScheduleAvailable']);self.assertEqual(report['counts'],{'missed':1})

    def test_failed_attempt_is_persistent_and_never_hidden_as_success(self):
        race,research,profiles,now=fixture();research['races'][0]['horses'][0]['name']='別馬'
        learning.write_json(self.root/'RaceData.json',[race]);learning.write_json(self.root/'AppResearchData.json',research)
        learning.capture_upcoming(self.root,now,SMALL,clock=lambda:now)
        report=learning.read_json(self.root/'RaceSimulatorRecordingStatus.json')
        self.assertEqual(report['counts'],{'failed':1});self.assertFalse(report['races'][0]['durableConfirmed'])
        self.assertTrue((self.root/'reports/20261107/race_simulator_attempts.json').exists())

    def test_results_pending_verified_and_matched_are_distinct(self):
        race,research,profiles,now=fixture()
        record=learning.capture_one(self.root,race,research,profiles,now,SMALL,clock=lambda:now)
        self.assertEqual(build_status(self.root,now)['counts'],{'local_saved':1})
        resolver=lambda path:now
        self.assertEqual(build_status(self.root,now,verify_commits=True,commit_resolver=resolver)['counts'],{'result_waiting':1})
        report=build_status(self.root,now,records=[{'record':record}],verify_commits=True,commit_resolver=resolver)
        self.assertEqual(report['counts'],{'matched':1});self.assertEqual(report['liveVerification']['status'],'verified')

    def test_pre_rollout_history_is_excluded_and_missing_schedule_is_explicit(self):
        race,_,_,now=fixture();learning.write_json(self.root/'RaceData.json',[race])
        report=build_status(self.root,now+timedelta(days=2))
        self.assertEqual(report['counts'],{'excluded':1});self.assertFalse(report['expectedScheduleAvailable'])
        self.assertEqual(report['liveVerification']['status'],'awaiting_live_race')


if __name__=='__main__':unittest.main()
