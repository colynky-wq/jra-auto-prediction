"""Read completed archived ResultData only when it matches published entrants.

No odds or outcomes are reconstructed. This is a descriptive result input,
not evidence that the original prediction was formal or had fresh odds.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

import pandas as pd

JST = timezone(timedelta(hours=9))
NON_FINISH = {"取消", "出走取消", "除外", "競走除外", "中止", "競走中止", "失格", "降着", "取止"}


def load_archive(path):
    if not path.exists():
        return {}, None
    raw = path.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError("archived ResultData must be an array")
    index = {}
    for row in rows:
        rid = row["raceID"]
        if rid in index:
            raise ValueError("duplicate archived result race")
        index[rid] = row
    return index, hashlib.sha256(raw).hexdigest()


def result_frame(record, prediction, date8):
    rid = prediction["raceID"]
    day = datetime.strptime(date8, "%Y%m%d").date()
    if (record["raceID"] != rid or record["raceDate"] != day.isoformat()
            or prediction["raceDate"] != day.isoformat()
            or record["startTime"] != prediction["startTime"]
            or record["venue"] != prediction["venue"]
            or record["raceNumber"] != prediction["raceNumber"]
            or record.get("resultDetailVersion", 0) < 2):
        raise ValueError("archived result identity mismatch")
    updated = datetime.fromisoformat(record["resultUpdatedAtJST"])
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=JST)
    post = datetime.strptime(date8 + " " + record["startTime"],
                             "%Y%m%d %H:%M").replace(tzinfo=JST)
    if updated <= post:
        raise ValueError("archived result not recorded after post")
    expected = {h["number"]: re.sub(r"\s+", "", h["name"]) for h in prediction["horses"]}
    if len(expected) != len(prediction["horses"]):
        raise ValueError("duplicate predicted runner")
    observed = {}
    rows = []
    for h in record["horses"]:
        n = h["number"]
        if type(n) is not int or not 1 <= n <= 18 or n in observed:
            raise ValueError("invalid or duplicate archived runner")
        observed[n] = re.sub(r"\s+", "", h["name"])
        finish = h["finishPosition"]
        status = h["resultStatus"]
        if finish is None:
            if status not in NON_FINISH:
                raise ValueError("archived result not completed")
        elif (type(finish) is not int or not 1 <= finish <= len(expected)
              or status != "確定"):
            raise ValueError("invalid archived finish")
        odds = h.get("finalOdds")
        if odds is not None and (type(odds) not in (float, int)
                                 or not math.isfinite(odds) or odds < 1):
            raise ValueError("invalid archived final odds")
        rows.append({"raceID": rid, "number": n, "resultHorseName": h["name"],
                     "finish": finish, "resultStatus": status, "finalOdds": odds,
                     "finalPopularity": h.get("popularity")})
    if (expected != observed or record["fieldSize"] != len(expected)
            or not any(row["finish"] == 1 for row in rows)):
        raise ValueError("archived result roster or winner mismatch")
    return pd.DataFrame(rows).sort_values("number").reset_index(drop=True)
