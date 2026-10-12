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
RACEDATA_PATH = REPO / "RaceData.json"
RESULTDATA_PATH = REPO / "ResultData.json"
REPORTS_DIR = REPO / "reports"

TARGET_DATE8 = (
    os.environ.get("KEIBAAI_RESULT_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)
DELAY_MINUTES = int(
    os.environ.get("KEIBAAI_RESULT_DELAY_MINUTES", "5").strip() or "5"
)


def finite_or_none(value):
    try:
        x = float(value)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def int_or_none(value):
    x = finite_or_none(value)
    return int(x) if x is not None else None


def wanted_date_text() -> str:
    if len(TARGET_DATE8) != 8 or not TARGET_DATE8.isdigit():
        raise RuntimeError(f"invalid target date: {TARGET_DATE8!r}")
    return f"{TARGET_DATE8[:4]}-{TARGET_DATE8[4:6]}-{TARGET_DATE8[6:8]}"


def already_started(race: dict) -> bool:
    today8 = datetime.now(JST).strftime("%Y%m%d")
    if TARGET_DATE8 < today8:
        return True
    if TARGET_DATE8 > today8:
        return False

    try:
        post = datetime.strptime(
            f"{TARGET_DATE8} {race.get('startTime')}",
            "%Y%m%d %H:%M",
        ).replace(tzinfo=JST)
        return datetime.now(JST) >= post + timedelta(minutes=DELAY_MINUTES)
    except Exception:
        return False


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_predictions() -> list[dict]:
    if not RACEDATA_PATH.exists():
        raise RuntimeError(f"RaceData.json not found: {RACEDATA_PATH}")
    with RACEDATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError("RaceData.json root must be a JSON array")
    wanted = wanted_date_text()
    return [r for r in data if str(r.get("raceDate", "")) == wanted]


def race_complete(rows: list[dict]) -> bool:
    if not rows:
        return False
    for row in rows:
        if row.get("finishPosition") is not None:
            continue
        status = str(row.get("resultStatus") or "").strip()
        if status and status not in {"不明", "nan", "None"}:
            continue
        return False
    return True


def build_result_for_race(race: dict) -> dict | None:
    rid = str(race.get("raceID", "")).zfill(12)
    predicted_horses = race.get("horses") or []
    predicted_numbers = {
        int(h.get("number"))
        for h in predicted_horses
        if str(h.get("number", "")).strip().isdigit()
    }

    if not predicted_numbers:
        print("SKIP no predicted horses:", rid)
        return None

    try:
        d = agg.fetch_result_table(rid)
    except Exception as exc:
        print("RESULT NOT READY:", rid, repr(exc))
        return None

    d = d.loc[d["number"].astype(int).isin(predicted_numbers)].copy()
    if len(d) != len(predicted_numbers):
        print(
            "RESULT INCOMPLETE:", rid,
            "predicted=", len(predicted_numbers),
            "result=", len(d),
        )
        return None

    horses = []
    for _, row in d.sort_values("number").iterrows():
        horses.append(
            {
                "number": int(row["number"]),
                "name": str(row.get("resultHorseName", "")),
                "finishPosition": int_or_none(row.get("finish")),
                "resultStatus": str(row.get("resultStatus", "")) or None,
                "finalOdds": finite_or_none(row.get("finalOdds")),
                "popularity": int_or_none(row.get("finalPopularity")),
            }
        )

    if not race_complete(horses):
        print("RESULT NOT FINAL ENOUGH:", rid)
        return None

    return {
        "raceDate": wanted_date_text(),
        "raceID": rid,
        "venue": race.get("venue"),
        "raceNumber": race.get("raceNumber"),
        "startTime": race.get("startTime"),
        "raceName": race.get("raceName"),
        "course": race.get("course"),
        "fieldSize": len(predicted_numbers),
        "resultUpdatedAtJST": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "horses": horses,
    }


def write_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def main() -> None:
    os.environ["KEIBAAI_AGGREGATE_DATE"] = TARGET_DATE8
    agg.TARGET_DATE8 = TARGET_DATE8

    predictions = load_predictions()
    if not predictions:
        print("No RaceData races for target date; leave ResultData unchanged.")
        return

    day_path = REPORTS_DIR / TARGET_DATE8 / "ResultData.json"
    existing = load_json_list(day_path)
    if not existing:
        existing = [
            r for r in load_json_list(RESULTDATA_PATH)
            if str(r.get("raceDate", "")) == wanted_date_text()
        ]

    by_race = {
        str(r.get("raceID", "")).zfill(12): r
        for r in existing
        if str(r.get("raceID", "")).strip()
    }

    eligible = 0
    added_or_refreshed = 0
    waiting = 0

    for race in predictions:
        rid = str(race.get("raceID", "")).zfill(12)
        if not already_started(race):
            continue
        eligible += 1

        # Already-complete rows are stable. Preserve them instead of repeatedly
        # hitting JRA every five minutes.
        old = by_race.get(rid)
        if old and race_complete(old.get("horses") or []):
            continue

        built = build_result_for_race(race)
        if built is None:
            waiting += 1
            continue

        by_race[rid] = built
        added_or_refreshed += 1

    output = list(by_race.values())
    output.sort(
        key=lambda r: (
            str(r.get("startTime") or ""),
            str(r.get("venue") or ""),
            int(r.get("raceNumber") or 0),
            str(r.get("raceID") or ""),
        )
    )

    if not output:
        print("No completed results yet; leave ResultData unchanged.")
        return

    write_json(RESULTDATA_PATH, output)
    write_json(day_path, output)

    print("target:", TARGET_DATE8)
    print("prediction races:", len(predictions))
    print("eligible after delay:", eligible)
    print("published result races:", len(output))
    print("new/refreshed this run:", added_or_refreshed)
    print("still waiting:", waiting)
    print("root:", RESULTDATA_PATH)
    print("day copy:", day_path)


if __name__ == "__main__":
    main()
