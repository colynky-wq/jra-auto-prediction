"""Build Same-Day Track Bias Shadow V2.1 reports from RaceData + ResultData.

Shadow-only: formal 10-minute predictions and the fixed production model remain
unchanged. Evidence comes from V2; V2.1 adds conservative confidence gates before
that evidence may move challenger rankings.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from same_day_track_bias_v2 import build_same_day_bias_v2, build_style_map_from_history
from same_day_track_bias_v21 import apply_same_day_v21_adjustment, adjustment_gate_summary

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
RESULTDATA_PATH = REPO / "ResultData.json"
HISTORY_PATH = HERE / "production_history.parquet"
REPORTS_DIR = REPO / "reports"

TARGET_DATE8 = (
    os.environ.get("KEIBAAI_SAME_DAY_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)


def _date_text(date8: str) -> str:
    if len(date8) != 8 or not date8.isdigit():
        raise ValueError(f"invalid date: {date8!r}")
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"


def _load_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError(f"{path.name} root must be a list")
    return data


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def build_reports(
    races: list[dict],
    results: list[dict],
    race_date: str,
    style_map: dict[str, float] | None = None,
) -> list[dict]:
    day_races = [r for r in races if str(r.get("raceDate") or "") == race_date]
    style_map = style_map or {}
    output: list[dict] = []

    for target in sorted(
        day_races,
        key=lambda r: (str(r.get("venue") or ""), str(r.get("startTime") or "")),
    ):
        course = str(target.get("course") or "")
        if not (course.startswith("芝") or course.startswith("ダ")):
            continue
        try:
            bias = build_same_day_bias_v2(
                day_races,
                results,
                race_date=race_date,
                venue=str(target.get("venue") or ""),
                target_start_time=str(target.get("startTime") or ""),
                target_course=course,
                target_race_number=target.get("raceNumber"),
            )
        except ValueError:
            continue

        gates = adjustment_gate_summary(bias)
        bias["adjustmentPolicy"] = gates
        horses = target.get("horses") or []
        challenger = apply_same_day_v21_adjustment(horses, bias, style_map)
        output.append(
            {
                "raceDate": race_date,
                "raceID": target.get("raceID"),
                "venue": target.get("venue"),
                "raceNumber": target.get("raceNumber"),
                "startTime": target.get("startTime"),
                "raceName": target.get("raceName"),
                "course": course,
                "formalPredictionTiming": target.get("predictionTiming"),
                "formalCaptureQuality": target.get("captureQuality"),
                "sameDayBias": bias,
                "challenger": {
                    "status": "shadow_only",
                    "version": "same_day_track_bias_v2_1",
                    "meaning": "Formal A preserved; V2.1 gates weak draw/style evidence before applying bounded challenger adjustments.",
                    "horses": challenger,
                },
            }
        )
    return output


def main() -> None:
    race_date = _date_text(TARGET_DATE8)
    races = _load_list(RACEDATA_PATH)
    results = _load_list(RESULTDATA_PATH)
    style_map = build_style_map_from_history(HISTORY_PATH, race_date)
    report = build_reports(races, results, race_date, style_map)

    day_dir = REPORTS_DIR / TARGET_DATE8
    day_path = day_dir / "SameDayTrackBiasShadow.json"
    latest_path = REPO / "SameDayTrackBiasShadow.json"
    _write_json(day_path, report)
    _write_json(latest_path, report)

    usable = sum(1 for r in report if (r.get("sameDayBias") or {}).get("completedRaceCount", 0) > 0)
    gated_draw = sum(1 for r in report if ((r.get("sameDayBias") or {}).get("adjustmentPolicy") or {}).get("drawEnabled"))
    gated_style = sum(1 for r in report if ((r.get("sameDayBias") or {}).get("adjustmentPolicy") or {}).get("styleEnabled"))
    print(
        f"same-day shadow v2.1: date={race_date} races={len(report)} "
        f"prior_results={usable} draw_enabled={gated_draw} style_enabled={gated_style} "
        f"style_profiles={len(style_map)} path={day_path}"
    )


if __name__ == "__main__":
    main()
