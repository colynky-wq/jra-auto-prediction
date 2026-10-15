"""Durable live outbox; publishing never checks out or rebases the monitor's files."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import time

JST = timezone(timedelta(hours=9))
ROOT_FILES = ('RaceData.json', 'SignalData.json', 'WideOddsData.json',
              'AppResearchData.json', 'PaceRecordingStatus.json', 'reports/PaceRecordingStatus.md')
BUILDERS = ('production/build_pace_recording_status.py', 'production/repair_wide_odds.py',
            'production/build_app_research_data.py')


class PublishError(Exception):
    pass


def decode_json(data):
    def invalid(value):
        raise ValueError('non-finite JSON value')
    return json.loads(data, parse_constant=invalid)


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name+'.', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Git:
    def __init__(self, root):
        self.root = Path(root)

    def run(self, *args, env=None, check=True):
        try:
            result = subprocess.run(['git', *args], cwd=self.root,
                                    env={**os.environ, **(env or {})}, capture_output=True, timeout=25)
        except subprocess.TimeoutExpired:
            raise PublishError(f'git {args[0]} timed out') from None
        if check and result.returncode:
            # Do not copy authentication headers or credential-bearing URLs into diagnostics.
            raise PublishError(f'git {args[0]} failed (exit {result.returncode})')
        return result


class Outbox:
    def __init__(self, root, folder, date8, git=None, today=None, role='live'):
        self.root, self.folder = Path(root).resolve(), Path(folder).resolve()
        self.date8 = date8
        if role not in {'live', 'health'}:
            raise ValueError('invalid publisher role')
        self.role = role
        self.day = datetime.strptime(date8, '%Y%m%d').date().isoformat()
        if self.folder.is_relative_to(self.root):
            raise ValueError('outbox must be outside the monitor checkout')
        self.git = git or Git(self.root)
        self.today = today or (lambda: datetime.now(JST).date().isoformat())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state_path = self.folder/'state.json'
        self.errors = []
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if (self.state.get('schemaVersion') != 1 or self.state.get('date8') != date8
                    or self.state.get('role', 'live') != role):
                raise ValueError('outbox identity mismatch')
        else:
            self.state = {'schemaVersion': 1, 'date8': date8, 'role': role,
                          'sourceCommit': self.git.run('rev-parse', 'HEAD').stdout.decode().strip(),
                          'observed': {}, 'pending': {}, 'lastPublishedSHA': None, 'lastError': None}
        for entries in (self.state['observed'], self.state['pending']):
            for path, digest in entries.items():
                if not self.allowed(path) or not re.fullmatch('[a-f0-9]{64}', digest):
                    raise ValueError('invalid outbox entry')

    def allowed(self, path):
        parts = PurePosixPath(path).parts
        if not parts or path != PurePosixPath(path).as_posix() or '..' in parts or path.startswith('/'):
            return False
        if any(part.startswith('.') for part in parts):
            return False
        if path.endswith(('.tmp', '.lock')):
            return False
        if self.role == 'health':
            return path in {'PaceRecordingStatus.json', 'reports/PaceRecordingStatus.md'} or path.startswith(f'reports/{self.date8}/monitor_sessions/')
        return path in ROOT_FILES or path.startswith(f'reports/{self.date8}/')

    def save(self):
        atomic(self.state_path, (json.dumps(self.state, ensure_ascii=False, indent=2)+'\n').encode())

    def capture(self):
        """Freeze changed bytes first, then replace the durable pending manifest."""
        paths = [*ROOT_FILES, f'reports/{self.date8}']
        changed = self.git.run('diff', '--name-only', '-z', 'HEAD', '--', *paths).stdout
        new = self.git.run('ls-files', '--others', '--exclude-standard', '-z', '--', *paths).stdout
        names = sorted(set((changed+new).decode().strip('\0').split('\0')) - {''})
        self.errors = []
        observed, pending = dict(self.state['observed']), dict(self.state['pending'])
        frozen = {}
        stamps = {}
        for name in names:
            if not self.allowed(name):
                continue
            path = self.root/name
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                    raise ValueError('unsafe source path')
                before = path.stat()
                data = path.read_bytes()
                after = path.stat()
                stamp = (before.st_ino, before.st_size, before.st_mtime_ns)
                if stamp != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('source changed while reading')
                if name.endswith('.json'):
                    payload = decode_json(data)
                    if not isinstance(payload, (dict, list)):
                        raise ValueError('JSON container required')
                digest = hashlib.sha256(data).hexdigest()
                if observed.get(name) != digest:
                    frozen[name] = (digest, data)
                    stamps[name] = stamp
            except (OSError, ValueError, UnicodeError):
                self.errors.append(name)
        # Do not combine bytes from different writes of the same capture.
        for name, stamp in stamps.items():
            try:
                stat = (self.root/name).stat()
                if stamp != (stat.st_ino, stat.st_size, stat.st_mtime_ns):
                    raise PublishError('bundle changed during capture')
            except OSError:
                raise PublishError('bundle changed during capture') from None
        for name, (digest, data) in frozen.items():
            destination = self.folder/'blobs'/digest
            if not destination.exists():
                atomic(destination, data)
            observed[name] = digest
            pending[name] = digest
        self.state.update(observed=observed, pending=pending, captureErrors=self.errors)
        self.save()

    def remote_bytes(self, base, path):
        exists = self.git.run('ls-tree', '-z', base, '--', path).stdout
        return self.git.run('show', f'{base}:{path}').stdout if exists else None

    def bytes_for_remote(self, path, data, remote):
        if path == 'PaceRecordingStatus.json':
            from monitor_health_runtime import apply_health, choose_health
            local = json.loads(data)
            prior = json.loads(remote) if remote is not None else None
            health = choose_health(local.get('monitoring'), prior.get('monitoring') if prior else None)
            source = prior if self.role == 'health' and prior is not None else local
            merged = apply_health(source, health)
            if self.role == 'health':
                merged['generatedAtJST'] = local['generatedAtJST']
            return (json.dumps(merged, ensure_ascii=False, indent=2)+'\n').encode()
        if path.startswith(f'reports/{self.date8}/monitor_sessions/') and path.endswith('.json'):
            from monitor_health_runtime import choose_health
            health = choose_health(json.loads(data), json.loads(remote) if remote is not None else None)
            if Path(path).stem != str(health['runID']) or health['raceDate'] != self.day:
                raise PublishError('monitor journal identity mismatch')
            return (json.dumps(health, ensure_ascii=False, indent=2)+'\n').encode()
        immutable = ('/prerace/' in path and not path.endswith('/index.json')) or path.endswith('/PaceRecordingSchedule.json') or '/wide_axis_v1/' in path or '/triple_v1/' in path
        if immutable and remote is not None and remote != data:
            raise PublishError(f'既存の発走前記録と競合しています: {path}')
        if '/prerace/' in path and path.endswith('/index.json') and remote is not None and remote != data:
            local, prior = json.loads(data), json.loads(remote)
            if local['raceID'] != prior['raceID']:
                raise PublishError('snapshot index identity mismatch')
            entries = {}
            for entry in prior['snapshots']+local['snapshots']:
                key = entry['file']
                if key in entries and entries[key] != entry:
                    raise PublishError('snapshot index entry mismatch')
                entries[key] = entry
            local['snapshots'] = sorted(entries.values(), key=lambda e: (e['capturedAtJST'], e['file']))
            if local['snapshots']:
                local['latestFingerprint'] = local['snapshots'][-1]['fingerprint']
            return (json.dumps(local, ensure_ascii=False, indent=2)+'\n').encode()
        if path == 'RaceData.json':
            races = json.loads(data)
            if not races or any(r.get('raceDate') != self.day for r in races):
                raise PublishError('live prediction day mismatch')
            if remote is not None:
                old = json.loads(remote)
                if any(r.get('raceDate', '') > self.day for r in old):
                    raise PublishError('新しい開催日の予測を古いデータで上書きできません。')
                combined = {r['raceID']: r for r in old if r.get('raceDate') == self.day}
                combined.update({r['raceID']: r for r in races})
                races = sorted(combined.values(), key=lambda r: (r.get('startTime', ''), r['raceID']))
            return (json.dumps(races, ensure_ascii=False, indent=2)+'\n').encode()
        return data

    def publish(self):
        if not self.state['pending']:
            return True
        try:
            if self.today() != self.day:
                raise PublishError('開催日が変わったため、未送信データを保持して停止します。')
            self.git.run('fetch', '--no-tags', 'origin', 'main')
            base = self.git.run('rev-parse', 'FETCH_HEAD').stdout.decode().strip()
            # A separate index builds the commit without touching the monitor's HEAD,
            # index, source files, models, or concurrently written predictions.
            with tempfile.TemporaryDirectory(dir=self.folder) as temporary:
                env = {'GIT_INDEX_FILE': str(Path(temporary)/'index')}
                self.git.run('read-tree', base, env=env)
                remaining, issues = {}, []
                prepared_status = None
                names = sorted(self.state['pending'], key=lambda name: (name != 'PaceRecordingStatus.json', name))
                for path in names:
                    digest = self.state['pending'][path]
                    try:
                        blob = self.folder/'blobs'/digest
                        data = blob.read_bytes()
                        if hashlib.sha256(data).hexdigest() != digest:
                            raise PublishError('outbox content hash mismatch')
                        if path == 'reports/PaceRecordingStatus.md':
                            from pace_recording_display import markdown
                            status = prepared_status or self.remote_bytes(base, 'PaceRecordingStatus.json')
                            if status is None:
                                raise PublishError('recording status unavailable for report')
                            data = markdown(json.loads(status)).encode()
                        else:
                            data = self.bytes_for_remote(path, data, self.remote_bytes(base, path))
                        if path == 'PaceRecordingStatus.json':
                            prepared_status = data
                    except (PublishError, OSError, ValueError, KeyError, TypeError) as exc:
                        remaining[path] = digest
                        issues.append(str(exc) if isinstance(exc, PublishError) else f'{path}: {type(exc).__name__}')
                        continue
                    prepared = Path(temporary)/'payload'
                    prepared.write_bytes(data)
                    oid = self.git.run('hash-object', '-w', str(prepared)).stdout.decode().strip()
                    self.git.run('update-index', '--add', '--cacheinfo', f'100644,{oid},{path}', env=env)
                tree = self.git.run('write-tree', env=env).stdout.decode().strip()
                original = self.git.run('rev-parse', base+'^{tree}').stdout.decode().strip()
                if tree == original:
                    commit = base  # A previous push succeeded but its response was lost.
                else:
                    commit = self.git.run('commit-tree', tree, '-p', base, '-m',
                                          f'Publish KeibaAI live prediction bundle {self.date8}').stdout.decode().strip()
                    self.git.run('push', 'origin', f'{commit}:refs/heads/main')
            self.state.update(pending=remaining, lastPublishedSHA=commit, lastError='; '.join(issues) or None)
            self.save()
            keep = set(self.state['observed'].values())
            for blob in (self.folder/'blobs').glob('*'):
                if blob.name not in keep:
                    blob.unlink()
            print('LIVE_BUNDLE_PUBLISHED', commit, 'pending', len(remaining), flush=True)
            if remaining:
                print('::warning::競合した記録は未送信のまま保持します。', self.state['lastError'], flush=True)
            return not remaining
        except (PublishError, OSError, ValueError, KeyError, TypeError) as exc:
            self.state['lastError'] = str(exc) if isinstance(exc, PublishError) else type(exc).__name__
            try:
                self.save()
            except OSError:
                pass
            print('::warning::公開を再試行します。監視は継続し、未送信データを保持します。',
                  self.state['lastError'], flush=True)
            return False


def run_session(root, outbox, command, poll_seconds=20, builders=BUILDERS, final_attempts=3, retry_pause=2):
    monitor = subprocess.Popen(command, cwd=root)
    interrupted = False

    def capture():
        for builder in builders:
            try:
                result = subprocess.run([sys.executable, builder], cwd=root, timeout=90)
                if result.returncode:
                    print('::warning::補助表示の更新に失敗しました。監視は継続します。', builder, flush=True)
            except (OSError, subprocess.TimeoutExpired):
                print('::warning::補助表示の更新を完了できませんでした。', builder, flush=True)
        try:
            outbox.capture()
        except (PublishError, OSError):
            outbox.errors = ['capture_failed']
            print('::warning::公開用の保存を再試行します。監視は継続します。', flush=True)

    try:
        while monitor.poll() is None:
            capture()
            outbox.publish()
            time.sleep(max(0.01, poll_seconds))
        monitor_exit = monitor.wait()
        capture()
        for attempt in range(final_attempts):
            if outbox.publish():
                break
            if attempt+1 < final_attempts:
                time.sleep(retry_pause)
        if outbox.state['pending'] or outbox.errors:
            print('::error::公開を完了できませんでした。未送信データを成果物に保持します。', flush=True)
            return monitor_exit or 1
        return monitor_exit
    except KeyboardInterrupt:
        interrupted = True
        return 130
    finally:
        if monitor.poll() is None:
            monitor.terminate()
            try:
                monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                monitor.kill()
                monitor.wait()
        if interrupted:
            try:
                # Freeze already-produced bytes without network access or another forecast.
                outbox.capture()
            except (PublishError, OSError):
                print('::warning::停止直前の保存を完了できませんでした。既存の未送信データを保持します。', flush=True)
            print('監視を停止しました。保存済みの未送信データは保持しています。', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--outbox', required=True)
    parser.add_argument('--poll-seconds', type=float, default=20)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('monitor command required')
    root = Path(__file__).resolve().parent.parent
    outbox = Outbox(root, args.outbox, args.date)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(run_session(root, outbox, command, args.poll_seconds))


if __name__ == '__main__':
    main()
