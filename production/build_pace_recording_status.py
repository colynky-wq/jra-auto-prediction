"""Human-readable per-race recording status, including absent and damaged records."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import pandas as pd
from horse_profile_runtime import JRA_VENUE_MAP
from prerace_snapshot_runtime import JST
from evaluate_prospective_pace import START, check_prediction
from pace_recording_io import (
    atomic_text, aware_time, load_laps, load_models, race_day,
    read_object, recording_folders, validate_prediction,
)

ROOT = Path(__file__).resolve().parent.parent
LABELS = {'waiting': '記録待ち', 'saved': '予測保存済み', 'held': '発走前に保留',
          'missing': '記録なし', 'failed': '保存失敗', 'invalid': '記録不整合'}
HOLD = {'insufficient_style_history': '過去3走以上の脚質記録が足りません。',
        'insufficient_course_reference': '同じコース条件の基準件数が足りません。',
        'straight_race': '直線レースは対象外です。',
        'unknown_race_class': 'レースのクラスを確認できません。'}
FAILURES = {'capture_failed': '発走前の出馬表を保存できませんでした。',
            'prediction_failed': '研究予測の作成または保存に失敗しました。',
            'monitor_failed': '自動監視の処理が失敗し、研究予測が残っていません。'}
EVENT_STAGES = set(FAILURES) | {'monitor_missed', 'track_unavailable'}
READ_ERRORS = (KeyError, ValueError, TypeError, OSError, AttributeError, OverflowError)


def read_json(path):
    value, _, error = read_object(path)
    return value, error


def metadata(rid, day, complete=False):
    return {'raceID': rid, 'raceDate': day, 'venue': JRA_VENUE_MAP.get(rid[4:6], ''),
            'raceNumber': int(rid[-2:]), 'startTime': None,
            'scheduledPostJST': None, 'fromCompletedHistory': complete}


def build(reports_root, lap_root, history, models_root, now):
    reports_root = Path(reports_root)
    expected, groups = {}, {}
    day_issues = defaultdict(list)
    h = history.copy()
    h['日付'] = pd.to_datetime(h['日付'])
    h = h[(h['日付'].dt.strftime('%Y-%m-%d') >= START) & ~h['障害'].fillna(False).astype(bool)]
    for rid, group in h.groupby('レースID', sort=False):
        day = group['日付'].iloc[0].strftime('%Y-%m-%d')
        groups[rid] = group
        expected[rid] = metadata(rid, day, True)

    for path in sorted(reports_root.glob('*/PaceRecordingSchedule.json')):
        try:
            day = datetime.strptime(path.parent.name, '%Y%m%d').date().isoformat()
        except ValueError:
            continue
        if day < START:
            continue
        schedule, error = read_json(path)
        if (error or schedule is None or schedule.get('raceDate') != day
                or not isinstance(schedule.get('races'), list)):
            day_issues[day].append('開催予定の記録を一部読み取れません。')
            continue
        for race in schedule['races']:
            try:
                rid = race['raceID']
                if race_day(path.parent / 'prerace' / rid) != day or race['raceDate'] != day:
                    raise ValueError('schedule identity')
                post = aware_time(race['scheduledPostJST'])
                if (post.date().isoformat() != day or post.strftime('%H:%M') != race['startTime']
                        or race['venue'] != JRA_VENUE_MAP[rid[4:6]]
                        or race['raceNumber'] != int(rid[-2:])):
                    raise ValueError('schedule metadata')
                expected[rid] = {**race, 'fromCompletedHistory': rid in groups}
            except READ_ERRORS:
                day_issues[day].append('開催予定の記録を一部読み取れません。')

    # The directory is the durable identity, including an orphaned prediction
    # or a malformed event whose own identity fields cannot be trusted.
    for folder in recording_folders(reports_root, START):
        rid, day = folder.name, race_day(folder)
        expected.setdefault(rid, metadata(rid, day, rid in groups))

    models, model_issues = load_models(models_root)
    laps, lap_issues = load_laps(lap_root)
    rows = []
    for rid, race in sorted(expected.items(),
                            key=lambda item: (item[1]['raceDate'], item[1].get('startTime') or '', item[0]),
                            reverse=True):
        folder = reports_root / race['raceDate'].replace('-', '') / 'prerace' / rid
        index, index_error = read_json(folder / 'index.json')
        pred, pred_error = read_json(folder / 'PacePrediction.json')
        issues = list(day_issues[race['raceDate']])
        if index_error or (index and (index.get('raceID', rid) != rid
                                     or not isinstance(index.get('snapshots'), list))):
            issues.append('出馬表の索引を読み取れません。保存済み予測は別途確認します。')
        events = []
        event_paths = sorted((folder / 'events').glob('*.json'))
        for path in event_paths:
            event, error = read_json(path)
            try:
                if (error or event is None or event['raceID'] != rid
                        or event['raceDate'] != race['raceDate']
                        or event['stage'] not in EVENT_STAGES
                        or aware_time(event['atJST']).date().isoformat() != race['raceDate']):
                    raise ValueError('event identity')
                events.append(event)
            except READ_ERRORS:
                issues.append('失敗記録の一部を読み取れません。')
        latest = max(events, key=lambda event: aware_time(event['atJST'])) if events else None
        state, reason = 'waiting', '発走前の記録を待っています。'
        result_state, result_label = 'no_prediction', '予測未保存'
        captured = predicted = None
        post = aware_time(race['scheduledPostJST']) if race.get('scheduledPostJST') else None

        if pred_error or pred is not None:
            try:
                pred, snapshot = validate_prediction(folder, models)
                captured, predicted = pred['snapshotCapturedAtJST'], pred['predictedAtJST']
                if race['startTime'] is None:
                    race['startTime'] = aware_time(pred['scheduledPostJST']).strftime('%H:%M')
                if pred['status'] == 'held':
                    state = 'held'
                    reason = HOLD.get(pred.get('holdReason'), '発走前の条件確認で保留しました。')
                    result_state, result_label = 'not_evaluated', '照合対象外'
                else:
                    state, reason = 'saved', '発走前の研究予測を保存しました。'
                    result_state, result_label = 'awaiting_result', '結果待ち'
                    if rid in laps and rid in groups:
                        try:
                            _, changed = check_prediction(pred, snapshot, laps[rid], groups[rid]['馬番'].tolist())
                            if changed:
                                result_state, result_label = 'entrant_changed', '出走馬変更'
                                reason = '予測後に出走馬が変わったため、主集計から外します。'
                            else:
                                result_state, result_label = 'matched', '照合済み'
                        except READ_ERRORS:
                            result_state, result_label = 'invalid', '照合保留'
                            issues.append('結果の保存内容と予測の対応を確認できません。予測は保存済みです。')
                    elif race['raceDate'] in lap_issues:
                        result_state, result_label = 'invalid', '照合保留'
                        issues.append(lap_issues[race['raceDate']])
            except READ_ERRORS:
                state, reason = 'invalid', '保存内容・参照ファイル・時刻の整合性を確認できません。'
                result_state, result_label = 'invalid', '照合保留'
        elif latest and latest['stage'] in FAILURES:
            state, reason = 'failed', FAILURES[latest['stage']]
        elif race['fromCompletedHistory'] or (post and now >= post):
            state, reason = 'missing', 'レースは発走済みですが、発走前の研究予測が見つかりません。'

        if latest and latest['stage'] == 'monitor_missed' and pred is None and not pred_error:
            state, reason = 'missing', '発走前の記録時間に間に合いませんでした。'
        if any(event['stage'] == 'track_unavailable' for event in events):
            issues.append('馬場情報を取得できない試行がありました。')
        if state == 'saved' and any(event['stage'] in FAILURES for event in events):
            issues.append('保存前後に失敗した試行があります。予測は保存済みです。')
        issues = sorted(set(issues))
        row = {key: race[key] for key in ['raceID', 'raceDate', 'venue', 'raceNumber', 'startTime']}
        row.update(recordingState=state, recordingLabel=LABELS[state], resultState=result_state,
                   resultLabel=result_label, reason=reason, issues=issues,
                   snapshotCapturedAtJST=captured, predictedAtJST=predicted,
                   lastAttemptAtJST=latest['atJST'] if latest else predicted,
                   attemptEvents=len(event_paths),
                   needsAttention=state in {'failed', 'missing', 'invalid'} or result_state == 'invalid' or bool(issues))
        rows.append(row)

    counts = Counter(row['recordingState'] for row in rows)
    warnings = sorted(set(model_issues + list(lap_issues.values()) +
                          [f'{day}：{issue}' for day, entries in day_issues.items() for issue in entries]))
    empty = '研究記録の対象となる開催予定はまだ保存されていません。次の自動監視で一覧を作成します。'
    if warnings and not rows:
        empty = '記録の一部を読み取れないため、対象件数を確認できません。' + ' '.join(warnings)
    return {'schemaVersion': 1, 'status': 'recording_active' if rows else
            'recording_attention_required' if warnings else 'awaiting_first_schedule',
            'generatedAtJST': now.strftime('%Y-%m-%d %H:%M:%S'), 'recordingStartDate': START,
            'counts': {'totalRaces': len(rows), **{key: int(counts[key]) for key in LABELS},
                       'matched': sum(row['resultState'] == 'matched' for row in rows),
                       'awaitingResult': sum(row['resultState'] == 'awaiting_result' for row in rows),
                       'needsAttention': sum(row['needsAttention'] for row in rows)},
            'races': rows, 'warnings': warnings, 'emptyMessage': empty,
            'paceForecastEnabled': False, 'formalSignalsChanged': False, 'stakeSizingEnabled': False}


def markdown(data):
    lines = ['# 研究記録状況', '', f"更新：{data['generatedAtJST']} JST", '',
             f"対象 {data['counts']['totalRaces']}件／予測保存済み {data['counts']['saved']}件／要確認 {data['counts']['needsAttention']}件", '',
             'ペース予想の表示は保留中です。この一覧は研究用データの保存と照合の状況です。', '']
    if data.get('warnings'):
        lines += data['warnings'] + ['']
    if not data['races']:
        return '\n'.join(lines + [data['emptyMessage'], ''])
    lines += ['| 日付 | レース | 発走 | 記録 | 結果照合 | 原因・確認事項 |',
              '|---|---|---|---|---|---|']
    for row in data['races']:
        reason = ' '.join([row['reason']] + row['issues']).replace('|', '／').replace('\n', ' ')
        lines.append(f"| {row['raceDate']} | {row['venue']}{row['raceNumber']}R | {row['startTime'] or '未収録'} | {row['recordingLabel']} | {row['resultLabel']} | {reason} |")
    return '\n'.join(lines) + '\n'


def main():
    history = pd.read_parquet(ROOT / 'production/production_history.parquet',
                              columns=['レースID', '日付', '馬番', '障害'])
    data = build(ROOT / 'reports', ROOT / 'research/laps', history,
                 ROOT / 'research/pace/models', datetime.now(JST))
    path = ROOT / 'PaceRecordingStatus.json'
    report_path = ROOT / 'reports/PaceRecordingStatus.md'
    previous, _ = read_json(path)
    if previous:
        old = {key: value for key, value in previous.items() if key != 'generatedAtJST'}
        new = {key: value for key, value in data.items() if key != 'generatedAtJST'}
        try:
            age = (datetime.now(JST) - datetime.strptime(previous['generatedAtJST'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = 301
        if old == new and 0 <= age < 300 and report_path.exists():
            print('RECORDING_STATUS_UNCHANGED')
            return
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    atomic_text(report_path, markdown(data))
    print(json.dumps(data['counts']))


if __name__ == '__main__':
    main()
