"""Evaluate formal A ranking versus Same-Day Shadow V2 on completed races.

The report is research-only. Each shadow row was built with strictly earlier
same-day races, so evaluating it against the target race's later result does not
feed the target result back into its own challenger score.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RACEDATA_PATH = REPO / "RaceData.json"
RESULTDATA_PATH = REPO / "ResultData.json"
SHADOW_PATH = REPO / "SameDayTrackBiasShadow.json"
OUTPUT_PATH = REPO / "SameDayShadowEvaluation.json"
REPORTS_DIR = REPO / "reports"

TARGET_DATE8 = (
    os.environ.get("KEIBAAI_SAME_DAY_DATE", "").strip()
    or datetime.now(JST).strftime("%Y%m%d")
)


def _date_text(date8: str) -> str:
    if len(date8) != 8 or not date8.isdigit():
        raise ValueError(f"invalid date: {date8!r}")
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}"


def _load(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _safe_float(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _surface(course: str | None) -> str:
    text = str(course or "")
    if text.startswith("芝"):
        return "芝"
    if text.startswith("ダ"):
        return "ダート"
    return "その他"


def _rank_by_probability(horses: list[dict]) -> dict[int, int]:
    rows = []
    for h in horses:
        try:
            no = int(h.get("number"))
        except Exception:
            continue
        p = _safe_float(h.get("aiProbability"))
        if p is None:
            continue
        rows.append((no, p))
    rows.sort(key=lambda x: (-x[1], x[0]))
    return {no: i for i, (no, _) in enumerate(rows, 1)}


def _shadow_rank(horses: list[dict]) -> dict[int, int]:
    out = {}
    fallback = []
    for h in horses:
        try:
            no = int(h.get("number"))
        except Exception:
            continue
        rank = h.get("sameDayShadowRank")
        try:
            out[no] = int(rank)
            continue
        except Exception:
            pass
        score = _safe_float(h.get("sameDayShadowScore"))
        if score is not None:
            fallback.append((no, score))
    if not out and fallback:
        fallback.sort(key=lambda x: (-x[1], x[0]))
        out = {no: i for i, (no, _) in enumerate(fallback, 1)}
    return out


def _winner_number(result: dict) -> int | None:
    for h in result.get("horses") or []:
        try:
            if int(h.get("finishPosition")) == 1:
                return int(h.get("number"))
        except Exception:
            continue
    return None


def _summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {
            "races": 0,
            "formalTop1Hits": 0,
            "shadowTop1Hits": 0,
            "formalTop3Hits": 0,
            "shadowTop3Hits": 0,
            "formalTop1Rate": None,
            "shadowTop1Rate": None,
            "formalTop3Rate": None,
            "shadowTop3Rate": None,
            "formalMeanWinnerRank": None,
            "shadowMeanWinnerRank": None,
            "meanWinnerRankDeltaShadowMinusFormal": None,
            "shadowImprovedWinnerRank": 0,
            "shadowWorsenedWinnerRank": 0,
            "shadowUnchangedWinnerRank": 0,
        }
    f1 = sum(r["formalWinnerRank"] == 1 for r in rows)
    s1 = sum(r["shadowWinnerRank"] == 1 for r in rows)
    f3 = sum(r["formalWinnerRank"] <= 3 for r in rows)
    s3 = sum(r["shadowWinnerRank"] <= 3 for r in rows)
    fm = sum(r["formalWinnerRank"] for r in rows) / n
    sm = sum(r["shadowWinnerRank"] for r in rows) / n
    return {
        "races": n,
        "formalTop1Hits": f1,
        "shadowTop1Hits": s1,
        "formalTop3Hits": f3,
        "shadowTop3Hits": s3,
        "formalTop1Rate": round(f1 / n, 4),
        "shadowTop1Rate": round(s1 / n, 4),
        "formalTop3Rate": round(f3 / n, 4),
        "shadowTop3Rate": round(s3 / n, 4),
        "formalMeanWinnerRank": round(fm, 4),
        "shadowMeanWinnerRank": round(sm, 4),
        "meanWinnerRankDeltaShadowMinusFormal": round(sm - fm, 4),
        "shadowImprovedWinnerRank": sum(r["shadowWinnerRank"] < r["formalWinnerRank"] for r in rows),
        "shadowWorsenedWinnerRank": sum(r["shadowWinnerRank"] > r["formalWinnerRank"] for r in rows),
        "shadowUnchangedWinnerRank": sum(r["shadowWinnerRank"] == r["formalWinnerRank"] for r in rows),
    }


def evaluate(races: list[dict], results: list[dict], shadows: list[dict], race_date: str) -> dict:
    race_map = {
        str(r.get("raceID") or "").zfill(12): r
        for r in races
        if str(r.get("raceDate") or "") == race_date
    }
    result_map = {
        str(r.get("raceID") or "").zfill(12): r
        for r in results
        if str(r.get("raceDate") or "") == race_date
    }

    details = []
    skipped = defaultdict(int)
    for shadow in shadows:
        if str(shadow.get("raceDate") or "") != race_date:
            continue
        rid = str(shadow.get("raceID") or "").zfill(12)
        race = race_map.get(rid)
        result = result_map.get(rid)
        if not race or not result:
            skipped["missing_race_or_result"] += 1
            continue
        bias = shadow.get("sameDayBias") or {}
        if str(bias.get("version") or "") != "same_day_track_bias_v2":
            skipped["not_v2"] += 1
            continue
        prior = int(bias.get("completedRaceCount") or 0)
        if prior <= 0:
            skipped["no_prior_same_surface_race"] += 1
            continue
        if str(race.get("predictionTiming") or "") != "live_10min":
            skipped["not_formal_live_10min"] += 1
            continue
        winner = _winner_number(result)
        if winner is None:
            skipped["winner_missing"] += 1
            continue
        formal_ranks = _rank_by_probability(race.get("horses") or [])
        shadow_ranks = _shadow_rank((shadow.get("challenger") or {}).get("horses") or [])
        if winner not in formal_ranks or winner not in shadow_ranks:
            skipped["winner_rank_missing"] += 1
            continue

        fr = formal_ranks[winner]
        sr = shadow_ranks[winner]
        details.append(
            {
                "raceID": rid,
                "venue": race.get("venue"),
                "raceNumber": race.get("raceNumber"),
                "startTime": race.get("startTime"),
                "raceName": race.get("raceName"),
                "course": race.get("course"),
                "surface": _surface(race.get("course")),
                "winnerNumber": winner,
                "formalWinnerRank": fr,
                "shadowWinnerRank": sr,
                "winnerRankDeltaShadowMinusFormal": sr - fr,
                "completedPriorSameSurfaceRaces": prior,
                "detailPriorRaceCount": int(bias.get("detailRaceCount") or 0),
                "runningStyleBiasLabel": bias.get("runningStyleBiasLabel"),
                "closingSpeedBiasLabel": bias.get("closingSpeedBiasLabel"),
                "paceShapeLabel": bias.get("paceShapeLabel"),
                "formalTop1Hit": fr == 1,
                "shadowTop1Hit": sr == 1,
                "formalTop3Hit": fr <= 3,
                "shadowTop3Hit": sr <= 3,
            }
        )

    groups = defaultdict(list)
    for row in details:
        groups[f"{row['venue']}|{row['surface']}"] .append(row)

    return {
        "version": "same_day_shadow_evaluation_v1",
        "raceDate": race_date,
        "status": "research_only",
        "interpretation": "One-day or small-sample results are descriptive only; do not promote Shadow V2 into Formal A from this report alone.",
        "eligibility": "Formal live_10min target races with >=1 strictly earlier completed same-day, same-venue, same-surface race in Same-Day V2.",
        "overall": _summarize(details),
        "byVenueSurface": {key: _summarize(value) for key, value in sorted(groups.items())},
        "skipped": dict(sorted(skipped.items())),
        "races": details,
    }


def main() -> None:
    race_date = _date_text(TARGET_DATE8)
    report = evaluate(_load(RACEDATA_PATH), _load(RESULTDATA_PATH), _load(SHADOW_PATH), race_date)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    day_dir = REPORTS_DIR / TARGET_DATE8
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "SameDayShadowEvaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["overall"], ensure_ascii=False))


if __name__ == "__main__":
    main()
