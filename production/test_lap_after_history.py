"""Isolated checks of the post-history trigger, without scraping or forecasts."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lap_after_history as trigger


def event():
    return {'action': 'completed', 'repository': {'full_name': trigger.REPOSITORY},
            'workflow_run': {'id': 1234, 'name': trigger.WORKFLOW, 'path': trigger.WORKFLOW_PATH,
                             'head_repository': {'full_name': trigger.REPOSITORY}, 'head_branch': 'main',
                             'event': 'schedule', 'status': 'completed', 'conclusion': 'success',
                             'run_started_at': '2026-10-17T12:15:00Z',
                             'updated_at': '2026-10-17T12:55:00Z'}}


def report(day='20261017', generated='2026-10-17T21:49:22+09:00'):
    return {'targetDate': day, 'generatedAtJST': generated, 'applyRequested': True,
            'oldRows': 100, 'newRows': 3, 'combinedRows': 103,
            'oldRaceCount': 10, 'addedRaceCount': 1, 'combinedRaceCount': 11,
            'oldLatestDate': '2026-10-12', 'newLatestDate': datetime.strptime(day, '%Y%m%d').date().isoformat(),
            'duplicateRaceHorseKeys': 0, 'status': 'applied'}


NOW = datetime.fromisoformat('2026-10-17T23:00:00+09:00')


class LapAfterHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, value, name=None):
        path = self.root / (name or value['targetDate'] + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def plan(self, payload=None, now=NOW):
        return trigger.select_plan(payload or event(), self.root, now)

    def test_delayed_history_after_2145_selects_applied_day(self):
        path = self.write(report()); before = path.read_bytes()
        plan = self.plan()
        self.assertEqual((plan['shouldCollect'], plan['targetDate']), (True, '20261017'))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_no_race_success_does_not_reuse_old_applied_report(self):
        self.write(report('20261013', '2026-10-13T21:20:00+09:00'))
        self.assertFalse(self.plan()['shouldCollect'])
        self.assertEqual(self.plan()['targetDate'], '')

    def test_completing_after_midnight_keeps_original_target_day(self):
        payload = event(); payload['workflow_run'].update(run_started_at='2026-10-17T14:55:00Z', updated_at='2026-10-17T15:05:00Z')
        self.write(report(generated='2026-10-18T00:01:00+09:00'))
        self.assertEqual(self.plan(payload, datetime.fromisoformat('2026-10-18T01:00:00+09:00'))['targetDate'], '20261017')

    def test_applied_manual_update_is_supported(self):
        payload = event(); payload['workflow_run']['event'] = 'workflow_dispatch'
        self.write(report())
        self.assertTrue(self.plan(payload)['shouldCollect'])

    def test_failure_cancel_and_incomplete_run_never_collect(self):
        self.write(report())
        for field, value in [('conclusion', 'failure'), ('conclusion', 'cancelled'), ('status', 'in_progress')]:
            with self.subTest(field=field, value=value):
                payload = event(); payload['workflow_run'][field] = value
                with self.assertRaises(trigger.TriggerError): self.plan(payload)

    def test_other_repo_branch_workflow_path_or_event_rejected(self):
        self.write(report())
        for field, value in [('head_repository', {'full_name': 'fork/repo'}), ('head_branch', 'feature'),
                             ('name', 'Different Workflow'), ('path', 'different.yml'), ('event', 'pull_request')]:
            with self.subTest(field=field):
                payload = event(); payload['workflow_run'][field] = value
                with self.assertRaises(trigger.TriggerError): self.plan(payload)
        payload = event(); payload['repository']['full_name'] = 'fork/repo'
        with self.assertRaises(trigger.TriggerError): self.plan(payload)

    def test_wrong_action_and_boolean_run_id_rejected(self):
        for payload in [{**event(), 'action': 'in_progress'}, {**event(), 'workflow_run': {**event()['workflow_run'], 'id': True}}]:
            with self.assertRaises(trigger.TriggerError): self.plan(payload)

    def test_naive_reversed_or_future_run_times_rejected(self):
        for values in [{'run_started_at': '2026-10-17T21:15:00'},
                       {'run_started_at': '2026-10-17T12:56:00Z'},
                       {'updated_at': '2026-10-18T12:55:00Z'}]:
            payload = event(); payload['workflow_run'].update(values)
            with self.assertRaises(trigger.TriggerError): self.plan(payload)

    def test_report_outside_run_window_does_not_trigger(self):
        self.write(report(generated='2026-10-17T21:56:00+09:00'))
        self.assertFalse(self.plan()['shouldCollect'])

    def test_dry_run_bad_date_future_or_bad_counts_rejected(self):
        for values in [{'status': 'validated'}, {'applyRequested': False}, {'targetDate': '20261018'},
                       {'newLatestDate': '2026-10-18'}, {'oldLatestDate': '2026-10-17'},
                       {'newRows': True}, {'newRows': 0}, {'combinedRows': 104},
                       {'combinedRaceCount': 12}, {'duplicateRaceHorseKeys': 1}]:
            with self.subTest(values=values):
                self.write({**report(), **values}, '20261017.json')
                with self.assertRaises(trigger.TriggerError): self.plan()
        (self.root/'20261017.json').unlink()
        self.write(report('20261018'))
        with self.assertRaises(trigger.TriggerError): self.plan()

    def test_two_applied_days_in_same_run_are_ambiguous(self):
        self.write(report()); self.write(report('20261016'))
        with self.assertRaises(trigger.TriggerError): self.plan()

    def test_corrupt_report_is_not_healthy_no_change(self):
        (self.root/'20261017.json').write_text('{')
        with self.assertRaises(trigger.TriggerError): self.plan()

    def test_valid_day_survives_corrupt_old_report(self):
        (self.root/'20261013.json').write_text('{'); self.write(report())
        plan = self.plan()
        self.assertTrue(plan['shouldCollect']); self.assertEqual(plan['unreadableReports'], ['20261013.json'])

    def test_report_before_prospective_start_does_not_trigger(self):
        self.write(report('20261012'))
        self.assertFalse(self.plan()['shouldCollect'])

    def test_cli_only_writes_validated_output_and_no_race_files(self):
        self.write(report()); event_path = self.root/'event-input.txt'; event_path.write_text(json.dumps(event()))
        output = self.root/'action-output.txt'
        with patch.object(trigger, 'datetime') as clock:
            clock.now.return_value = NOW
            clock.strptime = datetime.strptime; clock.fromisoformat = datetime.fromisoformat
            self.assertEqual(trigger.main(['--event', str(event_path), '--reports-root', str(self.root), '--github-output', str(output)]), 0)
        self.assertIn('should_collect=true\ntarget_date=20261017\nsource_run_id=1234\n', output.read_text())
        self.assertEqual(len(list(self.root.iterdir())), 3)
        before = output.read_bytes(); event_path.write_text(json.dumps({**event(), 'action': 'in_progress'}))
        self.assertEqual(trigger.main(['--event', str(event_path), '--reports-root', str(self.root), '--github-output', str(output)]), 2)
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
