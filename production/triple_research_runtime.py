"""Retryable background I/O for trifecta/trio research records."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import threading

from triple_forward import JST, TYPES, record_first

_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="triple-research")
_lock = threading.Lock()
_futures = {}


def capture(root,race,predicted):
    failures=[]
    for kind in TYPES:
        try:
            path=record_first(root,race,kind,predicted)
            print("TRIPLE_RESEARCH_CAPTURE",kind,race["raceID"],str(path) if path else "deadline_or_day_guard")
            if path is None:
                failures.append(kind+":deadline_or_day_guard")
        except Exception as error:
            print("WARNING: TRIPLE_RESEARCH_CAPTURE_FAILED",kind,race.get("raceID"),type(error).__name__)
            failures.append(kind+":"+type(error).__name__)
    if failures:
        raise RuntimeError("triple research incomplete: "+",".join(failures))
    return True


def schedule(root,race,predicted=None):
    """Schedule odds capture against the exact formal live_10min timestamp."""
    key=(race["raceDate"],race["raceID"])
    predicted = predicted or datetime.now(JST).isoformat()
    with _lock:
        previous=_futures.get(key)
        if previous is not None:
            if not previous.done():
                return previous
            try:
                if previous.exception() is None:
                    return previous
            except Exception:
                pass
        future=_pool.submit(capture,root,deepcopy(race),str(predicted))
        _futures[key]=future
        return future
