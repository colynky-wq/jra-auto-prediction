from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

import live_monitor_dry_run as base
import live_monitor_topk as topk


HERE = Path(__file__).resolve().parent
RACEDATA_PATH = HERE.parent / "RaceData.json"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


PUBLISH_TO_APP = _env_flag("KEIBAAI_PUBLISH_RACEDATA", False)
CAPTURE_WIDE_ODDS = _env_flag("KEIBAAI_CAPTURE_WIDE_ODDS", True)
REFRESH_SIGNALS = _env_flag("KEIBAAI_REFRESH_SIGNALS", True)


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


def _refresh_prediction_bundle(published_race: dict) -> None:
    """Best-effort post-prediction refresh.

    RaceData is always written first. Wide odds and SignalData are deliberately
    best-effort so a temporary odds/API failure can never erase or block the
    prediction that was already produced.
    """
    if CAPTURE_WIDE_ODDS:
        try:
            import wide_odds_runtime as wide

            snapshot = wide.record_first_wide_snapshot(
                race_id=str(published_race["raceID"]),
                race_date=str(published_race["raceDate"]),
                venue=str(published_race.get("venue") or ""),
                race_number=int(published_race.get("raceNumber") or 0),
                start_time=str(published_race.get("startTime") or ""),
            )
            print(
                "Wide odds snapshot:",
                snapshot.get("raceID"),
                snapshot.get("capturedAtJST"),
                "pairs=",
                snapshot.get("pairCount"),
            )
        except Exception as exc:
            print("WARNING: Wide odds snapshot failed; RaceData remains valid:", repr(exc))

    if REFRESH_SIGNALS:
        try:
            import build_signal_data_v2 as signals

            signals.main()
            print("SignalData refreshed immediately after RaceData publish.")
        except Exception as exc:
            print("WARNING: SignalData refresh failed; scheduled fallback can repair it:", repr(exc))
            return
        try:
            from datetime import datetime
            import wide_axis_rules as axis
            bundle = json.loads(signals.OUT_PATH.read_text())
            current = next((s for s in bundle['races'] if s['raceID'] == published_race['raceID']), None)
            if current is not None:
                saved = axis.record_first(RACEDATA_PATH.parent, published_race, current, datetime.now(axis.JST))
                if saved:
                    print('WIDE_AXIS_RECORDED', saved)
        except Exception as exc:
            print("WARNING: Wide-axis record failed; keep the prediction and do not backfill after post:", repr(exc))


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
                "age": int(row["年齢"]),
                "horseKey": str(row["馬キー"]),
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
        json.dump(todays, f, ensure_ascii=False, indent=2)
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

    # Separate bounded background worker; never delay Win/Top2/Top3/Wide.
    try:
        from triple_research_runtime import schedule
        schedule(RACEDATA_PATH.parent, published_race)
    except Exception as exc:
        print('WARNING: TRIPLE_RESEARCH_NOT_SCHEDULED; keep prediction:', type(exc).__name__)

    try:
        from all_bet_runtime import schedule as schedule_all_bets
        schedule_all_bets(RACEDATA_PATH.parent, published_race, out.attrs.get('allBetEntryEvidence'))
    except Exception as exc:
        print('WARNING: ALL_BET_RESEARCH_NOT_SCHEDULED; keep prediction:', type(exc).__name__)

    _refresh_prediction_bundle(published_race)

    print("Wide odds capture requested:", CAPTURE_WIDE_ODDS)
    print("Immediate signal refresh requested:", REFRESH_SIGNALS)
    print("=" * 110)


_original_predict = topk.predict_live_race_topk


def predict_live_race_topk_and_publish(
    race: dict,
    hist: pd.DataFrame,
    target_date: pd.Timestamp,
) -> pd.DataFrame:
    out = _original_predict(race, hist, target_date)

    if PUBLISH_TO_APP:
        publish_race_to_racedata(race, out)
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
        print("★★★★★ KEIBAAI APP BUNDLE PUBLISH RUN COMPLETED")
        print("Prediction publish now attempts RaceData + Wide odds snapshot + SignalData together.")


if __name__ == "__main__":
    main()
