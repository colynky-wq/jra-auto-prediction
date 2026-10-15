"""Bounded background I/O for research; the prediction publisher never waits."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import threading

from triple_forward import JST, TYPES, record_first

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='triple-research')
_lock = threading.Lock()
_submitted = set()


def capture(root, race, predicted):
    for kind in TYPES:
        try:
            saved = record_first(root, race, kind, predicted)
            print('TRIPLE_RESEARCH_CAPTURE', kind, race['raceID'], str(saved) if saved else 'deadline_or_day_guard')
        except Exception as error:
            print('WARNING: TRIPLE_RESEARCH_CAPTURE_FAILED', kind, race.get('raceID'), type(error).__name__)


def schedule(root, race):
    """One first attempt per race; the worker preserves the exact passed inputs."""
    key = (race['raceDate'], race['raceID'])
    with _lock:
        if key in _submitted:
            return False
        _submitted.add(key)
        _pool.submit(capture, root, deepcopy(race), datetime.now(JST).isoformat())
    return True
