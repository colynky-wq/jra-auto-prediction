"""Select an actually applied history day for post-race lap collection.

Standard-library-only startup check. It never collects data, evaluates a
prediction, edits history, or writes a forecast. GITHUB_OUTPUT is its only write.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

JST = timezone(timedelta(hours=9))
REPOSITORY = 'colynky-wq/jra-auto-prediction'
WORKFLOW = 'KeibaAI Production History Update'
WORKFLOW_PATH = '.github/workflows/keibaai-history-update.yml'
START = date(2026, 10, 13)


class TriggerError(ValueError):
    pass


def aware(value):
    if not isinstance(value, str):
        raise TriggerError('更新記録の時刻を確認できません。')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise TriggerError('更新記録の時刻を確認できません。') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise TriggerError('更新記録の時刻にタイムゾーンがありません。')
    return result


def select_plan(event, reports_root, now):
    """Trust only a completed own-main history run and its applied report."""
    try:
        run = event['workflow_run']
        if (event['action'] != 'completed'
                or event['repository']['full_name'] != REPOSITORY
                or run['head_repository']['full_name'] != REPOSITORY
                or run['head_branch'] != 'main'
                or run['name'] != WORKFLOW or run['path'] != WORKFLOW_PATH
                or run['event'] not in {'schedule', 'workflow_dispatch'}
                or run['status'] != 'completed' or run['conclusion'] != 'success'
                or type(run['id']) is not int or run['id'] <= 0):
            raise TriggerError('対象の履歴更新の成功通知ではありません。')
        start = aware(run['run_started_at'])
        finish = aware(run['updated_at'])
        if now.tzinfo is None or not start <= finish <= now:
            raise TriggerError('履歴更新の実行時間を確認できません。')
    except (KeyError, TypeError, AttributeError) as exc:
        raise TriggerError('履歴更新の通知形式を確認できません。') from exc

    candidates, unreadable = [], []
    for path in sorted(Path(reports_root).glob('*.json')):
        if not re.fullmatch(r'\d{8}', path.stem):
            continue
        try:
            day = datetime.strptime(path.stem, '%Y%m%d').date()
        except ValueError:
            continue
        if day < START:
            continue
        try:
            if path.is_symlink():
                raise TriggerError('更新記録が通常のファイルではありません。')
            report = json.loads(path.read_text(encoding='utf-8'))
            generated = aware(report['generatedAtJST'])
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            unreadable.append(path.name)
            continue
        # Old committed reports are not evidence of a change in this run.
        if not start <= generated <= finish:
            continue
        try:
            if (report['status'] != 'applied' or report['applyRequested'] is not True
                    or report['targetDate'] != path.stem
                    or report['newLatestDate'] != day.isoformat()
                    or date.fromisoformat(report['oldLatestDate']) >= day
                    or day > now.astimezone(JST).date()):
                raise ValueError('report identity or applied state')
            for key in ['oldRows', 'newRows', 'combinedRows', 'oldRaceCount',
                        'addedRaceCount', 'combinedRaceCount', 'duplicateRaceHorseKeys']:
                if type(report[key]) is not int or report[key] < 0:
                    raise ValueError('report counts')
            if (report['newRows'] <= 0 or report['addedRaceCount'] <= 0
                    or report['combinedRows'] != report['oldRows'] + report['newRows']
                    or report['combinedRaceCount'] != report['oldRaceCount'] + report['addedRaceCount']
                    or report['duplicateRaceHorseKeys'] != 0):
                raise ValueError('report consistency')
        except (KeyError, ValueError, TypeError) as exc:
            raise TriggerError(f'{path.name}：実際に適用した履歴更新を確認できません。') from exc
        candidates.append(path.stem)

    if len(candidates) > 1:
        raise TriggerError('同じ実行時間に複数の開催日の更新記録があります。')
    if not candidates and unreadable:
        raise TriggerError('更新記録を読み取れないため、更新日を確認できません。')
    target = candidates[0] if candidates else ''
    return {'shouldCollect': bool(target), 'targetDate': target, 'sourceRunID': run['id'],
            'reason': 'applied_history_day' if target else 'no_applied_history_in_this_run',
            'unreadableReports': unreadable}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', type=Path, required=True)
    parser.add_argument('--reports-root', type=Path, required=True)
    parser.add_argument('--github-output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        event = json.loads(args.event.read_text(encoding='utf-8'))
        plan = select_plan(event, args.reports_root, datetime.now(JST))
        lines = [f"should_collect={str(plan['shouldCollect']).lower()}",
                 f"target_date={plan['targetDate']}", f"source_run_id={plan['sourceRunID']}"]
        with args.github_output.open('a', encoding='utf-8') as out:
            out.write('\n'.join(lines) + '\n')
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print(f'LAP_HISTORY_TRIGGER_FAILED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
