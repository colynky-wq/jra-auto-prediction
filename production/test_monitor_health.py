import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from monitor_health_runtime import (DAY_STEP, NO_RACES_STEP, JST, MONITOR_STEP, REPOSITORY, WORKFLOW,
                                    apply_health, choose_health, expire_waiting_races,
                                    health_from_event, load_health)

NOW = datetime(2026, 10, 17, 17, 0, tzinfo=JST)


def event(run_id=123, status='completed', conclusion='failure', started='2026-10-17T00:00:00Z'):
    return {'repository': {'full_name': REPOSITORY}, 'workflow_run': {
        'id': run_id, 'name': WORKFLOW, 'head_repository': {'full_name': REPOSITORY},
        'head_branch': 'main', 'event': 'schedule', 'status': status, 'conclusion': conclusion,
        'created_at': started, 'run_started_at': started}}


def baseline(rows=None):
    rows = rows or []
    return {'schemaVersion': 1, 'status': 'recording_active' if rows else 'awaiting_first_schedule',
            'generatedAtJST': '2026-10-17 16:00:00', 'recordingStartDate': '2026-10-13',
            'races': rows, 'warnings': [], 'counts': {'totalRaces': len(rows), 'failed': 0},
            'emptyMessage': '初回待ち', 'paceForecastEnabled': False,
            'formalSignalsChanged': False, 'stakeSizingEnabled': False}


class TestMonitorHealth(unittest.TestCase):
    def health(self, status='failure', steps=None, run_id=123):
        jobs = [{'status': 'completed', 'conclusion': status, 'steps': steps or []}]
        return health_from_event(event(run_id, conclusion=status), jobs, NOW)

    def test_health_refresh_expires_only_past_waiting_races(self):
        board = baseline([
            {'raceID': '202605040601', 'raceDate': '2026-10-17',
             'startTime': '09:50', 'recordingState': 'waiting',
             'recordingLabel': '記録待ち', 'reason': '発走前の記録を待っています。',
             'resultState': 'no_prediction', 'needsAttention': False},
            {'raceID': '202605040602', 'raceDate': '2026-10-17',
             'startTime': '17:50', 'recordingState': 'waiting',
             'resultState': 'no_prediction', 'needsAttention': False},
            {'raceID': '202605040603', 'raceDate': '2026-10-17',
             'startTime': '09:10', 'recordingState': 'saved',
             'resultState': 'awaiting_result', 'needsAttention': False},
        ])
        board['counts'].update(waiting=2, missing=0, needsAttention=0)
        original = copy.deepcopy(board)
        now = datetime(2026, 10, 17, 10, 10, tzinfo=JST)
        fixed = expire_waiting_races(board, now)
        self.assertEqual([r['recordingState'] for r in fixed['races']],
                         ['missing', 'waiting', 'saved'])
        self.assertEqual((fixed['counts']['waiting'], fixed['counts']['missing'],
                          fixed['counts']['needsAttention']), (1, 1, 1))
        self.assertEqual(fixed['races'][0]['resultState'], 'no_prediction')
        self.assertEqual(board, original)  # no in-place modification
        self.assertIs(expire_waiting_races(fixed, now), fixed)

    def test_previous_day_unknown_start_time_is_not_waiting(self):
        board = baseline([{'raceID': '202605040601', 'raceDate': '2026-10-16',
                           'startTime': None, 'recordingState': 'waiting',
                           'resultState': 'no_prediction', 'needsAttention': False}])
        fixed = expire_waiting_races(board, NOW)
        self.assertEqual(fixed['races'][0]['recordingState'], 'missing')
        self.assertEqual(fixed['counts']['needsAttention'], 1)

    def test_startup_failure_does_not_invent_failed_races(self):
        health = self.health(steps=[{'name': DAY_STEP, 'conclusion': 'failure'},
                                  {'name': MONITOR_STEP, 'conclusion': 'skipped'}])
        self.assertEqual(health['status'], 'startup_failed')
        board = apply_health(baseline(), health)
        self.assertEqual(board['counts']['failed'], 0)
        self.assertEqual(board['races'], [])
        self.assertEqual(board['status'], 'recording_attention_required')
        self.assertIn('対象件数', board['emptyMessage'])
        self.assertTrue(board['warnings'])

    def test_no_races_and_actual_monitor_success_are_distinct(self):
        absent = self.health('success', [{'name': DAY_STEP, 'conclusion': 'success'},
                                         {'name': NO_RACES_STEP, 'conclusion': 'success'},
                                         {'name': MONITOR_STEP, 'conclusion': 'skipped'}])
        complete = self.health('success', [{'name': MONITOR_STEP, 'conclusion': 'success'}])
        self.assertEqual(absent['status'], 'no_races')
        self.assertEqual(complete['status'], 'completed')
        self.assertFalse(apply_health(baseline(), absent)['warnings'])

    def test_green_workflow_with_skipped_monitor_is_not_capture_success(self):
        skipped = self.health('success', [
            {'name': DAY_STEP, 'conclusion': 'success'},
            {'name': MONITOR_STEP, 'conclusion': 'skipped'}])
        self.assertEqual(skipped['status'], 'not_started')
        self.assertTrue(skipped['needsAttention'])
        self.assertTrue(apply_health(baseline(), skipped)['warnings'])
        # Missing job details must not be mistaken for a completed monitor.
        self.assertEqual(self.health('success')['status'], 'not_started')

    def test_failure_does_not_discard_saved_rows(self):
        board = baseline([{'raceID': '202605040601', 'recordingState': 'saved'}])
        health = self.health(steps=[{'name': MONITOR_STEP, 'conclusion': 'failure'}])
        updated = apply_health(board, health)
        self.assertEqual(updated['races'], board['races'])
        self.assertEqual(updated['counts'], board['counts'])
        self.assertEqual(health['status'], 'failed')

    def test_markdown_older_rows_cannot_confirm_a_failed_monitor_day(self):
        from pace_recording_display import markdown
        row = {'raceID': '202605040501', 'raceDate': '2026-10-16', 'venue': '東京',
               'raceNumber': 1, 'startTime': '10:00', 'recordingLabel': '予測保存済み',
               'resultLabel': '照合済み', 'reason': '保存済み', 'issues': []}
        board = apply_health(baseline([row]), self.health())
        rendered = markdown(board)
        self.assertIn('監視日の対象件数は未確認', rendered)
        self.assertIn('記録一覧の対象 1件', rendered)
        self.assertIn('2026-10-16', rendered)
        self.assertEqual(board['races'], [row])
        self.assertEqual(board['counts']['failed'], 0)

    def test_markdown_known_monitor_day_retains_counts_without_unknown_notice(self):
        from pace_recording_display import markdown
        row = {'raceID': '202605040601', 'raceDate': '2026-10-17', 'venue': '東京',
               'raceNumber': 1, 'startTime': '10:00', 'recordingLabel': '予測保存済み',
               'resultLabel': '結果待ち', 'reason': '保存済み', 'issues': []}
        board = apply_health(baseline([row]), self.health())
        rendered = markdown(board)
        self.assertNotIn('監視日の対象件数は未確認', rendered)
        self.assertIn('記録一覧の対象 1件', rendered)
        self.assertIn('自動監視', rendered)

    def test_completed_state_cannot_be_downgraded_by_delayed_start_event(self):
        complete = self.health('success', [{'name': MONITOR_STEP, 'conclusion': 'success'}])
        active = health_from_event(event(status='in_progress', conclusion=None), [], NOW)
        active['observedAtJST'] = '2026-10-17 18:00:00'
        active['runStartedAtJST'] = '2026-10-17 09:01:00'
        self.assertEqual(choose_health(complete, active)['status'], 'completed')

    def test_newer_session_replaces_old_failure_and_keeps_other_warnings(self):
        failed = self.health()
        old = apply_health(baseline(), failed)
        old['warnings'].append('別の記録が不整合です。')
        new = health_from_event(event(124, 'in_progress', None, '2026-10-17T05:30:00Z'), [], NOW)
        combined = apply_health(old, choose_health(failed, new))
        self.assertEqual(combined['monitoring']['status'], 'active')
        self.assertEqual(combined['warnings'], ['別の記録が不整合です。'])

    def test_untrusted_repo_branch_and_workflow_are_rejected(self):
        for field, value in [('head_branch', 'feature'), ('name', 'Other'), ('event', 'pull_request')]:
            with self.subTest(field=field):
                sample = event(); sample['workflow_run'][field] = value
                with self.assertRaises(ValueError): health_from_event(sample, [], NOW)
        sample = event(); sample['workflow_run']['head_repository']['full_name'] = 'foreign/repo'
        with self.assertRaises(ValueError): health_from_event(sample, [], NOW)

    def test_journal_identity_and_corrupt_file_are_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); folder = root/'20261017/monitor_sessions'; folder.mkdir(parents=True)
            healthy = self.health('success')
            (folder/'123.json').write_text(json.dumps(healthy))
            (folder/'124.json').write_text('{unfinished')
            selected, warnings = load_health(root, '2026-10-17')
            self.assertEqual(selected['runID'], 123); self.assertTrue(warnings)

    def test_cancelled_run_and_unavailable_job_details_remain_visible(self):
        cancelled = self.health('cancelled')
        generic = self.health()
        self.assertEqual(cancelled['status'], 'interrupted')
        self.assertEqual(generic['status'], 'failed')
        self.assertTrue(generic['needsAttention'])

    def test_cancelled_pending_heartbeat_has_no_worker_and_cannot_hide_running_monitor(self):
        # Reproduces Nov 22: run 44977082119 was cancelled with zero jobs
        # when a later queued heartbeat replaced it. Run 44973990350
        # still had an actual monitor job in progress.
        active = health_from_event(
            event(run_id=44973990350, status='in_progress', conclusion=None,
                  started='2026-11-22T01:59:50Z'),
            [{'status': 'in_progress', 'conclusion': None,
              'started_at': '2026-11-22T01:59:50Z',
              'steps': [{'name': MONITOR_STEP, 'status': 'in_progress'}]}],
            datetime(2026, 11, 22, 13, 8, tzinfo=JST))
        pending = health_from_event(
            event(run_id=44977082119, conclusion='cancelled',
                  started='2026-11-22T02:39:11Z'), [],
            datetime(2026, 11, 22, 13, 9, tzinfo=JST))
        self.assertEqual(pending['status'], 'superseded')
        self.assertFalse(pending['needsAttention'])
        self.assertEqual(choose_health(active, pending)['runID'], 44973990350)
        board = apply_health(baseline(), choose_health(active, pending))
        self.assertFalse(board['monitorWarnings'])
        self.assertEqual(choose_health(pending)['status'], 'superseded')
        self.assertEqual(choose_health(active, pending, active)['status'], 'active')

    def test_cancelled_run_with_unavailable_jobs_fails_closed(self):
        event_data = event(run_id=223, conclusion='cancelled')
        uncertain = health_from_event(event_data, None, NOW)
        actual = health_from_event(event_data, [
            {'status': 'completed', 'conclusion': 'cancelled',
             'steps': [{'name': MONITOR_STEP, 'status': 'in_progress'}]}], NOW)
        self.assertEqual(uncertain['status'], 'interrupted')
        self.assertTrue(uncertain['needsAttention'])
        self.assertEqual(actual['status'], 'interrupted')

    def test_pending_replacement_only_hides_other_runs_on_same_date(self):
        old = health_from_event(event(1, 'in_progress', None,
                                      '2026-10-16T00:00:00Z'), [], NOW)
        newer = health_from_event(event(run_id=2, conclusion='cancelled',
                                        started='2026-10-17T00:00:00Z'), [], NOW)
        self.assertEqual(choose_health(old, newer)['status'], 'superseded')

    def test_delayed_queue_uses_actual_job_start_day(self):
        sample = event(started='2026-10-16T14:00:00Z')
        jobs = [{'status': 'completed', 'conclusion': 'success',
                 'started_at': '2026-10-17T00:00:00Z', 'steps': []}]
        health = health_from_event(sample, jobs, NOW)
        self.assertEqual(health['raceDate'], '2026-10-17')


class TestHealthBoardConnection(unittest.TestCase):
    def test_empty_board_displays_startup_failure_without_fake_counts(self):
        from test_pace_recording_status import TestRecordingStatus
        from test_pace_prospective import NOW as clock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture = TestRecordingStatus()
            folder, pred, actual, history, obs = fixture.setup_case(root)
            folder.joinpath('index.json').unlink()
            folder.joinpath('sample.json').unlink()
            folder.rmdir()
            health = health_from_event(event(), [{'status': 'completed', 'conclusion': 'failure',
                                                  'steps': [{'name': DAY_STEP, 'conclusion': 'failure'}]}], clock)
            journal = root/'reports/20261017/monitor_sessions/123.json'
            journal.parent.mkdir(parents=True); journal.write_text(json.dumps(health))
            board = fixture.run_case(root, history.iloc[:0], clock)
            self.assertEqual(board['counts']['totalRaces'], 0)
            self.assertEqual(board['status'], 'recording_attention_required')
            self.assertEqual(board['monitoring']['status'], 'startup_failed')


if __name__ == '__main__':
    unittest.main()
