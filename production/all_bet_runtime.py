"""Retryable I/O worker for added-pool research records."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import threading

from all_bet_forward import JST, record_first
from all_bet_baseline import TYPES

_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="all-bet-research")
_lock = threading.Lock()
_futures = {}


def capture(root,race,predicted,evidence):
    failures=[]
    for kind in TYPES:
        try:
            path=record_first(root,race,kind,predicted,evidence)
            print("ALL_BET_RESEARCH_CAPTURE",kind,race["raceID"],str(path) if path else "deadline_or_day_guard")
            if path is None:
                failures.append(kind+":deadline_or_day_guard")
        except Exception as error:
            print("WARNING: ALL_BET_RESEARCH_CAPTURE_FAILED",kind,race.get("raceID"),type(error).__name__)
            failures.append(kind+":"+type(error).__name__)
    if failures:
        raise RuntimeError("all-bet research incomplete: "+",".join(failures))
    return True


def schedule(root,race,evidence=None,predicted=None):
    """Schedule odds capture against the exact formal prediction timestamp.

    ``predicted`` is optional for backwards compatibility, but the formal live
    publisher passes RaceData.capturedAtJST so every saved odds record is bound
    to the same immutable live_10min prediction rather than a later worker time.
    """
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
        future=_pool.submit(capture,root,deepcopy(race),str(predicted),deepcopy(evidence))
        _futures[key]=future
        return future
