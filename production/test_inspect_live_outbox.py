import hashlib
import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from inspect_live_outbox import InspectionError, ReadOnlyGit, inspect
from live_bundle_publisher import JST, Outbox

NOW = datetime(2026, 10, 17, 13, tzinfo=JST)
DATE8 = '20261017'


class TestOutboxInspection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.folder = Path(self.tmp.name) / 'outbox'
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('model.txt', 'fixed')
        self.commit()
        self.source = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True).stdout.decode().strip()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'test snapshot')

    def capture(self):
        box = Outbox(self.root, self.folder, DATE8, today=lambda: '2026-10-17')
        box.capture()
        return box

    def audit(self, now=NOW):
        return inspect(self.folder, self.root, 'main', now)

    def fingerprint(self):
        return {str(p.relative_to(self.tmp.name)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(self.tmp.name).rglob('*') if p.is_file()}

    def test_unpublished_artifact_and_all_files_are_unchanged(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        self.capture()
        before = self.fingerprint()
        result = self.audit()
        self.assertEqual(result['counts'], {'unpublished': 1})
        self.assertTrue(result['needsAttention'])
        self.assertTrue(result['sourceIsAncestor'])
        self.assertEqual(result['comparedCommit'], self.source)
        self.assertEqual(before, self.fingerprint())

    def test_lost_reply_is_already_published_without_touching_pending(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        box = self.capture()
        self.commit()  # Same bytes accepted; retained manifest still says pending.
        before = self.fingerprint()
        result = self.audit()
        self.assertEqual(result['counts'], {'published': 1})
        self.assertFalse(result['needsAttention'])
        self.assertEqual(result['pendingFiles'], 1)
        self.assertEqual(box.state['pending'], json.loads((self.folder / 'state.json').read_text())['pending'])
        self.assertEqual(before, self.fingerprint())

    def test_race_merge_recognizes_remote_superset(self):
        first = {'raceID': '202605040601', 'raceDate': '2026-10-17'}
        second = {'raceID': '202605040602', 'raceDate': '2026-10-17'}
        self.write('RaceData.json', [first])
        self.capture()
        self.write('RaceData.json', [first, second])
        self.commit()
        result = self.audit()
        self.assertEqual(result['counts'], {'represented': 1})
        self.assertFalse(result['needsAttention'])
        self.assertEqual(len(json.loads((self.root / 'RaceData.json').read_text())), 2)

    def test_first_prediction_conflict_and_other_unpublished_are_separate(self):
        path = f'reports/{DATE8}/202605040601/prerace/PacePrediction.json'
        self.write(path, {'fixture': 'first'})
        self.commit()
        self.write(path, {'fixture': 'different'})
        self.write(f'reports/{DATE8}/another.json', {'fixture': 'healthy'})
        self.capture()
        result = self.audit()
        self.assertEqual(result['counts'], {'conflict': 1, 'unpublished': 1})
        self.assertTrue(result['needsAttention'])

    def test_current_mutable_remote_difference_requires_review(self):
        self.write('SignalData.json', {'fixture': 'old'})
        self.commit()
        self.write('SignalData.json', {'fixture': 'new'})
        self.capture()
        self.assertEqual(self.audit()['counts'], {'review_required': 1})

    def test_day_changed_is_visible_even_if_all_bytes_are_published(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        self.capture()
        self.commit()
        result = self.audit(datetime(2026, 10, 18, 1, tzinfo=JST))
        self.assertEqual(result['counts'], {'published': 1})
        self.assertFalse(result['sameDayAsInspection'])
        self.assertTrue(result['needsAttention'])
        self.assertTrue(any('開催日' in x for x in result['issues']))

    def test_hash_missing_invalid_json_and_nonfinite_are_not_publishable(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        box = self.capture()
        digest = next(iter(box.state['pending'].values()))
        blob = self.folder / 'blobs' / digest
        blob.write_text('corrupted')
        self.assertEqual(self.audit()['counts'], {'invalid': 1})
        blob.unlink()
        self.assertTrue(self.audit()['archiveErrors'])
        for content in (b'{broken', b'{"bad": NaN}', b'null'):
            digest = hashlib.sha256(content).hexdigest()
            blob = self.folder / 'blobs' / digest
            blob.write_bytes(content)
            path = f'reports/{DATE8}/one.json'
            state = box.state.copy()
            state.update(pending={path: digest}, observed={path: digest})
            (self.folder / 'state.json').write_text(json.dumps(state))
            self.assertEqual(self.audit()['counts'], {'invalid': 1})

    def test_unsafe_manifest_wrong_role_invalid_date_and_digest_are_rejected(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        box = self.capture()
        initial = json.loads((self.folder / 'state.json').read_text())
        for change in ({'date8': '20260230'}, {'sourceCommit': 'HEAD'}, {'schemaVersion': True},
                       {'role': 'health', 'pending': {'RaceData.json': 'a' * 64}},
                       {'pending': {'../outside.json': 'a' * 64}},
                       {'pending': {f'reports/{DATE8}/one.json': 'b' * 64}},
                       {'observed': []}):
            state = {**initial, **change}
            (self.folder / 'state.json').write_text(json.dumps(state))
            with self.assertRaises(InspectionError):
                self.audit()

    def test_symlink_blobs_and_manifest_are_rejected(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        box = self.capture()
        digest = next(iter(box.state['pending'].values()))
        blob = self.folder / 'blobs' / digest
        target = self.folder / 'target'
        blob.rename(target)
        blob.symlink_to(target)
        self.assertEqual(self.audit()['counts'], {'invalid': 1})
        state = self.folder / 'state.json'
        target = self.folder / 'manifest'
        state.rename(target)
        state.symlink_to(target)
        with self.assertRaises(InspectionError):
            self.audit()

    def test_unknown_source_and_capture_failure_are_explicit(self):
        self.write(f'reports/{DATE8}/one.json', {'fixture': True})
        box = self.capture()
        box.state.update(sourceCommit='a' * 40, captureErrors=['capture_failed'])
        box.save()
        result = self.audit()
        self.assertFalse(result['sourceIsAncestor'])
        self.assertEqual(len(result['issues']), 2)

    def test_health_recovery_preserves_real_rows_and_matches_markdown(self):
        from monitor_health_runtime import apply_health, health_from_event
        from pace_recording_display import markdown
        from test_monitor_health import baseline, event
        active = health_from_event(event(status='in_progress', conclusion=None), [], NOW)
        complete = health_from_event(event(conclusion='success'), [], NOW)
        row = {'raceID': '202605040601', 'raceDate': '2026-10-17', 'venue': '東京',
               'raceNumber': 1, 'startTime': '10:00', 'recordingState': 'saved',
               'recordingLabel': '予測保存済み', 'resultLabel': '結果待ち',
               'reason': '保存済み', 'issues': []}
        old = apply_health(baseline(), active)
        self.write('PaceRecordingStatus.json', old)
        self.write('reports/PaceRecordingStatus.md', markdown(old))
        box = Outbox(self.root, self.folder, DATE8, role='health')
        box.capture()
        current = apply_health(baseline([row]), complete)
        self.write('PaceRecordingStatus.json', current)
        self.write('reports/PaceRecordingStatus.md', markdown(current))
        self.commit()
        before = self.fingerprint()
        result = self.audit()
        self.assertEqual(result['counts'], {'represented': 2})
        self.assertFalse(result['needsAttention'])
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(json.loads((self.root / 'PaceRecordingStatus.json').read_text())['races'], [row])

    def test_late_active_journal_is_already_represented_by_terminal(self):
        from monitor_health_runtime import health_from_event
        from test_monitor_health import event
        active = health_from_event(event(status='in_progress', conclusion=None), [], NOW)
        complete = health_from_event(event(conclusion='success'), [], NOW)
        path = f'reports/{DATE8}/monitor_sessions/123.json'
        self.write(path, active)
        box = Outbox(self.root, self.folder, DATE8, role='health')
        box.capture()
        self.write(path, complete)
        self.commit()
        self.assertEqual(self.audit()['counts'], {'represented': 1})

    def test_read_only_git_rejects_mutations_and_cli_fails_cleanly(self):
        with self.assertRaises(InspectionError):
            ReadOnlyGit(self.root).run('fetch', 'origin', 'main')
        before = self.fingerprint()
        script = Path(__file__).with_name('inspect_live_outbox.py')
        result = subprocess.run([sys.executable, str(script), '--outbox', str(self.folder),
                                 '--repo', str(self.root), '--ref', 'main'], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['mode'], 'read_only')
        self.assertEqual(before, self.fingerprint())


if __name__ == '__main__':
    unittest.main()
