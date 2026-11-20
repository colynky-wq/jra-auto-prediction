"""Prospective, immutable and research-only market-shrinkage forecasts.

At the *first* existing formal T-10 prediction, record fixed, pre-registered
probability blends. Never fit a coefficient to 2026 outcomes or backfill a
missing prospective observation. Existing formal AI and wagering remain intact.

This file deliberately has no network, model training, or betting imports.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

VERSION = "market-shrink-shadow-v1-20261121"
JST = timezone(timedelta(hours=9))
# Exploratory 25% AI is not a previously validated optimum.
# It must be assessed ONLY against future races first snapshotted on time.
AI_WEIGHTS = {"marketOnly": 0.0, "marketBlend25": 0.25,
              "marketBlend50": 0.5, "existingAI": 1.0}
FIELDS = {
    "win": ("aiProbability", "marketProbability"),
    "top2": ("aiTop2Probability", "marketTop2Probability"),
    "top3": ("aiTop3Probability", "marketTop3Probability"),
}


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def shadow_path(root, race: dict) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "market_shrink_shadow_v1" / (
        str(race["raceID"]) + ".json")


def _aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() != timedelta(hours=9):
        raise ValueError("JST offset-aware timestamp required")
    return dt


def _probability(value, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("invalid frozen pre-race probability: " + name)
    return float(value)


def build(race: dict, *, clock=lambda: datetime.now(JST)) -> dict:
    """Freeze a first-capture shadow before the formal T-10 window closes."""
    if race.get("predictionTiming") != "live_10min" or race.get("captureQuality") != "formal_10min":
        raise ValueError("formal T-10 snapshot required")
    day, rid = str(race["raceDate"]), str(race["raceID"])
    post = datetime.fromisoformat(day + "T" + str(race["startTime"])).replace(tzinfo=JST)
    captured = _aware(race["capturedAtJST"])
    now = clock().astimezone(JST)
    minutes = (post - captured).total_seconds() / 60
    if captured.date().isoformat() != day or not 8 <= minutes <= 12:
        raise ValueError("original capture outside T-12..T-8")
    if now < captured or now > post - timedelta(minutes=8):
        raise ValueError("shadow record outside prospective T-10 window")

    horses = race.get("horses")
    if not isinstance(horses, list) or len(horses) < 3:
        raise ValueError("at least three pre-race entrants required")
    numbers = [h.get("number") for h in horses]
    if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("invalid or duplicate pre-race horse numbers")

    rows = []
    for horse in horses:
        probs = {}
        for event, (ai_name, market_name) in FIELDS.items():
            ai = _probability(horse.get(ai_name), ai_name)
            market = _probability(horse.get(market_name), market_name)
            probs[event] = {
                name: round(ai * weight + market * (1 - weight), 6)
                for name, weight in AI_WEIGHTS.items()
            }
        rows.append({"number": horse["number"], "probabilitiesPct": probs})

    value = {
        "schemaVersion": 1, "version": VERSION,
        "status": "prospective_shadow_only",
        "formalRecommendationsEnabled": False, "autoBetEnabled": False,
        "stakeSizingEnabled": False, "researchOnly": True,
        "raceDate": day, "raceID": rid, "startTime": str(race["startTime"]),
        "predictionTiming": "live_10min",
        "capturedAtJST": race["capturedAtJST"],
        "recordedAtJST": now.isoformat(),
        "sourcePredictionSha256": fingerprint(race),
        "weightsOnAI": AI_WEIGHTS,
        "horses": rows,
        "interpretation": (
            "Fixed pre-race market/AI blends. The 25%-AI challenger is exploratory "
            "and has no established advantage. Evaluate on future settled records "
            "without coefficient fitting or bet recommendations."
        ),
    }
    value["recordFingerprint"] = fingerprint(value)
    return value


def _write_first(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".market-shadow-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError("immutable market shadow collision")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def record_best_effort(root, race: dict, *, first_formal_capture: bool,
                       clock=lambda: datetime.now(JST)) -> dict:
    """Never raise into the existing live prediction or betting workflows."""
    try:
        path = shadow_path(root, race)
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("sourcePredictionSha256") != fingerprint(race):
                raise ValueError("shadow differs from immutable formal prediction")
            if existing.get("recordFingerprint") != fingerprint({
                k: v for k, v in existing.items() if k != "recordFingerprint"
            }):
                raise ValueError("shadow fingerprint mismatch")
            return {"status": "already_frozen", "path": str(path)}
        if not first_formal_capture:
            # A missing first-capture record stays missing. No hindsight rescue.
            return {"status": "missing_first_capture_no_backfill", "path": None}
        saved = _write_first(path, build(race, clock=clock))
        return {"status": "prospective_frozen", "path": str(saved)}
    except Exception as exc:
        return {"status": "error_no_bet_impact", "path": None,
                "errorType": type(exc).__name__, "reason": str(exc)[:180]}
