"""Separate I/O worker for added pools; no wait in the app prediction publisher."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import threading

from all_bet_forward import JST, record_first
from all_bet_baseline import TYPES

_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='all-bet-research')
_lock = threading.Lock()
_submitted = set()


def capture(root,race,predicted,evidence):
    for kind in TYPES:
        try:
            path = record_first(root,race,kind,predicted,evidence)
            print('ALL_BET_RESEARCH_CAPTURE',kind,race['raceID'],str(path) if path else 'deadline_or_day_guard')
        except Exception as error:
            print('WARNING: ALL_BET_RESEARCH_CAPTURE_FAILED',kind,race.get('raceID'),type(error).__name__)


def schedule(root,race,evidence=None):
    key = (race['raceDate'],race['raceID'])
    with _lock:
        if key in _submitted:
            return False
        _submitted.add(key)
        _pool.submit(capture,root,deepcopy(race),datetime.now(JST).isoformat(),deepcopy(evidence))
    return True
