"""Read saved schedules and forecasts to expose omissions, never create forecasts."""
from datetime import datetime
import json
from pathlib import Path
import re

from triple_forward import stamp


def identity(race):
    day, rid, time = (race[k] for k in ('raceDate', 'raceID', 'startTime'))
    if (not isinstance(rid, str) or not re.fullmatch(r'\d{12}', rid)
            or datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day
            or not isinstance(time, str) or not re.fullmatch(r'\d{2}:\d{2}', time)):
        raise ValueError('invalid coverage race identity')
    post = stamp(day+'T'+time)
    return (day, rid), post


def expected_races(root, start, now):
    """Published forecasts plus elapsed races in genuine first-observed schedules.

    A scheduled race still ahead of us is waiting, not missing. A broken schedule
    is a diagnostic, never evidence of no racing. RaceData is allowed to contain
    older or other-day forecasts; keep each race's own date across midnight.
    """
    root = Path(root)
    found, warnings = {}, []
    path = root/'RaceData.json'
    if path.exists():
        try:
            races = json.loads(path.read_text())
            if not isinstance(races, list):
                raise ValueError('forecast list required')
            seen = set()
            for race in races:
                key, post = identity(race)
                if key in seen:
                    raise ValueError('duplicate forecast race')
                seen.add(key)
                if post >= start and post.date() <= now.date():
                    found[key] = race
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append('通常予測の一覧を完全に確認できません。')
    for path in sorted((root/'reports').glob('*/PaceRecordingSchedule.json')):
        try:
            day = datetime.strptime(path.parent.name, '%Y%m%d').date().isoformat()
            if day < start.date().isoformat() or day > now.date().isoformat():
                continue
            data = json.loads(path.read_text())
            observed = stamp(data['observedAtJST'])
            if (data['schemaVersion'] != 1 or data['raceDate'] != day
                    or observed.date().isoformat() != day or observed > now
                    or not isinstance(data['races'], list)):
                raise ValueError('invalid observed schedule')
            staged, seen = {}, set()
            for race in data['races']:
                key, post = identity(race)
                if (key[0] != day or key in seen or observed >= post
                        or stamp(race['scheduledPostJST']) != post
                        or race['raceNumber'] != int(key[1][-2:])):
                    raise ValueError('schedule race/time mismatch')
                seen.add(key)
                if start <= post <= now:
                    staged[key] = {**race, 'raceName':race.get('raceName') or
                                   f"{race.get('venue','')}{race['raceNumber']}R"}
            # A published prediction may reflect a legitimate changed post time.
            for key, race in staged.items():
                found.setdefault(key, race)
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append(str(path.relative_to(root))+': 開催予定の記録を確認できません。')
    return [found[k] for k in sorted(found)], warnings


def missing_reason(root, race, now):
    """Expose normal-prediction failure separately from absent pool capture."""
    path = Path(root)/'RaceData.json'
    try:
        forecasts = json.loads(path.read_text()) if path.exists() else []
        if any((r.get('raceDate'), r.get('raceID')) == (race['raceDate'], race['raceID']) for r in forecasts):
            return '予測は保存されていますが、この券種の初回記録はまだ確認できません。'
    except (OSError, ValueError, TypeError, AttributeError):
        return '通常予測の一覧を読み取れず、初回記録との対応を確認できません。'
    folder = Path(root)/'reports'/race['raceDate'].replace('-', '')/'prerace'/race['raceID']/'events'
    for path in sorted(folder.glob('*.json')):
        try:
            event = json.loads(path.read_text())
            if (event['raceID'] == race['raceID'] and event['raceDate'] == race['raceDate']
                    and event['stage'] == 'monitor_failed' and stamp(event['atJST']) <= now
                    and stamp(event['atJST']).date().isoformat() == race['raceDate']):
                return '通常予測の処理に失敗し、この券種の初回記録もありません。'+str(event['detail'])[:500]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return '予定発走を過ぎましたが、通常予測とこの券種の初回記録が保存されていません。'
