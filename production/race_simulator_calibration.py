"""Versioned, bounded simulator settings; no prediction-AI or market inputs."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

BASELINE_ID = 'race-simulator-v02-baseline'
DEFAULTS = {'speedScale': 1.0, 'strengthScale': 1.0, 'styleScale': 1.0}
BOUNDS = {'speedScale': (0.90, 1.10), 'strengthScale': (0.5, 1.5), 'styleScale': (0.5, 1.5)}
ENGINE_FILES = ('race_simulator_v02.py', 'race_simulator_v02_adapter.py', 'race_simulator_calibration.py')

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def settings(value=None):
    value = value or {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('unsupported calibration setting')
    result = dict(DEFAULTS)
    for key, raw in value.items():
        if isinstance(raw, bool): raise ValueError('boolean calibration setting')
        number = float(raw)
        lo, hi = BOUNDS[key]
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError('calibration setting outside bounded range: ' + key)
        result[key] = number
    return result

def engine_digest():
    folder = Path(__file__).resolve().parent
    return digest({name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ENGINE_FILES})

def baseline():
    return {'schemaVersion': 1, 'modelID': BASELINE_ID, 'settings': settings(),
            'engineSHA256': engine_digest(), 'trainingRaceIDs': [], 'createdAt': None, 'trainingCutoff': None}

def validate_model(model):
    if model.get('schemaVersion') != 1 or model.get('engineSHA256') != engine_digest():
        raise ValueError('simulator model engine/version mismatch')
    settings(model['settings'])
    if model['modelID'] == BASELINE_ID:
        if model != baseline(): raise ValueError('baseline model altered')
    elif model['modelID'] != 'rs-' + digest({k: v for k, v in model.items() if k != 'modelID'})[:24]:
        raise ValueError('model content hash mismatch')
    return model

def load_registry(root):
    path = Path(root)/'research/race_simulator/state.json'
    if not path.exists():
        return {'schemaVersion': 1, 'activeModelID': BASELINE_ID, 'candidateModelID': None, 'decisions': [], 'generation': 0}
    data = json.loads(path.read_text())
    if data.get('schemaVersion') != 1: raise ValueError('unsupported learning registry')
    return data

def load_model(root, model_id):
    if model_id == BASELINE_ID: return baseline()
    import re
    if not re.fullmatch(r'rs-[0-9a-f]{24}', str(model_id)): raise ValueError('invalid model identifier')
    model = validate_model(json.loads((Path(root)/'research/race_simulator/models'/f'{model_id}.json').read_text()))
    if model['modelID'] != model_id: raise ValueError('model path identity mismatch')
    return model

def active_model(root):
    return load_model(root, load_registry(root)['activeModelID'])
