"""Read-only inspection of a retained live/health outbox against a pinned Git tree.

No fetch, push, checkout, capture, builders, forecasts, or manifest updates occur.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from live_bundle_publisher import Git, JST, Outbox, PublishError, decode_json
from pace_recording_display import markdown


class InspectionError(ValueError):
    pass


class ReadOnlyGit(Git):
    def run(self, *args, **kwargs):
        if args[0] not in {'rev-parse', 'cat-file', 'merge-base', 'ls-tree', 'show'}:
            raise InspectionError('点検ではGitの読み取り操作だけを許可します。')
        kwargs['env'] = {'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0'}
        return super().run(*args, **kwargs)


def read_file(path, root):
    """Never follow a symlink out of, or within, the supplied artifact directory."""
    relative = path.relative_to(root)
    if root.is_symlink() or any((root / Path(*relative.parts[:i])).is_symlink()
                                for i in range(1, len(relative.parts) + 1)):
        raise InspectionError('成果物にシンボリックリンクがあります。')
    if not path.resolve().is_relative_to(root.resolve()):
        raise InspectionError('成果物の範囲外を参照しています。')
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError:
        raise InspectionError('成果物のファイルを読み取れません。') from None
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise InspectionError('読み取り中に成果物が変更されました。')
    return data


def load_manifest(folder):
    data = read_file(folder / 'state.json', folder)
    try:
        state = decode_json(data)
        if not isinstance(state, dict) or type(state.get('schemaVersion')) is not int or state['schemaVersion'] != 1:
            raise ValueError()
        date8 = state['date8']
        if not isinstance(date8, str) or not re.fullmatch(r'\d{8}', date8):
            raise ValueError()
        day = datetime.strptime(date8, '%Y%m%d').date().isoformat()
        role = state.get('role', 'live')
        if role not in {'live', 'health'} or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', state['sourceCommit']):
            raise ValueError()
        # Only use the publisher's pure policy methods; its constructor writes a directory.
        policy = object.__new__(Outbox)
        policy.date8, policy.day, policy.role = date8, day, role
        for name in ('observed', 'pending'):
            if not isinstance(state[name], dict):
                raise ValueError()
            for path, digest in state[name].items():
                if not policy.allowed(path) or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                    raise ValueError()
        if any(state['observed'].get(path) != digest for path, digest in state['pending'].items()):
            raise ValueError()
        errors = state.get('captureErrors', [])
        if not isinstance(errors, list) or any(not isinstance(e, str) for e in errors):
            raise ValueError()
        return state, policy
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise InspectionError('state.jsonの形式・日付・対象パス・指紋が不正です。') from None


def same_content(path, first, second):
    if first == second:
        return True
    if first is None or second is None or not path.endswith('.json'):
        return False
    # Formatting alone does not make a forecast/record absent. Keep scalar types.
    canonical = lambda data: json.dumps(decode_json(data), sort_keys=True, ensure_ascii=False,
                                       separators=(',', ':'), allow_nan=False)
    return canonical(first) == canonical(second)


def inspect(folder, repository, ref='origin/main', now=None):
    """Return evidence for review; never publish or claim that a replay is approved."""
    folder, repository = Path(folder).absolute(), Path(repository).absolute()
    state, policy = load_manifest(folder)
    git = ReadOnlyGit(repository)
    try:
        compared = git.run('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').stdout.decode().strip()
        # Pin the commit before any per-file reads. No ref changes can mix snapshots.
        source = state['sourceCommit']
        source_exists = git.run('cat-file', '-e', source + '^{commit}', check=False).returncode == 0
        ancestor = source_exists and git.run('merge-base', '--is-ancestor', source, compared, check=False).returncode == 0
    except PublishError:
        raise InspectionError('比較先のGitコミットを確認できません。') from None
    blobs, blob_errors = {}, {}
    for digest in set(state['observed'].values()):
        try:
            data = read_file(folder / 'blobs' / digest, folder)
            if hashlib.sha256(data).hexdigest() != digest:
                raise InspectionError('保存内容の指紋が一致しません。')
            blobs[digest] = data
        except InspectionError as exc:
            blob_errors[digest] = str(exc)

    def remote(path):
        exists = git.run('ls-tree', '-z', compared, '--', path).stdout
        return git.run('show', compared + ':' + path).stdout if exists else None

    entries, prepared_status = [], None
    for path in sorted(state['pending'], key=lambda p: (p != 'PaceRecordingStatus.json', p)):
        digest = state['pending'][path]
        entry = {'path': path, 'sha256': digest, 'state': None, 'message': None}
        try:
            if digest in blob_errors:
                raise InspectionError(blob_errors[digest])
            data = blobs[digest]
            if path.endswith('.json') and not isinstance(decode_json(data), (dict, list)):
                raise InspectionError('JSONの内容が辞書・配列ではありません。')
            prior = remote(path)
            if path == 'reports/PaceRecordingStatus.md':
                payload = prepared_status if prepared_status is not None else remote('PaceRecordingStatus.json')
                if payload is None:
                    raise InspectionError('説明文に対応する一覧JSONを確認できません。')
                candidate = markdown(decode_json(payload)).encode()
            else:
                candidate = policy.bytes_for_remote(path, data, prior)
            if path == 'PaceRecordingStatus.json':
                prepared_status = candidate
            if prior == data:
                entry.update(state='published', message='保存時の内容が公開先にあります。')
            elif same_content(path, prior, candidate):
                entry.update(state='represented', message='合流後の内容が公開先に含まれています。')
            elif prior is None:
                entry.update(state='unpublished', message='比較先にこのファイルがありません。')
            else:
                entry.update(state='review_required', message='公開先と内容が異なります。差分の確認が必要です。')
        except PublishError as exc:
            entry.update(state='conflict', message=str(exc))
        except (InspectionError, OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            entry.update(state='invalid', message=str(exc) if isinstance(exc, InspectionError) else '保存内容または比較先の形式を確認できません。')
        entries.append(entry)
    today = (now or datetime.now(JST)).astimezone(JST).date().isoformat()
    issues = []
    if not source_exists:
        issues.append('保存時のソースコミットを比較用リポジトリで確認できません。')
    elif not ancestor:
        issues.append('保存時のソースが比較先の履歴に含まれていません。')
    if today != policy.day:
        issues.append('開催日が過ぎた／未到来のため、通常の自動再送は使えません。')
    if state.get('captureErrors'):
        issues.append('保存時に取り込めなかったファイルがあります。キューだけでは全件を確認できません。')
    if blob_errors:
        issues.append('保管された内容の一部に欠落・指紋不一致があります。')
    counts = dict(Counter(e['state'] for e in entries))
    return {
        'schemaVersion': 1, 'mode': 'read_only', 'raceDate': policy.day, 'role': policy.role,
        'sourceCommit': state['sourceCommit'], 'comparedCommit': compared,
        'sourceIsAncestor': bool(ancestor), 'sameDayAsInspection': today == policy.day,
        'pendingFiles': len(entries), 'counts': counts, 'entries': entries,
        'captureErrors': state.get('captureErrors', []), 'archiveErrors': blob_errors,
        'issues': issues, 'needsAttention': bool(issues or any(e['state'] not in {'published', 'represented'} for e in entries)),
        'nextAction': 'この結果は点検のみです。再送・復旧前に差分と元の実行を確認し、予測や観測を作り直さないでください。',
    }


def main():
    parser = argparse.ArgumentParser(description='未送信成果物の読み取り点検。公開や予測処理は実行しません。')
    parser.add_argument('--outbox', required=True, help='展開済みのstate.jsonとblobsがあるフォルダ')
    parser.add_argument('--repo', required=True, help='公開先の履歴を取得済みのGitリポジトリ')
    parser.add_argument('--ref', default='origin/main', help='比較する取得済み参照。結果には固定コミットを記録')
    args = parser.parse_args()
    try:
        report = inspect(args.outbox, args.repo, args.ref)
    except (InspectionError, PublishError) as exc:
        print(json.dumps({'mode': 'read_only', 'needsAttention': True, 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['needsAttention'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
