"""Read research records independently and publish complete report bytes."""
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from horse_profile_runtime import JRA_VENUE_MAP
from prerace_snapshot_runtime import JST

LABELS = ('fast', 'middle', 'slow')


def read_object(path):
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        return data, hashlib.sha256(raw).hexdigest(), None
    except FileNotFoundError:
        return None, None, None
    except (OSError, ValueError, UnicodeError):
        return None, None, '保存ファイルを読み取れません。'


def aware_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('timezone missing')
    return result.astimezone(JST)


def race_day(folder):
    day = datetime.strptime(folder.parents[1].name, '%Y%m%d').date().isoformat()
    rid = folder.name
    if (not re.fullmatch(r'\d{12}', rid) or rid[:4] != day[:4]
            or rid[4:6] not in JRA_VENUE_MAP or not 1 <= int(rid[-2:]) <= 12):
        raise ValueError('invalid race folder identity')
    return day


def recording_folders(reports_root, start):
    result = []
    for folder in sorted(Path(reports_root).glob('*/prerace/*')):
        if not folder.is_dir():
            continue
        try:
            day = race_day(folder)
        except ValueError:
            continue
        if day >= start and (any(folder.glob('*.json')) or any((folder / 'events').glob('*.json'))):
            result.append(folder)
    return result


def load_models(models_root):
    models, issues, rejected = {}, [], set()
    for path in sorted(Path(models_root).glob('*.json')):
        model, sha, error = read_object(path)
        try:
            if model is None or error:
                raise ValueError('unreadable model')
            key = model['modelID']
            if not isinstance(key, str) or not key:
                raise ValueError('model ID missing')
            if date.fromisoformat(model['trainingCutoffDate']) >= aware_time(model['fittedAtJST']).date():
                raise ValueError('model training dates invalid')
            if key in models and models[key][1] != sha:
                models.pop(key)
                rejected.add(key)
                raise ValueError('ambiguous model ID')
            if key not in rejected:
                models[key] = (model, sha)
        except (KeyError, ValueError, TypeError):
            issues.append('研究モデルの登録情報を一部読み取れません。')
    return models, sorted(set(issues))


def load_laps(lap_root):
    laps, issues, rejected = {}, {}, set()
    for path in sorted(Path(lap_root).glob('*/RaceLapData.json')):
        data, _, error = read_object(path)
        try:
            day = datetime.strptime(path.parent.name, '%Y%m%d').date().isoformat()
        except ValueError:
            continue
        rows = data.get('races') if data is not None else None
        if error or not isinstance(rows, list):
            issues[day] = '当日の結果記録を一部読み取れず、照合を保留しています。'
            continue
        for row in rows:
            try:
                if not isinstance(row, dict):
                    raise ValueError('invalid lap row')
                key = row['raceID']
                if (not isinstance(key, str) or not re.fullmatch(r'\d{12}', key)
                        or key[:4] != day[:4] or row['raceDate'] != day):
                    raise ValueError('lap identity mismatch')
                if not isinstance(row.get('validation'), dict):
                    raise ValueError('invalid lap validation')
                if key in laps and laps[key] != row:
                    laps.pop(key)
                    rejected.add(key)
                    raise ValueError('ambiguous race result')
                if key not in rejected:
                    laps[key] = row
            except (KeyError, ValueError, TypeError):
                issues[day] = '当日の結果記録を一部読み取れず、照合を保留しています。'
    return laps, issues


def validate_prediction(folder, models):
    """Validate the immutable record even when there are no completed results."""
    pred, _, error = read_object(folder / 'PacePrediction.json')
    if error or pred is None:
        raise ValueError('prediction unreadable')
    day = race_day(folder)
    if pred['raceID'] != folder.name or pred['raceDate'] != day:
        raise ValueError('prediction identity mismatch')
    capture, available, generated, post, fitted = [aware_time(pred[k]) for k in (
        'snapshotCapturedAtJST', 'inputAvailableAtJST', 'predictedAtJST',
        'scheduledPostJST', 'modelFittedAtJST')]
    if (not capture <= available <= generated < post or not fitted <= generated
            or any(t.date().isoformat() != day for t in (capture, available, generated, post))
            or date.fromisoformat(pred['trainingCutoffDate']) >= date.fromisoformat(day)):
        raise ValueError('prediction time mismatch')
    source_day = pred.get('latestSourceDay')
    if source_day is not None and date.fromisoformat(source_day) >= date.fromisoformat(day):
        raise ValueError('future feature source')
    filename = pred['snapshotFile']
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.json'):
        raise ValueError('invalid snapshot filename')
    snapshot, sha, error = read_object(folder / filename)
    if error or snapshot is None or sha != pred['snapshotSHA256']:
        raise ValueError('snapshot bytes changed or missing')
    if pred['modelID'] not in models:
        raise ValueError('unregistered model')
    model, sha = models[pred['modelID']]
    if (sha != pred['modelFileSHA256'] or model['fittedAtJST'] != pred['modelFittedAtJST']
            or model['trainingCutoffDate'] != pred['trainingCutoffDate']):
        raise ValueError('model identity mismatch')
    if (snapshot['raceID'] != folder.name or snapshot['raceDate'] != day
            or snapshot['capturedAtJST'] != pred['snapshotCapturedAtJST']
            or snapshot['scheduledPostJST'] != pred['scheduledPostJST']
            or aware_time(snapshot.get('completeInputAvailableAtJST', snapshot['capturedAtJST'])) != available
            or snapshot.get('prePostConfirmed') is not True):
        raise ValueError('snapshot identity mismatch')
    if pred['status'] not in {'held', 'research_forecast'}:
        raise ValueError('unknown prediction status')
    if pred['status'] == 'research_forecast':
        for field in ('probabilities', 'baselineProbabilities'):
            values = [pred[field][key] for key in LABELS]
            if (any(isinstance(v, bool) or not isinstance(v, (float, int))
                    or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
                    or abs(sum(values) - 1) > 1e-9):
                raise ValueError('invalid probability vector')
        q25, median, q75 = pred['referenceQuantilesSeconds']
        if not all(math.isfinite(v) for v in (q25, median, q75)) or not 0 < q25 <= median <= q75:
            raise ValueError('invalid reference quantiles')
    return pred, snapshot


def atomic_text(path, text):
    path = Path(path)
    descriptor, filename = tempfile.mkstemp(prefix='.pace-recording-', suffix='.tmp', dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
