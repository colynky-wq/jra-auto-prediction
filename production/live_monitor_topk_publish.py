from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

import live_monitor_dry_run as base
import live_monitor_topk as topk


HERE = Path(__file__).resolve().parent
RACEDATA_PATH = HERE.parent / "RaceData.json"

PUBLISH_TO_APP = (
    os.environ.get("KEIBAAI_PUBLISH_RACEDATA", "false")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)


def _number(value, digits: int = 3) -> float:
    return round(float(value), digits)


def _target_date_text() -> str:
    value = str(base.TARGET_DATE8)
    if len(value) != 8 or not value.isdigit():
        raise RuntimeError(f"invalid target date for RaceData: {value!r}")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _load_existing() -> list[dict]:
    if not RACEDATA_PATH.exists():
        return []

    with RACEDATA_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, list):
        raise RuntimeError("RaceData.json root must be a JSON array")

    return payload


def publish_race_to_racedata(
    race: dict,
    out: pd.DataFrame,
) -> None:
    """Publish one live Top2/Top3 prediction into the app RaceData.json.

    On the first prediction of a new race day, old entries without the same
    raceDate are removed. Earlier predictions from the same target date remain,
    so RaceData.json grows race-by-race through the day.
    """
    target_date = _target_date_text()
    existing = _load_existing()

    todays = [
        item
        for item in existing
        if str(item.get("raceDate", "")).strip() == target_date
    ]

    horses: list[dict] = []
    for _, row in out.sort_values("馬番").iterrows():
        horses.append(
            {
                "number": int(row["馬番"]),
                "name": str(row["馬名"]),
                "odds": _number(row["単勝オッズ"], 1),
                "marketProbability": _number(row["市場勝率(%)"]),
                "aiProbability": _number(row["AI勝率(%)"]),
                "marketTop2Probability": _number(row["市場2着内率(%)"]),
                "aiTop2Probability": _number(row["AI2着内率(%)"]),
                "marketTop3Probability": _number(row["市場3着内率(%)"]),
                "aiTop3Probability": _number(row["AI3着内率(%)"]),
                "aiMarketRatio": _number(row["AI÷市場"], 4),
                "expectedIndex": _number(row["期待払戻指数"]),
            }
        )

    published_race = {
        "raceDate": target_date,
        "raceID": str(race["race_id"]),
        "venue": str(race["venue"]),
        "raceNumber": int(race["race_no"]),
        "startTime": str(race["start_time"]),
        "raceName": str(race.get("race_name", "")),
        "course": f"{race['surface']}{int(race['distance'])}m",
        "horses": horses,
    }

    rid = published_race["raceID"]
    replaced = False
    for i, item in enumerate(todays):
        if str(item.get("raceID", "")) == rid:
            todays[i] = published_race
            replaced = True
            break

    if not replaced:
        todays.append(published_race)

    todays.sort(
        key=lambda item: (
            str(item.get("startTime", "")),
            str(item.get("raceID", "")),
        )
    )

    tmp_path = RACEDATA_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(
            todays,
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    tmp_path.replace(RACEDATA_PATH)

    print()
    print("=" * 110)
    print("KEIBAAI APP PUBLISH")
    print("=" * 110)
    print("target date:", target_date)
    print("published race:", rid)
    print("published horses:", len(horses))
    print("RaceData races for target date:", len(todays))
    print("RaceData.json modified: YES")
    print("Top2 / Top3 fields written: YES")
    print("=" * 110)


_original_predict = topk.predict_live_race_topk


def predict_live_race_topk_and_publish(
    race: dict,
    hist: pd.DataFrame,
    target_date: pd.Timestamp,
) -> pd.DataFrame:
    out = _original_predict(
        race,
        hist,
        target_date,
    )

    if PUBLISH_TO_APP:
        publish_race_to_racedata(
            race,
            out,
        )
    else:
        print("App publish disabled: RaceData.json remains read-only.")

    return out


def main() -> None:
    if not PUBLISH_TO_APP:
        print("KEIBAAI_PUBLISH_RACEDATA is false; running read-only Top2/Top3 monitor.")

    topk.predict_live_race_topk = predict_live_race_topk_and_publish
    topk.main()

    if PUBLISH_TO_APP:
        print()
        print("★★★★★ KEIBAAI TOP2/TOP3 APP PUBLISH RUN COMPLETED")
        print("If a race was triggered successfully, RaceData.json now contains")
        print("aiTop2Probability and aiTop3Probability for that race.")


if __name__ == "__main__":
    main()
