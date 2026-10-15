import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from live_bundle_publisher import Git, Outbox, PublishError, run_session

DAY = '2026-10-17'
DATE8 = '20261017'


def command(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout.decode().strip()


class ControlledGit(Git):
    def __init__(self, root, failures=None, lose_reply=False, before_push=None):
        super().__init__(root)
        self.failures = dict(failures or {})
        self.lose_reply = lose_reply
        self.before_push = before_push

    def run(self, *args, **kwargs):
        if self.failures.get(args[0], 0):
            self.failures[args[0]] -= 1
            raise PublishError(f'git {args[0]} failed (simulated connection failure)')
        if args[0] == 'push' and self.before_push:
            callback, self.before_push = self.before_push, None
            callback()
        result = super().run(*args, **kwargs)
        if args[0] == 'push' and self.lose_reply:
            self.lose_reply = False
            raise PublishError('git push timed out after remote accepted the commit')
        return result


class TestLiveBundlePublisher(unittest.TestCase):
    def test_research_role_only_publishes_own_reports_and_preserves_app_files(self):
        path='reports/20261017/win5_v1/session.json'
        self.write(path,{'frozen':1})
        self.write('reports/20261017/win5_status.json',{'status':'ready'})
        self.write('RaceData.json',[self.race()])
        publisher=Outbox(self.root,self.outbox,DATE8,today=lambda:DAY,role='research')
        self.assertFalse(publisher.allowed('RaceData.json'))
        self.assertFalse(publisher.allowed('reports/20261017/ResultData.json'))
        publisher.capture();self.assertTrue(publisher.publish())
        self.assertEqual(json.loads(self.remote_read('RaceData.json'))[0]['raceDate'],'2026-10-16')
        self.assertEqual(json.loads(self.remote_read(path)),{'frozen':1})
        self.write(path,{'frozen':2});publisher.capture()
        self.assertFalse(publisher.publish())
        self.assertEqual(json.loads(self.remote_read(path)),{'frozen':1})

    def test_added_pool_first_capture_conflict_does_not_block_prediction(self):
        path='reports/20261017/all_bet_v1/place/202605040601.json'
        self.write(path,{'frozen':1});publisher=self.publisher();publisher.capture()
        self.assertTrue(publisher.publish())
        self.write(path,{'frozen':2});self.write('RaceData.json',[self.race()]);publisher.capture()
        self.assertFalse(publisher.publish())
        self.assertEqual(json.loads(self.remote_read(path)),{'frozen':1})
        self.assertEqual(json.loads(self.remote_read('RaceData.json'))[0]['raceDate'],DAY)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)/'monitor'; self.root.mkdir()
        self.remote = Path(self.temporary.name)/'remote.git'
        self.outbox = Path(self.temporary.name)/'outbox'
        command(self.root, 'init', '-b', 'main')
        command(self.root, 'config', 'user.name', 'test')
        command(self.root, 'config', 'user.email', 'test@example.invalid')
        self.write('RaceData.json', [{'raceID': '202605040501', 'raceDate': '2026-10-16'}])
        self.write('production/model.txt', 'original model')
        self.write('peer.txt', 'original peer')
        command(self.root, 'add', '.')
        command(self.root, 'commit', '-m', 'initial')
        command(self.root, 'clone', '--bare', str(self.root), str(self.remote))
        command(self.root, 'remote', 'add', 'origin', str(self.remote))
        self.start_head = command(self.root, 'rev-parse', 'HEAD')
        self.start_index = (self.root/'.git/index').read_bytes()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, value):
        destination = self.root/path; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)

    def race(self, number=1, probability=.25):
        return {'raceID': f'2026050406{number:02}', 'raceDate': DAY, 'startTime': f'10:{number:02}',
                'horses': [{'name': '試験専用', 'aiProbability': probability}]}

    def publisher(self, git=None):
        return Outbox(self.root, self.outbox, DATE8, git=git, today=lambda: DAY)

    def remote_read(self, path):
        return command(self.remote, 'show', 'main:'+path)

    def advance_peer(self, path, value):
        folder = Path(self.temporary.name)/'peer'
        if folder.exists():
            command(folder, 'pull', '--ff-only')
        else:
            command(self.root, 'clone', str(self.remote), str(folder))
            command(folder, 'config', 'user.name', 'peer')
            command(folder, 'config', 'user.email', 'peer@example.invalid')
        destination = folder/path; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value)
        command(folder, 'add', '.')
        command(folder, 'commit', '-m', 'peer update')
        command(folder, 'push', 'origin', 'main')

    def test_push_failure_preserves_frozen_bytes_and_recovers_after_restart(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(ControlledGit(self.root, {'push': 1}))
        publisher.capture(); frozen = dict(publisher.state['pending'])
        self.assertFalse(publisher.publish())
        self.assertEqual(json.loads((self.outbox/'state.json').read_text())['pending'], frozen)
        self.write('RaceData.json', [self.race(probability=.5)])
        recovered = self.publisher()
        self.assertTrue(recovered.publish())
        self.assertEqual(json.loads(self.remote_read('RaceData.json'))[0]['horses'][0]['aiProbability'], .25)
        self.assertEqual(json.loads((self.root/'RaceData.json').read_text())[0]['horses'][0]['aiProbability'], .5)
        self.assertEqual(command(self.root, 'rev-parse', 'HEAD'), self.start_head)
        self.assertEqual((self.root/'.git/index').read_bytes(), self.start_index)

    def test_fetch_failure_keeps_pending_then_retries(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(ControlledGit(self.root, {'fetch': 1}))
        publisher.capture()
        self.assertFalse(publisher.publish()); self.assertTrue(publisher.state['pending'])
        self.assertTrue(publisher.publish()); self.assertFalse(publisher.state['pending'])

    def test_lost_push_response_is_confirmed_without_duplicate_commit(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(ControlledGit(self.root, lose_reply=True))
        publisher.capture(); self.assertFalse(publisher.publish())
        accepted = command(self.remote, 'rev-parse', 'main')
        self.assertTrue(publisher.publish())
        self.assertEqual(command(self.remote, 'rev-parse', 'main'), accepted)

    def test_concurrent_remote_update_is_preserved_on_retry(self):
        self.write('RaceData.json', [self.race()])
        git = ControlledGit(self.root, before_push=lambda: self.advance_peer('peer.txt', 'new peer'))
        publisher = self.publisher(git); publisher.capture()
        self.assertFalse(publisher.publish())
        self.assertTrue(publisher.publish())
        self.assertEqual(self.remote_read('peer.txt'), 'new peer')
        self.assertEqual((self.root/'peer.txt').read_text(), 'original peer')

    def test_newer_source_capture_includes_earlier_unsent_races(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(ControlledGit(self.root, {'push': 1}))
        publisher.capture(); self.assertFalse(publisher.publish())
        self.write('RaceData.json', [self.race(), self.race(2)])
        publisher.capture(); self.assertTrue(publisher.publish())
        self.assertEqual(len(json.loads(self.remote_read('RaceData.json'))), 2)

    def test_same_day_peer_race_is_retained(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture()
        self.advance_peer('RaceData.json', [self.race(2)])
        self.assertTrue(publisher.publish())
        self.assertEqual([r['raceID'] for r in json.loads(self.remote_read('RaceData.json'))],
                         [self.race()['raceID'], self.race(2)['raceID']])

    def test_existing_first_prediction_is_kept_while_healthy_files_publish(self):
        path = 'reports/20261017/prerace/202605040601/PacePrediction.json'
        self.advance_peer(path, {'first': 'remote'})
        self.write(path, {'first': 'local'})
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture()
        self.assertFalse(publisher.publish())
        self.assertEqual(json.loads(self.remote_read(path)), {'first': 'remote'})
        self.assertEqual(len(json.loads(self.remote_read('RaceData.json'))), 1)
        self.assertEqual(set(publisher.state['pending']), {path})

    def test_wide_axis_first_record_cannot_be_replaced(self):
        path = 'reports/20261017/wide_axis_v1/202605040601.json'
        self.write(path, {'raceID':'202605040601','axis':6})
        sender = self.publisher();sender.capture();self.assertTrue(sender.publish())
        self.write(path, {'raceID':'202605040601','axis':7})
        sender.capture();self.assertFalse(sender.publish())
        self.assertEqual(json.loads(self.remote_read(path))['axis'],6)

    def test_triple_first_record_cannot_be_replaced(self):
        path = 'reports/20261017/triple_v1/trifecta/202605040601.json'
        self.write(path, {'first':1})
        sender=self.publisher();sender.capture();self.assertTrue(sender.publish())
        self.write(path, {'first':2});self.write('RaceData.json',[self.race()])
        sender.capture();self.assertFalse(sender.publish())
        self.assertEqual(json.loads(self.remote_read(path)),{'first':1})
        self.assertEqual(len(json.loads(self.remote_read('RaceData.json'))),1)

    def test_partial_json_is_not_published_and_other_files_continue(self):
        self.write('SignalData.json', '{unfinished')
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture(); self.assertTrue(publisher.errors)
        self.assertTrue(publisher.publish())
        self.assertEqual(len(json.loads(self.remote_read('RaceData.json'))), 1)
        self.write('SignalData.json', {'complete': True})
        publisher.capture(); self.assertFalse(publisher.errors); self.assertTrue(publisher.publish())
        self.assertEqual(json.loads(self.remote_read('SignalData.json')), {'complete': True})

    def test_tampered_blob_stays_pending(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture()
        digest = publisher.state['pending']['RaceData.json']
        (self.outbox/'blobs'/digest).write_text('changed')
        self.assertFalse(publisher.publish()); self.assertTrue(publisher.state['pending'])
        self.assertEqual(command(self.remote, 'rev-parse', 'main'), self.start_head)

    def test_models_and_source_are_not_uploaded(self):
        self.write('production/model.txt', 'unrelated change')
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture(); self.assertTrue(publisher.publish())
        self.assertEqual(self.remote_read('production/model.txt'), 'original model')

    def test_expired_day_keeps_the_outbox(self):
        self.write('RaceData.json', [self.race()])
        publisher = self.publisher(); publisher.capture()
        publisher.today = lambda: '2026-10-18'
        self.assertFalse(publisher.publish()); self.assertTrue(publisher.state['pending'])
        self.assertEqual(command(self.remote, 'rev-parse', 'main'), self.start_head)

    def test_monitor_process_finishes_despite_persistent_publish_failure(self):
        publisher = self.publisher(ControlledGit(self.root, {'push': 100}))
        script = ('import json,time;from pathlib import Path;'
                  f'race={self.race()!r};'
                  'p=Path("RaceData.json");p.write_text(json.dumps([race]));time.sleep(.1);'
                  'Path("monitor-finished.txt").write_text("finished")')
        result = run_session(self.root, publisher, [sys.executable, '-c', script],
                             poll_seconds=.01, builders=(), final_attempts=2, retry_pause=0)
        self.assertEqual(result, 1)
        self.assertTrue((self.root/'monitor-finished.txt').exists())
        self.assertTrue(json.loads((self.outbox/'state.json').read_text())['pending'])

    def test_monitor_and_final_retry_recover(self):
        publisher = self.publisher(ControlledGit(self.root, {'push': 1}))
        script = f'from pathlib import Path;Path("RaceData.json").write_text({json.dumps([self.race()])!r})'
        result = run_session(self.root, publisher, [sys.executable, '-c', script],
                             poll_seconds=.01, builders=(), final_attempts=2, retry_pause=0)
        self.assertEqual(result, 0); self.assertFalse(publisher.state['pending'])

    def test_health_sender_keeps_remote_race_counts_and_cannot_upload_models(self):
        from monitor_health_runtime import health_from_event
        from test_monitor_health import baseline, event, NOW
        prior = baseline([{'raceID': '202605040601', 'recordingState': 'saved'}])
        self.advance_peer('PaceRecordingStatus.json', prior)
        health = health_from_event(event(), [], NOW)
        local = baseline(); local['monitoring'] = health
        self.write('PaceRecordingStatus.json', local)
        self.write('RaceData.json', [self.race()])
        publisher = Outbox(self.root, self.outbox, DATE8, today=lambda: DAY, role='health')
        publisher.capture(); self.assertTrue(publisher.publish())
        result = json.loads(self.remote_read('PaceRecordingStatus.json'))
        self.assertEqual(result['races'], prior['races'])
        self.assertTrue(result['monitoring']['needsAttention'])
        self.assertEqual(json.loads(self.remote_read('RaceData.json'))[0]['raceDate'], '2026-10-16')

    def test_live_status_refresh_does_not_erase_independent_monitor_failure(self):
        from monitor_health_runtime import apply_health, health_from_event
        from test_monitor_health import baseline, event, NOW
        health = health_from_event(event(), [], NOW)
        self.advance_peer('PaceRecordingStatus.json', apply_health(baseline(), health))
        self.write('PaceRecordingStatus.json', baseline([{'raceID': '202605040601', 'recordingState': 'saved'}]))
        publisher = self.publisher(); publisher.capture(); self.assertTrue(publisher.publish())
        result = json.loads(self.remote_read('PaceRecordingStatus.json'))
        self.assertEqual(len(result['races']), 1)
        self.assertEqual(result['monitoring']['runID'], 123)
        self.assertTrue(result['monitorWarnings'])

    def test_observation_index_merges_other_sessions_without_replacing_first_prediction(self):
        path = 'reports/20261017/prerace/202605040601/index.json'
        def index(filename, captured):
            return {'raceID': '202605040601', 'latestFingerprint': filename,
                    'snapshots': [{'file': filename+'.json', 'capturedAtJST': captured, 'fingerprint': filename}]}
        self.advance_peer(path, index('peer', '2026-10-17T09:40:00+09:00'))
        self.write(path, index('local', '2026-10-17T09:41:00+09:00'))
        publisher = self.publisher(); publisher.capture(); self.assertTrue(publisher.publish())
        saved = json.loads(self.remote_read(path))
        self.assertEqual(len(saved['snapshots']), 2)
        self.assertEqual(saved['latestFingerprint'], 'local')

    def test_health_report_uses_the_same_merged_rows_as_the_json(self):
        from monitor_health_runtime import health_from_event
        from test_monitor_health import baseline, event, NOW
        row = {'raceID': '202605040601', 'raceDate': DAY, 'venue': '東京', 'raceNumber': 1,
               'startTime': '10:00', 'recordingLabel': '予測保存済み', 'resultLabel': '結果待ち',
               'reason': '保存しました。', 'issues': []}
        prior = baseline([row]); prior['counts'].update(saved=1, needsAttention=0)
        self.advance_peer('PaceRecordingStatus.json', prior)
        health = health_from_event(event(), [], NOW)
        local = baseline(); local['monitoring'] = health
        self.write('PaceRecordingStatus.json', local)
        self.write('reports/PaceRecordingStatus.md', 'old local report')
        publisher = Outbox(self.root, self.outbox, DATE8, today=lambda: DAY, role='health')
        publisher.capture(); self.assertTrue(publisher.publish())
        report = self.remote_read('reports/PaceRecordingStatus.md')
        self.assertIn('東京1R', report)
        self.assertIn('自動監視・公開が未完了', report)

    def test_delayed_active_journal_cannot_replace_completed_remote_result(self):
        from monitor_health_runtime import health_from_event
        from test_monitor_health import event, NOW
        path = 'reports/20261017/monitor_sessions/123.json'
        complete = health_from_event(event(conclusion='success'), [], NOW)
        self.advance_peer(path, complete)
        active = health_from_event(event(status='in_progress', conclusion=None), [], NOW)
        active['observedAtJST'] = '2026-10-17 18:00:00'
        self.write(path, active)
        publisher = self.publisher(); publisher.capture(); self.assertTrue(publisher.publish())
        self.assertEqual(json.loads(self.remote_read(path))['status'], 'completed')

    def test_cancel_preserves_latest_already_produced_bytes_and_stops_child(self):
        class InterruptingOutbox(Outbox):
            def publish(inner):
                self.write('RaceData.json', [self.race(probability=.75)])
                raise KeyboardInterrupt
        self.write('RaceData.json', [self.race(probability=.25)])
        publisher = InterruptingOutbox(self.root, self.outbox, DATE8, today=lambda: DAY)
        result = run_session(self.root, publisher, [sys.executable, '-c', 'import time;time.sleep(60)'],
                             poll_seconds=.01, builders=(), final_attempts=1, retry_pause=0)
        self.assertEqual(result, 130)
        digest = publisher.state['pending']['RaceData.json']
        saved = json.loads((self.outbox/'blobs'/digest).read_text())
        self.assertEqual(saved[0]['horses'][0]['aiProbability'], .75)

    def test_nonfinite_json_is_kept_out_of_the_public_bundle(self):
        self.write('RaceData.json', '[{"raceDate":"2026-10-17","aiProbability":NaN}]')
        publisher = self.publisher(); publisher.capture()
        self.assertIn('RaceData.json', publisher.errors)
        self.assertNotIn('RaceData.json', publisher.state['pending'])


if __name__ == '__main__':
    unittest.main()
