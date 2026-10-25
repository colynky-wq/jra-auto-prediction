from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

import live_monitor_dry_run as base
import live_monitor_topk as topk
from live_capture_policy import (
    CAPTURE_TIMING_MARKERS,
    FORMAL_TIMING_MARKER,
    RECOVERY_TIMING_MARKER,
)

HERE = Path(__file__).resolve().parent
RACEDATA_PATH = HERE.parent / "RaceData.json"
REPORTS_PATH = HERE.parent / "reports"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


PUBLISH_TO_APP = _env_flag("KEIBAAI_PUBLISH_RACEDATA", False)
CAPTURE_WIDE_ODDS = _env_flag("KEIBAAI_CAPTURE_WIDE_ODDS", True)
REFRESH_SIGNALS = _env_flag("KEIBAAI_REFRESH_SIGNALS", True)


def _number(value, digits=3):
    return round(float(value), digits)


def _optional_text(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_number(value, digits=1):
    return None if pd.isna(value) else round(float(value), digits)


def _optional_int(value):
    return None if pd.isna(value) else int(value)


def _target_date_text():
    value = str(base.TARGET_DATE8)
    if len(value) != 8 or not value.isdigit():
        raise RuntimeError(f"invalid target date for RaceData: {value!r}")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _load_existing():
    if not RACEDATA_PATH.exists():
        return []
    payload = json.loads(RACEDATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("RaceData.json root must be a JSON array")
    return payload


def _validate_complete_live_prediction(race, horses):
    declared = int(race.get("field_size") or race.get("head_count") or race.get("num_horses") or len(horses))
    numbers = [h.get("number") for h in horses]
    if len(horses) != declared or len(set(numbers)) != len(numbers):
        raise RuntimeError(f"incomplete live entrants: expected={declared} actual={len(horses)}")
    required = (
        "number", "name", "odds", "marketProbability", "aiProbability",
        "marketTop2Probability", "aiTop2Probability",
        "marketTop3Probability", "aiTop3Probability",
    )
    for horse in horses:
        if any(horse.get(key) is None for key in required):
            raise RuntimeError(f"incomplete live horse evidence: {horse.get('number')} {horse.get('name')}")


def _refresh_prediction_bundle(published_race):
    snapshot = None
    if CAPTURE_WIDE_ODDS:
        try:
            import formal_wide_odds_runtime as wide
            snapshot = wide.record_first_wide_snapshot(
                race_id=str(published_race["raceID"]),
                race_date=str(published_race["raceDate"]),
                venue=str(published_race.get("venue") or ""),
                race_number=int(published_race.get("raceNumber") or 0),
                start_time=str(published_race.get("startTime") or ""),
            )
            print(
                "FORMAL Wide odds snapshot:", snapshot.get("raceID"), snapshot.get("capturedAtJST"),
                "minutesToPost=", snapshot.get("minutesToPost"), "pairs=", snapshot.get("pairCount"),
            )
        except Exception as exc:
            print("WARNING: formal Wide odds unavailable; recommendation MUST remain disabled:", repr(exc))
    if REFRESH_SIGNALS:
        try:
            import build_signal_data_v2 as signals
            from datetime import datetime
            import wide_axis_rules as axis
            current = signals.build_live_race_signal(published_race, snapshot)
            if current is not None:
                saved = axis.record_first(RACEDATA_PATH.parent, published_race, current, datetime.now(axis.JST))
                print("WIDE_AXIS_RECORDED" if saved else "WIDE_AXIS_NOT_RECORDED", saved or published_race["raceID"])
        except Exception as exc:
            print("WARNING: Wide-axis record failed; keep recommendation disabled:", repr(exc))
        try:
            import build_signal_data_v2 as signals
            signals.main()
            print("SignalData refreshed immediately after RaceData publish.")
        except Exception as exc:
            print("WARNING: SignalData refresh failed; recommendation MUST remain disabled:", repr(exc))


def _capture_timing(race: dict) -> tuple[str, float | None]:
    timing = str(race.get("_prediction_timing") or FORMAL_TIMING_MARKER)
    if timing not in CAPTURE_TIMING_MARKERS:
        raise RuntimeError(f"invalid live capture timing marker: {timing!r}")
    trigger = race.get("_trigger_minutes_to_post")
    return timing, (None if trigger is None else round(float(trigger), 3))


def _save_late_recovery(published_race: dict) -> Path:
    """Persist recovery evidence outside RaceData/SignalData formal inputs."""
    date8 = str(published_race["raceDate"]).replace("-", "")
    rid = str(published_race["raceID"])
    path = REPORTS_PATH / date8 / "late_recovery" / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(published_race, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != payload:
            raise RuntimeError(f"conflicting late recovery evidence: {path}")
        return path
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return path


def publish_race_to_racedata(race, out):
    target_date = _target_date_text()
    timing, trigger_minutes = _capture_timing(race)
    formal = timing == FORMAL_TIMING_MARKER

    horses = []
    for _, row in out.sort_values("馬番").iterrows():
        horses.append({
            "number": int(row["馬番"]),
            "name": str(row["馬名"]),
            "age": int(row["年齢"]),
            "horseKey": str(row["馬キー"]),
            "jockey": _optional_text(row.get("騎手")),
            "carriedWeight": _optional_number(row.get("斤量"), 1),
            "popularity": _optional_int(row.get("人気")),
            "bodyWeight": _optional_int(row.get("馬体重")),
            "bodyWeightChange": _optional_int(row.get("増減")),
            "odds": _number(row["単勝オッズ"], 1),
            "marketProbability": _number(row["市場勝率(%)"]),
            "aiProbability": _number(row["AI勝率(%)"]),
            "marketTop2Probability": _number(row["市場2着内率(%)"]),
            "aiTop2Probability": _number(row["AI2着内率(%)"]),
            "marketTop3Probability": _number(row["市場3着内率(%)"]),
            "aiTop3Probability": _number(row["AI3着内率(%)"]),
            "aiMarketRatio": _number(row["AI÷市場"], 4),
            "expectedIndex": _number(row["期待払戻指数"]),
        })

    _validate_complete_live_prediction(race, horses)
    captured_at = base.now_jst().isoformat()
    safety_message = (
        "全出走馬・正式10分前の対象券種実オッズ・トリガミ条件を確認するまで購入推奨を出しません。"
        if formal else
        "正式10分前窓を逃した遅延救済記録です。正式10分前研究には使用せず、購入推奨を出しません。"
    )
    published_race = {
        "raceDate": target_date,
        "raceID": str(race["race_id"]),
        "venue": str(race["venue"]),
        "raceNumber": int(race["race_no"]),
        "startTime": str(race["start_time"]),
        "raceName": str(race.get("race_name", "")),
        "course": f"{race['surface']}{int(race['distance'])}m",
        "predictionTiming": timing,
        "captureQuality": "formal_10min" if formal else "late_recovery",
        "triggerMinutesToPost": trigger_minutes,
        "capturedAtJST": captured_at,
        "recommendationSafety": {
            "status": "evidence_pending" if formal else "late_recovery_not_eligible",
            "enabled": False,
            "message": safety_message,
        },
        "horses": horses,
    }
    rid = published_race["raceID"]

    # Recovery evidence is deliberately quarantined from RaceData.json.  That
    # guarantees later SignalData rebuilds and app/research consumers cannot
    # accidentally promote a T-7/T-5 fallback into formal T-10 evidence.
    if not formal:
        path = _save_late_recovery(published_race)
        print("KEIBAAI LATE RECOVERY SAVED", rid, path, "formal app/research inputs untouched")
        return

    existing = _load_existing()
    todays = [item for item in existing if str(item.get("raceDate", "")).strip() == target_date]
    frozen = next(
        (
            item for item in todays
            if str(item.get("raceID", "")) == rid and item.get("predictionTiming") == FORMAL_TIMING_MARKER
        ),
        None,
    )
    if frozen is not None:
        published_race = frozen
        captured_at = str(frozen.get("capturedAtJST") or captured_at)
        print("KEIBAAI APP PRESERVE FIRST FORMAL LIVE", rid, captured_at)
    else:
        replaced = False
        for index, item in enumerate(todays):
            if str(item.get("raceID", "")) == rid:
                todays[index] = published_race
                replaced = True
                break
        if not replaced:
            todays.append(published_race)
        todays.sort(key=lambda item: (str(item.get("startTime", "")), str(item.get("raceID", ""))))
        tmp = RACEDATA_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(todays, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(RACEDATA_PATH)
        print(
            "KEIBAAI APP PUBLISH", rid, FORMAL_TIMING_MARKER, captured_at, "horses", len(horses),
            "recommendation disabled pending formal odds",
        )

    from triple_research_runtime import schedule as schedule_triple
    from all_bet_runtime import schedule as schedule_all_bets

    triple_future = schedule_triple(RACEDATA_PATH.parent, published_race)
    all_bet_future = schedule_all_bets(
        RACEDATA_PATH.parent,
        published_race,
        out.attrs.get("allBetEntryEvidence"),
    )
    _refresh_prediction_bundle(published_race)
    triple_future.result(timeout=120)
    all_bet_future.result(timeout=120)

    from live_recording_resume import research_sidecars_complete
    if not research_sidecars_complete(
        RACEDATA_PATH.parent,
        str(published_race["raceDate"]).replace("-", ""),
        rid,
    ):
        raise RuntimeError("formal live research sidecars incomplete")


_original_predict = topk.predict_live_race_topk


def predict_live_race_topk_and_publish(race, hist, target_date):
    out = _original_predict(race, hist, target_date)
    if PUBLISH_TO_APP:
        publish_race_to_racedata(race, out)
    else:
        print("App publish disabled: RaceData.json remains read-only.")
    return out


def main():
    if not PUBLISH_TO_APP:
        print("KEIBAAI_PUBLISH_RACEDATA is false; running read-only Top2/Top3 monitor.")
    topk.predict_live_race_topk = predict_live_race_topk_and_publish
    topk.main()
    if PUBLISH_TO_APP:
        print("★★★★★ KEIBAAI APP BUNDLE PUBLISH RUN COMPLETED")


if __name__ == "__main__":
    main()
