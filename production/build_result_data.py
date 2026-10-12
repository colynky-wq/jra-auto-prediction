from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# Importing v2 installs the strengthened JRA result fetcher into agg.
import run_forward_aggregate_v2  # noqa: F401
import aggregate_forward_results as agg

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA = REPO / "RaceData.json"
OUTDIR = REPO / "result_data_artifacts"
TARGET_DATE8 = os.environ.get("KEIBAAI_RESULT_DATE", "").strip() or datetime.now(JST).strftime("%Y%m%d")


def finite_or_none(value):
    try:
        x = float(value)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def int_or_none(value):
    x = finite_or_none(value)
    return int(x) if x is not None else None


def already_started(race: dict) -> bool:
    # Past dates are always eligible. For today, wait a few minutes after post.
    today8 = datetime.now(JST).strftime("%Y%m%d")
    if TARGET_DATE8 < today8:
        return True
    if TARGET_DATE8 > today8:
        return False
    try:
        post = datetime.strptime(f"{TARGET_DATE8} {race.get('startTime')}", "%Y%m%d %H:%M").replace(tzinfo=JST)
        return datetime.now(JST) >= post + timedelta(minutes=3)
    except Exception:
        return False


def main() -> None:
    os.environ["KEIBAAI_AGGREGATE_DATE"] = TARGET_DATE8
    # aggregate_forward_results reads its target at import time; set it explicitly too.
    agg.TARGET_DATE8 = TARGET_DATE8

    with RACEDATA.open("r", encoding="utf-8") as f:
        races = json.load(f)

    wanted_date = f"{TARGET_DATE8[:4]}-{TARGET_DATE8[4:6]}-{TARGET_DATE8[6:8]}"
    races = [r for r in races if str(r.get("raceDate", "")) == wanted_date]

    output = []
    skipped = []

    for race in races:
        rid = str(race.get("raceID", "")).zfill(12)
        if not already_started(race):
            continue

        try:
            d = agg.fetch_result_table(rid)
        except Exception as exc:
            print("RESULT NOT READY:", rid, repr(exc))
            skipped.append(rid)
            continue

        horses = []
        for _, row in d.sort_values("number").iterrows():
            horses.append({
                "number": int(row["number"]),
                "name": str(row.get("resultHorseName", "")),
                "finishPosition": int_or_none(row.get("finish")),
                "resultStatus": str(row.get("resultStatus", "")) or None,
                "finalOdds": finite_or_none(row.get("finalOdds")),
                "popularity": int_or_none(row.get("finalPopularity")),
            })

        output.append({
            "raceDate": wanted_date,
            "raceID": rid,
            "venue": race.get("venue"),
            "raceNumber": race.get("raceNumber"),
            "fieldSize": len(horses),
            "horses": horses,
        })

    output.sort(key=lambda r: (r.get("venue") or "", r.get("raceNumber") or 0))
    OUTDIR.mkdir(parents=True, exist_ok=True)
    path = OUTDIR / "ResultData.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("target:", TARGET_DATE8)
    print("prediction races:", len(races))
    print("result races:", len(output))
    print("not ready / skipped:", len(skipped))
    print("artifact:", path)


if __name__ == "__main__":
    main()
