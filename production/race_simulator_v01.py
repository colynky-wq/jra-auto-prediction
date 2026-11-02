#!/usr/bin/env python3
"""RaceSimulator v0.1

Independent race-shape simulator for KeibaAI.

Design rule:
- Motion simulation never consumes aiProbability, marketProbability, odds, or popularity.
- Existing-AI probability is read only after simulation for post-hoc disagreement reporting.
- Optional independent strengths can be supplied from a separate model (for example the
  market-independent C-model) without coupling the two prediction paths.

The engine intentionally uses only Python's standard library so it can run in the
existing collection/research environment without adding a runtime dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "race-simulator-v0.1"
CHECKPOINTS: Tuple[float, ...] = (0.25, 0.50, 0.75, 0.90)
STYLE_NAMES: Tuple[str, ...] = ("逃げ", "先行", "差し", "追込")

STYLE_PARAMS: Dict[str, Dict[str, float]] = {
    "逃げ": {"early": 1.055, "middle": 1.018, "turn": 0.995, "late": 0.970, "kick": 0.975, "aggression": 1.12},
    "先行": {"early": 1.030, "middle": 1.012, "turn": 1.005, "late": 0.990, "kick": 0.995, "aggression": 1.06},
    "差し": {"early": 0.985, "middle": 0.995, "turn": 1.010, "late": 1.035, "kick": 1.045, "aggression": 0.98},
    "追込": {"early": 0.955, "middle": 0.985, "turn": 1.015, "late": 1.060, "kick": 1.075, "aggression": 0.93},
    "不明": {"early": 1.000, "middle": 1.000, "turn": 1.000, "late": 1.000, "kick": 1.000, "aggression": 1.00},
}
PACE_EARLY = {"SLOW": 0.986, "MID": 1.000, "FAST": 1.018}
PACE_FATIGUE = {"SLOW": 0.96, "MID": 1.00, "FAST": 1.08}


@dataclass(frozen=True)
class HorseInput:
    number: int
    name: str
    style: str = "不明"
    style_scores: Mapping[str, float] = field(default_factory=dict)
    sample_count: int = 0
    avg_corner1: Optional[float] = None
    avg_corner4: Optional[float] = None
    independent_strength: float = 1.0
    prediction_ai_probability: Optional[float] = None


@dataclass
class HorseState:
    horse: HorseInput
    position_m: float
    speed_mps: float
    stamina: float
    lane: float
    finished_time_s: Optional[float] = None
    blocked_steps: int = 0
    lane_changes: int = 0


@dataclass(frozen=True)
class RunResult:
    order: Tuple[int, ...]
    finish_times: Mapping[int, float]
    checkpoint_ranks: Mapping[float, Mapping[int, int]]
    blocked_steps: Mapping[int, int]
    lane_changes: Mapping[int, int]


def _canonical_style(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        "逃": "逃げ", "逃げ": "逃げ", "front-runner": "逃げ", "leader": "逃げ",
        "先": "先行", "先行": "先行", "front": "先行", "stalker": "先行",
        "差": "差し", "差し": "差し", "stalk": "差し", "closer": "差し",
        "追": "追込", "追込": "追込", "追い込み": "追込", "close": "追込", "deep-closer": "追込",
    }
    return aliases.get(text, text if text in STYLE_PARAMS else "不明")


def _canonical_pace(value: Any) -> str:
    text = str(value or "MID").upper().strip()
    if text in {"H", "HIGH", "FAST", "ハイ", "ハイペース"}:
        return "FAST"
    if text in {"S", "SLOW", "スロー", "スローペース"}:
        return "SLOW"
    return "MID"


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _distance_m(value: Any) -> float:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    text = "" if value is None else str(value)
    digits = "".join(ch if (ch.isdigit() or ch == ".") else " " for ch in text).split()
    if not digits:
        raise ValueError(f"distance could not be parsed: {value!r}")
    distance = float(digits[0])
    if distance <= 0:
        raise ValueError(f"distance must be > 0: {value!r}")
    return distance


def _nominal_speed(distance_m: float) -> float:
    if distance_m <= 1200:
        return 17.20
    if distance_m <= 1600:
        return 17.00
    if distance_m <= 2000:
        return 16.75
    if distance_m <= 2600:
        return 16.45
    return 16.15


def _phase(progress: float) -> str:
    if progress < 0.20:
        return "early"
    if progress < 0.65:
        return "middle"
    if progress < 0.82:
        return "turn"
    return "late"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = (len(ordered) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(ordered[lo])
    fraction = idx - lo
    return float(ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction)


def _probability01(value: Any) -> Optional[float]:
    v = _as_float(value)
    if v is None or v <= 0:
        return None
    if v <= 1.0:
        return _clamp(v, 0.0, 1.0)
    if v <= 100.0:
        return _clamp(v / 100.0, 0.0, 1.0)
    return None


def _normalise_strengths(raw: Mapping[int, float], horse_numbers: Iterable[int]) -> Dict[int, float]:
    numbers = list(horse_numbers)
    vals = [_clamp(float(raw.get(n, 1.0)), 0.25, 4.0) for n in numbers]
    mean = sum(vals) / max(len(vals), 1)
    if mean <= 0:
        return {n: 1.0 for n in numbers}
    return {n: _clamp(v / mean, 0.75, 1.25) for n, v in zip(numbers, vals)}


def _style_score_blend(style: str, scores: Mapping[str, float]) -> Dict[str, float]:
    base = STYLE_PARAMS[_canonical_style(style)].copy()
    parsed = {k: max(0.0, float(v)) for k, v in scores.items() if k in STYLE_NAMES and _as_float(v) is not None}
    total = sum(parsed.values())
    if total <= 0:
        return base
    confidence = max(parsed.values()) / total
    soften = _clamp((confidence - 0.25) / 0.75, 0.0, 1.0)
    for key in ("early", "middle", "turn", "late", "kick", "aggression"):
        base[key] = 1.0 + (base[key] - 1.0) * (0.55 + 0.45 * soften)
    return base


def _initial_lane(index: int, field_size: int) -> float:
    if field_size <= 1:
        return 2.0
    return 5.0 * index / (field_size - 1)


def _lane_is_open(candidate: float, state: HorseState, states: Sequence[HorseState]) -> bool:
    if candidate < 0.0 or candidate > 5.0:
        return False
    for other in states:
        if other.horse.number == state.horse.number or other.finished_time_s is not None:
            continue
        if abs(other.position_m - state.position_m) < 2.4 and abs(other.lane - candidate) < 0.55:
            return False
    return True


def _front_blocker(state: HorseState, states: Sequence[HorseState]) -> Optional[HorseState]:
    candidates: List[Tuple[float, HorseState]] = []
    for other in states:
        if other.horse.number == state.horse.number or other.finished_time_s is not None:
            continue
        gap = other.position_m - state.position_m
        if 0.0 < gap < 3.4 and abs(other.lane - state.lane) < 0.58:
            candidates.append((gap, other))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _tactical_rank_bias(horse: HorseInput, states: Sequence[HorseState], progress: float) -> float:
    target = horse.avg_corner1 if progress < 0.55 else horse.avg_corner4
    if target is None or target <= 0:
        return 0.0
    ordered = sorted(states, key=lambda s: s.position_m, reverse=True)
    current_rank = next((idx + 1 for idx, s in enumerate(ordered) if s.horse.number == horse.number), len(ordered))
    return _clamp((current_rank - target) * 0.0035, -0.018, 0.018)


def _one_run(horses: Sequence[HorseInput], distance_m: float, pace: str, seed: int, dt: float) -> RunResult:
    rng = random.Random(seed)
    nominal = _nominal_speed(distance_m)
    field_size = len(horses)
    expected_seconds = distance_m / nominal
    max_seconds = expected_seconds * 1.70 + 25.0

    states: List[HorseState] = []
    for index, horse in enumerate(sorted(horses, key=lambda h: h.number)):
        reaction_delay = _clamp(rng.gauss(0.15, 0.10), 0.0, 0.45)
        style = _style_score_blend(horse.style, horse.style_scores)
        start_speed = nominal * (0.83 + 0.04 * (style["early"] - 1.0) / 0.055)
        states.append(HorseState(
            horse=horse,
            position_m=-reaction_delay * start_speed,
            speed_mps=max(8.0, start_speed * rng.uniform(0.985, 1.015)),
            stamina=1.0,
            lane=_initial_lane(index, field_size),
        ))

    checkpoint_ranks: Dict[float, Dict[int, int]] = {}
    time_s = 0.0
    pace_key = _canonical_pace(pace)

    while time_s < max_seconds and any(s.finished_time_s is None for s in states):
        active = [s for s in states if s.finished_time_s is None]
        if not active:
            break
        leader_pos = max(s.position_m for s in active)
        global_progress = _clamp(leader_pos / distance_m, 0.0, 1.0)
        for cp in CHECKPOINTS:
            if cp not in checkpoint_ranks and global_progress >= cp:
                ranked = sorted(states, key=lambda s: s.position_m, reverse=True)
                checkpoint_ranks[cp] = {s.horse.number: idx + 1 for idx, s in enumerate(ranked)}

        updates: Dict[int, Tuple[float, float, float, float, bool]] = {}
        for state in active:
            horse = state.horse
            progress = _clamp(state.position_m / distance_m, 0.0, 1.0)
            phase = _phase(progress)
            style = _style_score_blend(horse.style, horse.style_scores)
            strength_effect = 1.0 + 0.115 * math.tanh((horse.independent_strength - 1.0) * 3.5)
            target_speed = nominal * strength_effect * style[phase]
            if phase == "early":
                target_speed *= PACE_EARLY[pace_key]
            elif phase == "late":
                target_speed *= 1.0 - 0.017 * (PACE_FATIGUE[pace_key] - 1.0) / 0.08
            target_speed *= 1.0 + _tactical_rank_bias(horse, states, progress)
            if progress >= 0.74:
                kick_room = _clamp((state.stamina - 0.18) / 0.58, 0.0, 1.0)
                target_speed *= 1.0 + (style["kick"] - 1.0) * kick_room
            if state.stamina < 0.30:
                target_speed *= 0.91 + 0.30 * state.stamina

            blocker = _front_blocker(state, states)
            blocked = False
            lane = state.lane
            if blocker is not None:
                open_lane = next((c for c in (state.lane + 0.82, state.lane - 0.82) if _lane_is_open(c, state, states)), None)
                if open_lane is not None and rng.random() < 0.58 + 0.18 * (1.0 - progress):
                    lane = open_lane
                else:
                    blocked = True
                    target_speed = min(target_speed, max(7.0, blocker.speed_mps - 0.10))

            accel_noise = rng.gauss(0.0, 0.16 if phase != "late" else 0.20)
            desired_accel = _clamp((target_speed - state.speed_mps) * 0.72 + accel_noise, -1.65, 1.45)
            next_speed = _clamp(state.speed_mps + desired_accel * dt, 7.0, 20.7)
            intensity = next_speed / nominal
            base_drain = dt / max(expected_seconds, 1.0) * 0.70
            extra = max(0.0, intensity - 0.965)
            drain = base_drain * (1.0 + 1.35 * extra + 0.22 * max(0.0, style["aggression"] - 1.0))
            if phase == "early":
                drain *= PACE_FATIGUE[pace_key]
            next_stamina = _clamp(state.stamina - drain, 0.0, 1.0)
            next_position = state.position_m + 0.5 * (state.speed_mps + next_speed) * dt
            updates[horse.number] = (next_speed, next_stamina, next_position, lane, blocked)

        for state in active:
            next_speed, next_stamina, next_position, next_lane, blocked = updates[state.horse.number]
            previous_position = state.position_m
            if abs(next_lane - state.lane) > 0.2:
                state.lane_changes += 1
            if blocked:
                state.blocked_steps += 1
            state.speed_mps = next_speed
            state.stamina = next_stamina
            state.lane = next_lane
            state.position_m = next_position
            if previous_position < distance_m <= next_position:
                fraction = (distance_m - previous_position) / max(next_position - previous_position, 1e-9)
                state.finished_time_s = time_s + dt * fraction
                state.position_m = distance_m
        time_s += dt

    ranked_final_position = sorted(states, key=lambda s: (s.finished_time_s is not None, s.position_m), reverse=True)
    for cp in CHECKPOINTS:
        if cp not in checkpoint_ranks:
            checkpoint_ranks[cp] = {s.horse.number: idx + 1 for idx, s in enumerate(ranked_final_position)}

    unfinished_penalty = max_seconds + 60.0
    finish_times = {
        s.horse.number: (s.finished_time_s if s.finished_time_s is not None else unfinished_penalty - s.position_m / max(nominal, 1.0))
        for s in states
    }
    order = tuple(number for number, _ in sorted(finish_times.items(), key=lambda item: item[1]))
    return RunResult(
        order=order,
        finish_times=finish_times,
        checkpoint_ranks=checkpoint_ranks,
        blocked_steps={s.horse.number: s.blocked_steps for s in states},
        lane_changes={s.horse.number: s.lane_changes for s in states},
    )


def simulate_race(horses: Sequence[HorseInput], distance_m: float, pace: str = "MID", runs: int = 1000, seed: int = 20261103, dt: float = 0.5) -> Dict[str, Any]:
    if len(horses) < 2:
        raise ValueError("at least two horses are required")
    if distance_m <= 0:
        raise ValueError("distance_m must be > 0")
    if runs < 1:
        raise ValueError("runs must be >= 1")
    if not 0.1 <= dt <= 2.0:
        raise ValueError("dt must be between 0.1 and 2.0 seconds")
    numbers = [h.number for h in horses]
    if len(set(numbers)) != len(numbers):
        raise ValueError("horse numbers must be unique")

    win = {n: 0 for n in numbers}; top2 = {n: 0 for n in numbers}; top3 = {n: 0 for n in numbers}
    finish_ranks: Dict[int, List[int]] = {n: [] for n in numbers}
    finish_times: Dict[int, List[float]] = {n: [] for n in numbers}
    checkpoint_ranks: Dict[int, Dict[float, List[int]]] = {n: {cp: [] for cp in CHECKPOINTS} for n in numbers}
    blocked: Dict[int, List[int]] = {n: [] for n in numbers}; lane_changes: Dict[int, List[int]] = {n: [] for n in numbers}

    for run_idx in range(runs):
        result = _one_run(horses, distance_m, pace, seed + run_idx * 7919, dt)
        for rank, number in enumerate(result.order, start=1):
            finish_ranks[number].append(rank); finish_times[number].append(float(result.finish_times[number]))
            if rank == 1: win[number] += 1
            if rank <= 2: top2[number] += 1
            if rank <= 3: top3[number] += 1
        for cp in CHECKPOINTS:
            for number in numbers:
                checkpoint_ranks[number][cp].append(int(result.checkpoint_ranks[cp][number]))
        for number in numbers:
            blocked[number].append(int(result.blocked_steps[number])); lane_changes[number].append(int(result.lane_changes[number]))

    horse_by_number = {h.number: h for h in horses}
    rows: List[Dict[str, Any]] = []
    for number in numbers:
        horse = horse_by_number[number]
        rank_bands = []
        for cp in CHECKPOINTS:
            values = checkpoint_ranks[number][cp]
            rank_bands.append({"progress": cp, "p20Rank": round(_quantile(values, 0.20), 2), "medianRank": round(_quantile(values, 0.50), 2), "p80Rank": round(_quantile(values, 0.80), 2)})
        values = finish_ranks[number]
        rank_bands.append({"progress": 1.0, "p20Rank": round(_quantile(values, 0.20), 2), "medianRank": round(_quantile(values, 0.50), 2), "p80Rank": round(_quantile(values, 0.80), 2)})
        rows.append({
            "horseNumber": number, "horseName": horse.name, "style": horse.style,
            "independentStrength": round(horse.independent_strength, 6),
            "simulationWinProbability": round(win[number] / runs, 6),
            "simulationTop2Probability": round(top2[number] / runs, 6),
            "simulationTop3Probability": round(top3[number] / runs, 6),
            "expectedFinish": round(sum(values) / len(values), 3),
            "earlyLeadProbability": round(sum(1 for r in checkpoint_ranks[number][0.25] if r == 1) / runs, 6),
            "top3At75Probability": round(sum(1 for r in checkpoint_ranks[number][0.75] if r <= 3) / runs, 6),
            "averageFinishTimeSeconds": round(sum(finish_times[number]) / len(finish_times[number]), 3),
            "averageBlockedSeconds": round(sum(blocked[number]) / len(blocked[number]) * dt, 3),
            "averageLaneChanges": round(sum(lane_changes[number]) / len(lane_changes[number]), 3),
            "rankBands": rank_bands,
        })
    rows.sort(key=lambda row: (-row["simulationWinProbability"], row["expectedFinish"], row["horseNumber"]))
    return {"schemaVersion": SCHEMA_VERSION, "distanceMeters": distance_m, "pace": _canonical_pace(pace), "settings": {"runs": runs, "seed": seed, "dtSeconds": dt}, "horses": rows}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False); fh.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try: os.unlink(temp_path)
        except OSError: pass
        raise


def _select_race(data: Any, race_id: str) -> Dict[str, Any]:
    if isinstance(data, dict):
        if str(data.get("raceID", "")) == race_id:
            return data
        for key in ("races", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]; break
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and str(row.get("raceID", "")) == race_id:
                return row
    raise KeyError(f"raceID not found: {race_id}")


def _select_research(data: Any, race_id: str) -> Optional[Dict[str, Any]]:
    try: return _select_race(data, race_id)
    except KeyError: return None


def _read_strengths(data: Any, race_id: str, horse_numbers: Iterable[int]) -> Dict[int, float]:
    if data is None:
        return {n: 1.0 for n in horse_numbers}
    candidate: Any = data
    if isinstance(data, dict) and race_id in data:
        candidate = data[race_id]
    elif isinstance(data, dict) and "races" in data and isinstance(data["races"], dict):
        candidate = data["races"].get(race_id, data)
    raw: Dict[int, float] = {}
    if isinstance(candidate, dict):
        for key, value in candidate.items():
            try: number = int(key)
            except (TypeError, ValueError): continue
            parsed = _as_float(value.get("strength", value.get("score"))) if isinstance(value, dict) else _as_float(value)
            if parsed is not None: raw[number] = parsed
    elif isinstance(candidate, list):
        for row in candidate:
            if not isinstance(row, dict): continue
            number = _as_int(row.get("horseNumber", row.get("number")), -1)
            value = _as_float(row.get("strength", row.get("score")))
            if number >= 0 and value is not None: raw[number] = value
    return _normalise_strengths(raw, horse_numbers)


def _style_lookup(research: Optional[Mapping[str, Any]]) -> Dict[int, Mapping[str, Any]]:
    if not research: return {}
    rows = research.get("style", [])
    if not isinstance(rows, list): return {}
    result: Dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict): continue
        number = _as_int(row.get("number", row.get("horseNumber")), -1)
        if number >= 0: result[number] = row
    return result


def _build_inputs(race: Mapping[str, Any], research: Optional[Mapping[str, Any]], strengths_data: Any) -> Tuple[List[HorseInput], str, str]:
    horse_rows = race.get("horses")
    if not isinstance(horse_rows, list) or len(horse_rows) < 2:
        raise ValueError("race.horses must contain at least two horses")
    numbers = [_as_int(row.get("horseNumber", row.get("number")), -1) for row in horse_rows if isinstance(row, dict)]
    if any(n < 0 for n in numbers): raise ValueError("every horse requires horseNumber/number")
    race_id = str(race.get("raceID", "")); strengths = _read_strengths(strengths_data, race_id, numbers); style_rows = _style_lookup(research)
    formation = research.get("formation", {}) if isinstance(research, Mapping) else {}
    pace = _canonical_pace(formation.get("paceRaw", formation.get("pace", "MID")) if isinstance(formation, Mapping) else "MID")
    inputs: List[HorseInput] = []
    for row in horse_rows:
        if not isinstance(row, dict): continue
        number = _as_int(row.get("horseNumber", row.get("number")), -1); style_row = style_rows.get(number, {})
        scores = style_row.get("scores", {}) if isinstance(style_row.get("scores", {}), dict) else {}
        inputs.append(HorseInput(
            number=number, name=str(row.get("horseName", row.get("name", f"#{number}"))),
            style=_canonical_style(style_row.get("style", "不明")),
            style_scores={str(k): float(v) for k, v in scores.items() if _as_float(v) is not None},
            sample_count=_as_int(style_row.get("sampleCount"), 0), avg_corner1=_as_float(style_row.get("avgCorner1")), avg_corner4=_as_float(style_row.get("avgCorner4")),
            independent_strength=strengths.get(number, 1.0), prediction_ai_probability=_probability01(row.get("aiProbability")),
        ))
    return inputs, pace, ("external-independent" if strengths_data is not None else "neutral-equal-strength")


def _comparison_section(horses: Sequence[HorseInput], simulation_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ai = {h.number: h.prediction_ai_probability for h in horses if h.prediction_ai_probability is not None}
    if not ai:
        return {"available": False, "reason": "existing aiProbability is missing or zero"}
    total = sum(ai.values()); normalized = {n: (p / total if total > 0 else 0.0) for n, p in ai.items()}
    rows: List[Dict[str, Any]] = []
    for sim in simulation_rows:
        number = int(sim["horseNumber"])
        if number not in normalized: continue
        sim_p = float(sim["simulationWinProbability"]); ai_p = normalized[number]; delta = sim_p - ai_p
        label = "AGREE" if abs(delta) <= 0.025 else ("SIMULATOR_HIGHER" if delta > 0 else "PREDICTOR_HIGHER")
        rows.append({"horseNumber": number, "horseName": sim["horseName"], "predictionAIWinProbabilityNormalized": round(ai_p, 6), "simulationWinProbability": round(sim_p, 6), "delta": round(delta, 6), "label": label})
    rows.sort(key=lambda row: abs(row["delta"]), reverse=True)
    return {"available": True, "note": "Post-hoc comparison only. prediction AI values never enter race motion simulation.", "horses": rows}


def simulate_from_keibaai(race_data: Any, research_data: Any, race_id: str, strengths_data: Any = None, runs: int = 1000, seed: int = 20261103, dt: float = 0.5) -> Dict[str, Any]:
    race = _select_race(race_data, race_id); research = _select_research(research_data, race_id) if research_data is not None else None
    horses, pace, strength_source = _build_inputs(race, research, strengths_data); distance = _distance_m(race.get("distance"))
    simulation = simulate_race(horses, distance, pace=pace, runs=runs, seed=seed, dt=dt)
    return {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raceID": race_id, "raceName": race.get("raceName"), "racecourse": race.get("racecourse", race.get("venue")), "track": race.get("track"),
        "distanceMeters": distance, "pace": pace,
        "independence": {"usesPredictionAIForMotion": False, "usesMarketProbabilityForMotion": False, "usesOddsForMotion": False, "strengthSource": strength_source},
        "settings": simulation["settings"], "horses": simulation["horses"], "comparisonWithPredictionAI": _comparison_section(horses, simulation["horses"]),
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KeibaAI RaceSimulator v0.1")
    parser.add_argument("--race-data", required=True, type=Path, help="RaceData.json")
    parser.add_argument("--research-data", type=Path, help="AppResearchData.json (pace/style)")
    parser.add_argument("--race-id", required=True, help="raceID to simulate")
    parser.add_argument("--strengths-json", type=Path, help="Optional market-independent strength JSON")
    parser.add_argument("--runs", type=int, default=1000, help="Monte Carlo runs (default: 1000)")
    parser.add_argument("--seed", type=int, default=20261103, help="Reproducible random seed")
    parser.add_argument("--dt", type=float, default=0.5, help="Simulation time step in seconds")
    parser.add_argument("--output", type=Path, default=Path("RaceSimulationData.json"), help="Output JSON")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv); race_data = _load_json(args.race_data); research_data = _load_json(args.research_data) if args.research_data else None; strengths_data = _load_json(args.strengths_json) if args.strengths_json else None
    payload = simulate_from_keibaai(race_data, research_data, args.race_id, strengths_data, args.runs, args.seed, args.dt)
    _atomic_write_json(args.output, payload)
    print(json.dumps({"status": "ok", "raceID": args.race_id, "output": str(args.output), "runs": args.runs, "strengthSource": payload["independence"]["strengthSource"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
