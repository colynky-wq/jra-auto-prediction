"""Workflow-level health without inventing race observations or predictions."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

JST = timezone(timedelta(hours=9))
REPOSITORY = 'colynky-wq/jra-auto-prediction'
WORKFLOW = 'KeibaAI Live Permanent'
MONITOR_STEP = 'Monitor races and publish one consistent bundle'
DAY_STEP = 'Check whether JRA races exist today'
STATES = {
    'active': ('自動監視を実行中', '開催確認・予測保存・公開を自動処理しています。', False),
    'completed': ('自動監視が終了', 'この監視処理は正常に終了しました。レース別の記録状況は一覧で確認できます。', False),
    'no_races': ('対象の開催なし', 'この監視日の開催一覧に対象レースがありません。', False),
    'startup_failed': ('自動監視の開始に失敗', '開催確認または開始準備を完了できませんでした。開催なしと判断した状態ではありません。', True),
    'failed': ('自動監視・公開が未完了', '監視または公開を完了できませんでした。未送信データがある場合は復旧用の成果物に保持します。', True),
    'interrupted': ('自動監視が中断', '監視処理が途中で停止しました。保存・公開できた内容はレース別の一覧で確認してください。', True),
}


def aware(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('aware timestamp required')
    return parsed.astimezone(JST)


def validate_event(event):
    run = event['workflow_run']
    if (event['repository']['full_name'] != REPOSITORY or run['name'] != WORKFLOW
            or run['head_repository']['full_name'] != REPOSITORY or run['head_branch'] != 'main'
            or run['event'] not in {'schedule', 'workflow_dispatch'}):
        raise ValueError('untrusted workflow identity')
    if not isinstance(run['id'], int) or isinstance(run['id'], bool) or run['id'] < 1:
        raise ValueError('invalid workflow id')
    return run


def health_from_event(event, jobs, now):
    run = validate_event(event)
    started = aware(run.get('run_started_at') or run['created_at'])
    if jobs:
        starts = [aware(j['started_at']) for j in jobs if j.get('started_at')]
        if starts:
            started = min(starts)
    conclusion = run.get('conclusion')
    terminal = run['status'] == 'completed'
    steps = [s for job in jobs for s in job.get('steps', [])]
    if not terminal and jobs and all(j['status'] == 'completed' for j in jobs):
        terminal = True
        conclusion = 'success' if all(j.get('conclusion') == 'success' for j in jobs) else 'failure'
    if not terminal:
        status = 'active'
    elif conclusion == 'cancelled':
        status = 'interrupted'
    elif conclusion == 'success':
        monitor = next((s for s in steps if s['name'] == MONITOR_STEP), None)
        day = next((s for s in steps if s['name'] == DAY_STEP), None)
        status = 'no_races' if monitor and monitor.get('conclusion') == 'skipped' and day and day.get('conclusion') == 'success' else 'completed'
    else:
        monitor = next((s for s in steps if s['name'] == MONITOR_STEP), None)
        status = 'startup_failed' if steps and (monitor is None or monitor.get('conclusion') == 'skipped') else 'failed'
    label, message, attention = STATES[status]
    return {'schemaVersion': 1, 'raceDate': started.date().isoformat(), 'runID': run['id'],
            'status': status, 'label': label, 'message': message, 'needsAttention': attention,
            'phaseRank': 2 if terminal else 1,
            'runStartedAtJST': started.strftime('%Y-%m-%d %H:%M:%S'),
            'observedAtJST': now.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S'),
            'detailsURL': f'https://github.com/{REPOSITORY}/actions/runs/{run["id"]}'}


def validated(health):
    if health is None:
        return None
    if (type(health['schemaVersion']) is not int or health['schemaVersion'] != 1 or health['status'] not in STATES
            or not isinstance(health['runID'], int) or isinstance(health['runID'], bool)
            or health['runID'] < 1 or type(health['phaseRank']) is not int or health['phaseRank'] not in {1, 2}
            or health['needsAttention'] is not STATES[health['status']][2]
            or health['label'] != STATES[health['status']][0]
            or health['message'] != STATES[health['status']][1]
            or health['detailsURL'] != f'https://github.com/{REPOSITORY}/actions/runs/{health["runID"]}'):
        raise ValueError('invalid monitor health')
    started = datetime.strptime(health['runStartedAtJST'], '%Y-%m-%d %H:%M:%S')
    datetime.strptime(health['observedAtJST'], '%Y-%m-%d %H:%M:%S')
    if started.date().isoformat() != health['raceDate']:
        raise ValueError('monitor health date mismatch')
    if (health['status'] == 'active') != (health['phaseRank'] == 1):
        raise ValueError('monitor health phase mismatch')
    return health


def choose_health(*candidates):
    safe = [validated(h) for h in candidates if h is not None]
    if not safe:
        return None
    sessions = {}
    for health in safe:
        previous = sessions.get(health['runID'])
        if previous is None or (health['phaseRank'], health['observedAtJST']) > (previous['phaseRank'], previous['observedAtJST']):
            sessions[health['runID']] = health
    return max(sessions.values(), key=lambda h: (h['runStartedAtJST'], h['runID']))


def load_health(reports, today, start='2026-10-13'):
    found, warnings = [], []
    for path in sorted(Path(reports).glob('*/monitor_sessions/*.json')):
        try:
            day = datetime.strptime(path.parent.parent.name, '%Y%m%d').date().isoformat()
        except ValueError:
            continue
        if not start <= day <= today:
            continue
        try:
            health = validated(json.loads(path.read_text()))
            if health['raceDate'] != day or path.stem != str(health['runID']):
                raise ValueError('monitor journal identity mismatch')
            found.append(health)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            warnings.append(f'{day}：自動監視の状態記録を一部読み取れません。')
    return choose_health(*found), warnings


def apply_health(data, health):
    """Global warning is separate from the count of real race-level failures."""
    health = validated(health)
    result = dict(data)
    previous = set(data.get('monitorWarnings', []))
    warnings = [w for w in data.get('warnings', []) if w not in previous]
    monitor_warnings = []
    if health and health['needsAttention']:
        monitor_warnings = [f'{health["raceDate"]}：{health["label"]}。{health["message"]}']
    result.update(monitoring=health, monitorWarnings=monitor_warnings,
                  warnings=sorted(set(warnings+monitor_warnings)))
    if not result['races']:
        result['status'] = 'recording_attention_required' if result['warnings'] else 'awaiting_first_schedule'
        if health and health['needsAttention']:
            result['emptyMessage'] = health['message']+' レース別の記録が未公開のため、対象件数は確認できていません。'
        elif not result['warnings'] and health and health['status'] in {'active', 'no_races'}:
            result['emptyMessage'] = health['message']
        elif previous and not result['warnings']:
            result['emptyMessage'] = '研究記録の対象となる開催予定はまだ保存されていません。次の自動監視で一覧を作成します。'
    return result
