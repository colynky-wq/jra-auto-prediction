"""Only publish settled after-race research from a complete, same-day result set.

Does not create missing T-10 captures or imply all race-day predictions were
formally frozen. A complete set of independently fetched result identities is
required before derived research reports may be republished.
"""
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sys

JST = timezone(timedelta(hours=9))


class NotReady(RuntimeError):
    pass


def check(races, results, now):
    if now.tzinfo is None:
        raise ValueError("aware clock required")
    local = now.astimezone(JST)
    day = local.date().isoformat()
    if local.time() < time(17, 35):
        raise NotReady("racing still active or early result-settlement window")
    if not isinstance(races, list) or not isinstance(results, list):
        raise ValueError("source datasets must be arrays")
    if not races:
        raise NotReady("no current race-day forecast display")
    if any(not isinstance(r, dict) for r in races+results):
        raise ValueError("source race/result contains non-object")
    race_days = {r.get("raceDate") for r in races}
    result_days = {r.get("raceDate") for r in results}
    if race_days != {day}:
        raise NotReady("RaceData is not for today's just-completed meeting")
    if result_days != {day}:
        raise NotReady("independently fetched results are not all from today's meeting")
    ids = [r.get("raceID") for r in races]
    finished = [r.get("raceID") for r in results]
    if any(not isinstance(rid, str) or re.fullmatch(r"\d{12}", rid) is None
           for rid in ids + finished):
        raise ValueError("invalid published result or prediction race ID")
    if len(set(ids)) != len(ids) or len(set(finished)) != len(finished):
        raise ValueError("duplicate race ID in published source")
    if set(ids) != set(finished):
        raise NotReady("not all published races have independently retrieved results")
    return {"date": day, "races": len(ids), "results": len(finished),
            "researchOnly": True, "formalT10Reconstruction": False}


def main():
    root = Path(__file__).resolve().parents[1]
    races = json.loads((root/"RaceData.json").read_text(encoding="utf-8"))
    results = json.loads((root/"ResultData.json").read_text(encoding="utf-8"))
    try:
        ready = check(races, results, datetime.now(JST))
    except NotReady as exc:
        print("AFTER_RACING_RESEARCH_DEFERRED:", str(exc), flush=True)
        return 10
    print("AFTER_RACING_RESEARCH_SOURCE_CONFIRMED:",
          json.dumps(ready, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
