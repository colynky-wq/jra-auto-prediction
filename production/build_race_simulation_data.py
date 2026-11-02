#!/usr/bin/env python3
"""Build RaceSimulationData.json from current KeibaAI race data.

The batch publisher wraps the canonical RaceSimulator v0.1 adapter. Motion remains
independent from prediction AI, market probabilities, odds and popularity. Existing
AI values are present only in the adapter's post-hoc comparison section.

By default only the latest raceDate is simulated. This keeps the daily app refresh
fast while preserving --all-dates for explicit backfills/research.

Malformed race metadata is repaired only from a source-controlled, externally
verified sidecar. A repair can change course/distance metadata, but never horse
strength, prediction AI values, market values, odds, popularity, or race motion
parameters beyond the corrected course/distance itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from race_simulator_keibaai_adapter import simulate_current_keibaai

SCHEMA_VERSION = "race-simulator-v0.1-batch"
DEFAULT_METADATA_OVERRIDES = Path(__file__).with_name("race_metadata_overrides.json")


def _load(path: Optional[Path]) -> Any:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _rows(data: Any) -> List[Mapping[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(data, Mapping) and isinstance(data.get("races"), list):
        return [row for row in data["races"] if isinstance(row, Mapping)]
    raise ValueError("expected a race list or an object containing races")


def _file_sha256(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stable_seed(base_seed: int, race_id: str) -> int:
    digest = hashlib.sha256(race_id.encode("utf-8")).hexdigest()
    return base_seed + (int(digest[:8], 16) % 1_000_000)


def _select_target_races(rows: List[Mapping[str, Any]], all_dates: bool) -> Tuple[List[Mapping[str, Any]], Optional[str]]:
    dates = sorted({str(row.get("raceDate")) for row in rows if row.get("raceDate")})
    if all_dates or not dates:
        return rows, None if not dates else dates[-1]
    latest = dates[-1]
    return [row for row in rows if str(row.get("raceDate")) == latest], latest


def _plausible_distance(row: Mapping[str, Any]) -> Optional[float]:
    for value in (row.get("distanceMeters"), row.get("course"), row.get("distance"), row.get("track")):
        if isinstance(value, (int, float)):
            parsed = float(value)
            if 600 <= parsed <= 5000:
                return parsed
            continue
        text = str(value or "")
        chunks = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in text).split()
        for chunk in chunks:
            try:
                parsed = float(chunk)
            except ValueError:
                continue
            if 600 <= parsed <= 5000:
                return parsed
    return None


def _override_map(data: Any) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        return {}
    rows = data.get("races", data)
    if not isinstance(rows, Mapping):
        return {}
    return {str(key): value for key, value in rows.items() if isinstance(value, Mapping)}


def _repair_race_metadata(
    race_data: Any,
    metadata_overrides: Any,
) -> Tuple[Any, List[Dict[str, Any]]]:
    """Apply verified repairs only when the source row lacks a plausible distance."""
    source_rows = _rows(race_data)
    overrides = _override_map(metadata_overrides)
    repaired_rows: List[Dict[str, Any]] = []
    applied: List[Dict[str, Any]] = []

    for original in source_rows:
        row = dict(original)
        race_id = str(row.get("raceID", ""))
        override = overrides.get(race_id)
        if override is not None and _plausible_distance(row) is None:
            course = override.get("course")
            distance = override.get("distanceMeters")
            if course:
                row["course"] = str(course)
            try:
                parsed_distance = float(distance) if distance is not None else None
            except (TypeError, ValueError):
                parsed_distance = None
            if parsed_distance is not None and 600 <= parsed_distance <= 5000:
                row["distanceMeters"] = parsed_distance
            if _plausible_distance(row) is not None:
                applied.append({
                    "raceID": race_id,
                    "course": row.get("course"),
                    "distanceMeters": _plausible_distance(row),
                    "source": override.get("source"),
                    "verifiedDate": override.get("verifiedDate"),
                    "reason": override.get("reason"),
                })
        repaired_rows.append(row)

    if isinstance(race_data, Mapping):
        repaired_container = dict(race_data)
        repaired_container["races"] = repaired_rows
        return repaired_container, applied
    return repaired_rows, applied


def build_batch(
    race_data: Any,
    research_data: Any,
    strengths_data: Any = None,
    metadata_overrides: Any = None,
    *,
    runs: int = 400,
    seed: int = 20261103,
    dt: float = 0.5,
    all_dates: bool = False,
) -> Dict[str, Any]:
    effective_race_data, metadata_repairs = _repair_race_metadata(race_data, metadata_overrides)
    source_races = _rows(effective_race_data)
    races, target_date = _select_target_races(source_races, all_dates)
    research_rows = _rows(research_data) if research_data is not None else []
    research_by_id = {str(row.get("raceID", "")): row for row in research_rows if row.get("raceID")}

    output: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []

    for race in races:
        race_id = str(race.get("raceID", ""))
        if not race_id:
            errors.append({"raceID": "", "reason": "missing raceID"})
            continue

        course = str(race.get("course", race.get("track", "")))
        race_name = str(race.get("raceName", ""))
        if "障" in course or "障害" in race_name:
            skipped.append({"raceID": race_id, "reason": "obstacle race is outside current simulator scope"})
            continue

        research_row = research_by_id.get(race_id)
        research_payload = [research_row] if research_row is not None else None
        try:
            result = simulate_current_keibaai(
                effective_race_data,
                research_payload,
                race_id,
                strengths_data,
                runs=runs,
                seed=_stable_seed(seed, race_id),
                dt=dt,
            )
            result["raceDate"] = race.get("raceDate")
            result["raceNumber"] = race.get("raceNumber")
            result["startTime"] = race.get("startTime")
            result["venue"] = race.get("venue")
            output.append(result)
        except Exception as exc:  # keep other races publishable
            errors.append({"raceID": race_id, "reason": f"{type(exc).__name__}: {exc}"})

    output.sort(key=lambda row: (str(row.get("raceDate") or ""), str(row.get("venue") or ""), int(row.get("raceNumber") or 0), str(row.get("raceID") or "")))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ok" if output and not errors else ("partial" if output else "failed"),
        "targetRaceDate": target_date,
        "settings": {"runsPerRace": runs, "baseSeed": seed, "dtSeconds": dt, "allDates": all_dates},
        "summary": {
            "sourceRaces": len(source_races),
            "inputRaces": len(races),
            "simulatedRaces": len(output),
            "skippedRaces": len(skipped),
            "errorRaces": len(errors),
            "metadataRepairs": len(metadata_repairs),
        },
        "metadataRepairs": metadata_repairs,
        "races": output,
        "skipped": skipped,
        "errors": errors,
    }


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
    parser = argparse.ArgumentParser(description="Build current KeibaAI RaceSimulator outputs")
    parser.add_argument("--race-data", type=Path, default=Path("RaceData.json"))
    parser.add_argument("--research-data", type=Path, default=Path("AppResearchData.json"))
    parser.add_argument("--strengths-json", type=Path)
    parser.add_argument("--metadata-overrides", type=Path, default=DEFAULT_METADATA_OVERRIDES)
    parser.add_argument("--runs", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20261103)
    parser.add_argument("--dt", type=float, default=0.5)
    parser.add_argument("--all-dates", action="store_true", help="simulate every raceDate instead of latest only")
    parser.add_argument("--output", type=Path, default=Path("RaceSimulationData.json"))
    args = parser.parse_args(argv)

    metadata_overrides = _load(args.metadata_overrides) if args.metadata_overrides and args.metadata_overrides.exists() else None
    payload = build_batch(
        _load(args.race_data),
        _load(args.research_data),
        _load(args.strengths_json),
        metadata_overrides,
        runs=args.runs,
        seed=args.seed,
        dt=args.dt,
        all_dates=args.all_dates,
    )
    payload["sourceSHA256"] = {
        "RaceData.json": _file_sha256(args.race_data),
        "AppResearchData.json": _file_sha256(args.research_data),
        "strengths": _file_sha256(args.strengths_json),
        "metadataOverrides": _file_sha256(args.metadata_overrides),
    }
    _write(args.output, payload)
    print(json.dumps({"status": payload["status"], "targetRaceDate": payload["targetRaceDate"], **payload["summary"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["races"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
