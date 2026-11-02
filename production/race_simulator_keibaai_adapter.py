#!/usr/bin/env python3
"""Adapter from current KeibaAI JSON schema to RaceSimulator v0.1.

Current production inputs (2026-11):
- RaceData.json: `course` such as `ダ1400m`, horses use `number` / `name`.
- AppResearchData.json: top-level `races`; per-horse `styleTendency` and
  `initialPositionHistory`; race-level `formation.paceForecast` may be withheld.

The adapter deliberately does not feed AI probability, market probability, odds, or
popularity into race motion. `aiProbability` is copied only after simulation for a
post-hoc disagreement report.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from race_simulator_v01 import HorseInput, simulate_race


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _select_race(data: Any, race_id: str) -> Mapping[str, Any]:
    rows = data.get("races") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("expected a race list or an object with `races`")
    for row in rows:
        if isinstance(row, dict) and str(row.get("raceID", "")) == race_id:
            return row
    raise KeyError(f"raceID not found: {race_id}")


def _distance_from_course(race: Mapping[str, Any]) -> float:
    value = race.get("distance", race.get("course"))
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    text = str(value or "")
    chunks = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in text).split()
    if not chunks:
        raise ValueError(f"cannot parse distance from {value!r}")
    return float(chunks[0])


def _probability01(value: Any) -> Optional[float]:
    v = _f(value)
    if v is None or v <= 0:
        return None
    if v <= 1:
        return min(1.0, v)
    if v <= 100:
        return min(1.0, v / 100.0)
    return None


def _canonical_style(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in {"逃げ", "先行", "差し", "追込"} else "不明"


def _load_strengths(data: Any, race_id: str, numbers: Sequence[int]) -> Dict[int, float]:
    if data is None:
        return {n: 1.0 for n in numbers}
    candidate = data.get(race_id, data) if isinstance(data, dict) else data
    raw: Dict[int, float] = {}
    if isinstance(candidate, dict):
        for key, value in candidate.items():
            number = _i(key)
            if number < 0:
                continue
            if isinstance(value, dict):
                value = value.get("strength", value.get("score"))
            parsed = _f(value)
            if parsed is not None:
                raw[number] = max(0.25, min(4.0, parsed))
    vals = [raw.get(n, 1.0) for n in numbers]
    mean = sum(vals) / max(len(vals), 1)
    return {n: max(0.75, min(1.25, v / mean)) for n, v in zip(numbers, vals)} if mean > 0 else {n: 1.0 for n in numbers}


def _research_horses(research_race: Optional[Mapping[str, Any]], field_size: int) -> Dict[int, Dict[str, Any]]:
    if not research_race:
        return {}
    result: Dict[int, Dict[str, Any]] = {}
    rows = research_race.get("horses", [])
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = _i(row.get("number", row.get("horseNumber")))
            if number < 0:
                continue
            tendency = row.get("styleTendency") if isinstance(row.get("styleTendency"), dict) else {}
            position = row.get("initialPositionHistory") if isinstance(row.get("initialPositionHistory"), dict) else {}
            mean_relative = _f(position.get("meanRelativePosition"))
            result[number] = {
                "style": _canonical_style(tendency.get("dominantStyle")),
                "scores": tendency.get("distributionPct", {}) if isinstance(tendency.get("distributionPct"), dict) else {},
                "sampleCount": _i(tendency.get("sampleRaces"), 0),
                "avgCorner1": None if mean_relative is None else 1.0 + max(0.0, min(1.0, mean_relative)) * max(field_size - 1, 0),
            }
    legacy = research_race.get("style", [])
    if not result and isinstance(legacy, list):
        for row in legacy:
            if not isinstance(row, dict):
                continue
            number = _i(row.get("number", row.get("horseNumber")))
            if number >= 0:
                result[number] = {
                    "style": _canonical_style(row.get("style")),
                    "scores": row.get("scores", {}) if isinstance(row.get("scores"), dict) else {},
                    "sampleCount": _i(row.get("sampleCount"), 0),
                    "avgCorner1": _f(row.get("avgCorner1")),
                }
    return result


def _pace(research_race: Optional[Mapping[str, Any]]) -> Tuple[str, str, Dict[str, Any]]:
    formation = research_race.get("formation", {}) if isinstance(research_race, Mapping) else {}
    if not isinstance(formation, Mapping):
        formation = {}
    explicit = formation.get("paceRaw", formation.get("pace"))
    if explicit:
        text = str(explicit).upper()
        pace = "FAST" if text in {"H", "HIGH", "FAST"} else "SLOW" if text in {"S", "SLOW"} else "MID"
        return pace, "research-explicit", {"formationStatus": formation.get("status"), "formationCoveragePct": _f(formation.get("coveragePct")), "paceForecastStatus": "explicit"}
    forecast = formation.get("paceForecast", {})
    if isinstance(forecast, Mapping):
        status = str(forecast.get("status", "")).lower()
        value = forecast.get("paceRaw", forecast.get("pace"))
        if status in {"available", "validated", "ok"} and value:
            text = str(value).upper()
            pace = "FAST" if text in {"H", "HIGH", "FAST"} else "SLOW" if text in {"S", "SLOW"} else "MID"
            return pace, "research-pace-forecast", {"formationStatus": formation.get("status"), "formationCoveragePct": _f(formation.get("coveragePct")), "paceForecastStatus": forecast.get("status")}
        forecast_status = forecast.get("status")
    else:
        forecast_status = None
    return "MID", "neutral-mid-fallback", {"formationStatus": formation.get("status"), "formationCoveragePct": _f(formation.get("coveragePct")), "paceForecastStatus": forecast_status}


def _posthoc_comparison(horses: Sequence[HorseInput], sim_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    probs = {h.number: h.prediction_ai_probability for h in horses if h.prediction_ai_probability is not None}
    if not probs:
        return {"available": False, "reason": "aiProbability missing or zero"}
    total = sum(probs.values())
    normalized = {n: p / total for n, p in probs.items()} if total > 0 else {}
    rows: List[Dict[str, Any]] = []
    for sim in sim_rows:
        n = int(sim["horseNumber"])
        if n not in normalized:
            continue
        sim_p = float(sim["simulationWinProbability"])
        delta = sim_p - normalized[n]
        label = "AGREE" if abs(delta) <= 0.025 else "SIMULATOR_HIGHER" if delta > 0 else "PREDICTOR_HIGHER"
        rows.append({"horseNumber": n, "horseName": sim["horseName"], "predictionAIWinProbabilityNormalized": round(normalized[n], 6), "simulationWinProbability": round(sim_p, 6), "delta": round(delta, 6), "label": label})
    rows.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {"available": True, "note": "Post-hoc only; prediction AI never enters race motion.", "horses": rows}


def simulate_current_keibaai(race_data: Any, research_data: Any, race_id: str, strengths_data: Any = None, runs: int = 1000, seed: int = 20261103, dt: float = 0.5) -> Dict[str, Any]:
    race = _select_race(race_data, race_id)
    research_race = _select_race(research_data, race_id) if research_data is not None else None
    race_horses = race.get("horses", [])
    if not isinstance(race_horses, list) or len(race_horses) < 2:
        raise ValueError("race has fewer than two horses")
    numbers = [_i(row.get("number", row.get("horseNumber"))) for row in race_horses if isinstance(row, dict)]
    if len(numbers) != len(race_horses) or any(n < 0 for n in numbers):
        raise ValueError("invalid horse numbers")
    research_by_no = _research_horses(research_race, len(numbers))
    strengths = _load_strengths(strengths_data, race_id, numbers)
    horses: List[HorseInput] = []
    for row in race_horses:
        n = _i(row.get("number", row.get("horseNumber")))
        info = research_by_no.get(n, {})
        scores = info.get("scores", {})
        horses.append(HorseInput(
            number=n,
            name=str(row.get("name", row.get("horseName", f"#{n}"))),
            style=str(info.get("style", "不明")),
            style_scores={str(k): float(v) for k, v in scores.items() if _f(v) is not None},
            sample_count=_i(info.get("sampleCount"), 0),
            avg_corner1=_f(info.get("avgCorner1")),
            avg_corner4=None,
            independent_strength=strengths[n],
            prediction_ai_probability=_probability01(row.get("aiProbability")),
        ))
    pace, pace_source, formation_quality = _pace(research_race)
    distance = _distance_from_course(race)
    sim = simulate_race(horses, distance, pace=pace, runs=runs, seed=seed, dt=dt)
    known = sum(1 for h in horses if h.style != "不明")
    return {
        "schemaVersion": "race-simulator-v0.1-current-keibaai",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raceID": race_id,
        "raceName": race.get("raceName"),
        "racecourse": race.get("venue", race.get("racecourse")),
        "track": race.get("course", race.get("track")),
        "distanceMeters": distance,
        "pace": pace,
        "paceSource": pace_source,
        "inputQuality": {**formation_quality, "styleKnownHorses": known, "styleTotalHorses": len(horses), "styleCoveragePct": round(100.0 * known / len(horses), 1)},
        "independence": {"usesPredictionAIForMotion": False, "usesMarketProbabilityForMotion": False, "usesOddsForMotion": False, "strengthSource": "external-independent" if strengths_data is not None else "neutral-equal-strength"},
        "settings": sim["settings"],
        "horses": sim["horses"],
        "comparisonWithPredictionAI": _posthoc_comparison(horses, sim["horses"]),
    }


def _load(path: Optional[Path]) -> Any:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp, path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RaceSimulator adapter for current KeibaAI JSON")
    parser.add_argument("--race-data", type=Path, required=True)
    parser.add_argument("--research-data", type=Path, required=True)
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--strengths-json", type=Path)
    parser.add_argument("--runs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--dt", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("RaceSimulationData.json"))
    args = parser.parse_args(argv)
    payload = simulate_current_keibaai(_load(args.race_data), _load(args.research_data), args.race_id, _load(args.strengths_json), args.runs, args.seed, args.dt)
    _write(args.output, payload)
    print(json.dumps({"status": "ok", "raceID": args.race_id, "output": str(args.output), "paceSource": payload["paceSource"], "strengthSource": payload["independence"]["strengthSource"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
