"""Immutable prospective shadow records for ticket-specific probabilities.

This module never recommends or selects a bet.  At the first formal live_10min
prediction it freezes the full-precision Win/Top2/Top3 inputs and coherent
Wide/Quinella/Exacta/Trio/Trifecta probabilities.  A second immutable manifest
links the separately frozen pre-race odds evidence by SHA256 so later ROI work
cannot silently substitute hindsight odds.
"""
from __future__ import annotations

from datetime import datetime
from itertools import combinations
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np

from live_capture_policy import FORMAL_TIMING_MARKER
from ticket_probability_runtime import VERSION as TICKET_RUNTIME_VERSION, distributions
from triple_forward import JST, digest, stamp

VERSION = "ticket-shadow-forward-v1-20261031"
SCHEMA_VERSION = 1
LABEL = "🧪研究モデル"
BET_TYPES = ("wide", "quinella", "exacta", "trio", "trifecta")
HERE = Path(__file__).resolve().parent
MODEL_PATHS = {
    "formalAWin": HERE / "lightgbm_fixed_2025.txt",
    "top2": HERE / "lightgbm_top2_fixed_2025.txt",
    "top3": HERE / "lightgbm_top3_fixed_2025.txt",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _outcome_key(numbers) -> str:
    return "".join(f"{int(n):02d}" for n in numbers)


def _prediction_path(root, race) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "ticket_shadow_v1" / "prediction" / f"{race['raceID']}.json"


def _manifest_path(root, race) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "ticket_shadow_v1" / "manifest" / f"{race['raceID']}.json"


def _atomic_first(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ticket-shadow-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError(f"immutable ticket shadow conflict: {path}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _formal_identity(race: dict) -> dict:
    if race.get("predictionTiming") != FORMAL_TIMING_MARKER or race.get("captureQuality") != "formal_10min":
        raise ValueError("ticket shadow requires a formal live_10min prediction")
    captured = stamp(race.get("capturedAtJST"))
    post = stamp(str(race["raceDate"]) + "T" + str(race["startTime"]))
    if captured.date().isoformat() != race["raceDate"] or not captured < post:
        raise ValueError("formal prediction timestamp must be same-day and pre-post")
    horses = race.get("horses")
    if not isinstance(horses, list) or len(horses) < 3:
        raise ValueError("at least three formal entrants required")
    numbers = [h.get("number") for h in horses]
    if any(type(n) is not int for n in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("unique formal horse numbers required")
    return {
        "raceDate": race["raceDate"],
        "raceID": str(race["raceID"]),
        "venue": str(race.get("venue") or ""),
        "raceNumber": int(race.get("raceNumber") or 0),
        "startTime": str(race["startTime"]),
        "predictionTiming": FORMAL_TIMING_MARKER,
        "captureQuality": "formal_10min",
        "capturedAtJST": captured.isoformat(),
        "horseNumbers": numbers,
    }


def _model_hashes() -> dict:
    out = {}
    for name, path in MODEL_PATHS.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        out[name] = {"path": path.name, "sha256": _file_sha256(path)}
    runtime = Path(__file__).with_name("ticket_probability_runtime.py")
    out["ticketRuntime"] = {"version": TICKET_RUNTIME_VERSION, "sha256": _file_sha256(runtime)}
    return out


def _full_precision_inputs(race: dict, inputs: dict) -> tuple[list[int], np.ndarray, np.ndarray, np.ndarray]:
    identity = _formal_identity(race)
    if not isinstance(inputs, dict):
        raise ValueError("full-precision ticket research inputs required")
    numbers = list(inputs.get("numbers") or [])
    if numbers != identity["horseNumbers"]:
        raise ValueError("ticket research entrant order does not match formal prediction")
    arrays = []
    for key in ("win", "top2", "top3"):
        value = np.asarray(inputs.get(key), dtype=float)
        if value.shape != (len(numbers),) or not np.isfinite(value).all():
            raise ValueError(f"invalid full-precision {key} inputs")
        arrays.append(value)
    win, top2, top3 = arrays
    if (win <= 0).any() or abs(float(win.sum()) - 1.0) > 1e-8:
        raise ValueError("full-precision Win probabilities must sum to one")
    if (top2 < win - 1e-10).any() or (top3 < top2 - 1e-10).any() or (top3 > 1 + 1e-10).any():
        raise ValueError("full-precision marginals must be monotone")
    if abs(float(top2.sum()) - 2.0) > 1e-7 or abs(float(top3.sum()) - 3.0) > 1e-7:
        raise ValueError("Top2/Top3 probability mass must equal 2/3")
    return numbers, win, top2, top3


def _serialize_distribution(values: dict) -> dict:
    return {_outcome_key(key): float(prob) for key, prob in sorted(values.items())}


def _build_prediction(race: dict, inputs: dict, recorded_at: str) -> dict:
    identity = _formal_identity(race)
    numbers, win, top2, top3 = _full_precision_inputs(race, inputs)
    recorded = stamp(recorded_at)
    post = stamp(identity["raceDate"] + "T" + identity["startTime"])
    if recorded < stamp(identity["capturedAtJST"]) or recorded >= post:
        raise ValueError("ticket shadow prediction must be recorded after formal capture and before post")
    d = distributions(numbers, win, top2, top3)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "version": VERSION,
        "researchLabel": LABEL,
        "mode": "prospective_shadow_only",
        "formalPrediction": identity,
        "recordedAtJST": recorded.isoformat(),
        "models": _model_hashes(),
        "horseMarginals": [
            {"number": int(n), "win": float(win[i]), "top2": float(top2[i]), "top3": float(top3[i]),
             "fittedRankingWeight": float(d["weights"][n])}
            for i, n in enumerate(numbers)
        ],
        "ticketProbabilities": {
            "wide": _serialize_distribution(d["wide"]),
            "quinella": _serialize_distribution(d["quinella"]),
            "exacta": _serialize_distribution(d["exacta"]),
            "trio": _serialize_distribution(d["trio"]),
            "trifecta": _serialize_distribution(d["trifecta"]),
        },
        "oddsJoinPolicy": "join only immutable formal pre-race odds sidecars listed by the manifest; never substitute later/final odds",
        "recommendationEnabled": False,
        "autoBetEnabled": False,
        "selectedBets": 0,
        "stakes": None,
    }
    record["recordFingerprint"] = digest(record)
    return record


def validate_prediction(record: dict) -> dict:
    value = dict(record)
    fingerprint = value.pop("recordFingerprint", None)
    if fingerprint != digest(value) or record.get("schemaVersion") != SCHEMA_VERSION or record.get("version") != VERSION:
        raise ValueError("ticket shadow prediction fingerprint/version mismatch")
    if record.get("researchLabel") != LABEL or record.get("mode") != "prospective_shadow_only":
        raise ValueError("ticket shadow mode mismatch")
    if record.get("recommendationEnabled") is not False or record.get("autoBetEnabled") is not False or record.get("selectedBets") != 0 or record.get("stakes") is not None:
        raise ValueError("ticket shadow must never contain a wagering decision")
    identity = record.get("formalPrediction") or {}
    if identity.get("predictionTiming") != FORMAL_TIMING_MARKER or identity.get("captureQuality") != "formal_10min":
        raise ValueError("ticket shadow is not formal live_10min evidence")
    captured, recorded = stamp(identity.get("capturedAtJST")), stamp(record.get("recordedAtJST"))
    post = stamp(identity["raceDate"] + "T" + identity["startTime"])
    if not captured <= recorded < post:
        raise ValueError("ticket shadow timestamp sequence invalid")
    marginals = record.get("horseMarginals") or []
    numbers = identity.get("horseNumbers") or []
    if [x.get("number") for x in marginals] != numbers:
        raise ValueError("ticket shadow entrant order mismatch")
    for row in marginals:
        vals = [row.get(k) for k in ("win", "top2", "top3", "fittedRankingWeight")]
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in vals):
            raise ValueError("non-finite ticket shadow probability")
        if not 0 < row["win"] <= row["top2"] <= row["top3"] <= 1 + 1e-10 or not 0 < row["fittedRankingWeight"] < 1:
            raise ValueError("invalid ticket shadow marginals")
    probs = record.get("ticketProbabilities") or {}
    if set(probs) != set(BET_TYPES):
        raise ValueError("ticket shadow bet families incomplete")
    for kind, table in probs.items():
        if not isinstance(table, dict) or not table:
            raise ValueError("ticket shadow probability table missing")
        if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 < p <= 1 for p in table.values()):
            raise ValueError("invalid ticket probability")
        if kind != "wide" and not math.isclose(math.fsum(table.values()), 1.0, abs_tol=1e-8):
            raise ValueError(f"{kind} probability mass mismatch")
    return record


def record_prediction_first(root, race: dict, inputs: dict, *, clock=lambda: datetime.now(JST)) -> Path:
    path = _prediction_path(root, race)
    if path.exists():
        old = validate_prediction(json.loads(path.read_text(encoding="utf-8")))
        identity = _formal_identity(race)
        if old["formalPrediction"]["raceID"] != identity["raceID"] or stamp(old["formalPrediction"]["capturedAtJST"]) != stamp(identity["capturedAtJST"]):
            raise ValueError("existing ticket shadow belongs to a different formal capture")
        return path
    record = _build_prediction(race, inputs, clock().astimezone(JST).isoformat())
    validate_prediction(record)
    return _atomic_first(path, record)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _missing_reference(root: Path, path: Path, reason="odds_record_missing") -> dict:
    return {"status": "unavailable", "usableForForwardROI": False, "reason": reason, "path": _relative(root, path)}


def _wide_reference(root: Path, race: dict) -> dict:
    from formal_wide_odds_runtime import _valid_window
    day = race["raceDate"].replace("-", "")
    path = root / "reports" / day / "wide_odds_live10" / f"{race['raceID']}.json"
    if not path.is_file():
        return _missing_reference(root, path)
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("raceID") != race["raceID"] or row.get("raceDate") != race["raceDate"] or not _valid_window(row):
            raise ValueError("formal Wide identity/window mismatch")
        numbers = sorted(h["number"] for h in race["horses"])
        expected = {tuple(x) for x in combinations(numbers, 2)}
        actual = {(int(x["number1"]), int(x["number2"])) for x in row.get("pairs", [])}
        if actual != expected:
            raise ValueError("formal Wide odds coverage mismatch")
        return {"status": "ready", "usableForForwardROI": True, "path": _relative(root, path),
                "sha256": _file_sha256(path), "capturedAtJST": row.get("capturedAtJST"),
                "officialDatetime": row.get("officialDatetime"), "pairCount": len(actual)}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        ref = _missing_reference(root, path, "invalid_formal_wide_odds")
        ref["errorType"] = type(error).__name__
        ref["sha256"] = _file_sha256(path)
        return ref


def _saved_reference(root: Path, race: dict, kind: str) -> dict:
    day = race["raceDate"].replace("-", "")
    if kind in {"quinella", "exacta"}:
        import all_bet_forward as source
        path = root / "reports" / day / "all_bet_v1" / kind / f"{race['raceID']}.json"
    else:
        import triple_forward as source
        path = root / "reports" / day / "triple_v1" / kind / f"{race['raceID']}.json"
    if not path.is_file():
        return _missing_reference(root, path)
    try:
        row = source.validate(json.loads(path.read_text(encoding="utf-8")))
        same_capture = stamp(row["predictionAvailableAtJST"]) == stamp(race["capturedAtJST"])
        ready = row.get("status") == "ready" and same_capture
        return {"status": row.get("status"), "usableForForwardROI": bool(ready), "path": _relative(root, path),
                "sha256": _file_sha256(path), "predictionAvailableAtJST": row.get("predictionAvailableAtJST"),
                "requestStartedAtJST": row.get("requestStartedAtJST"), "receivedAtJST": row.get("receivedAtJST"),
                "recordedAtJST": row.get("recordedAtJST"), "officialDatetime": row.get("officialDatetime"),
                "reason": None if ready else ("odds_unavailable" if same_capture else "formal_capture_timestamp_mismatch")}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        ref = _missing_reference(root, path, "invalid_odds_record")
        ref["errorType"] = type(error).__name__
        ref["sha256"] = _file_sha256(path)
        return ref


def _build_manifest(root: Path, race: dict, recorded_at: str) -> dict:
    prediction_path = _prediction_path(root, race)
    if not prediction_path.is_file():
        raise ValueError("prospective ticket probability record is missing")
    prediction = validate_prediction(json.loads(prediction_path.read_text(encoding="utf-8")))
    if stamp(prediction["formalPrediction"]["capturedAtJST"]) != stamp(race["capturedAtJST"]):
        raise ValueError("ticket probability/formal capture mismatch")
    evidence = {
        "wide": _wide_reference(root, race),
        "quinella": _saved_reference(root, race, "quinella"),
        "exacta": _saved_reference(root, race, "exacta"),
        "trio": _saved_reference(root, race, "trio"),
        "trifecta": _saved_reference(root, race, "trifecta"),
    }
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "version": VERSION,
        "researchLabel": LABEL,
        "mode": "prospective_shadow_only",
        "raceDate": race["raceDate"],
        "raceID": race["raceID"],
        "predictionTiming": FORMAL_TIMING_MARKER,
        "formalPredictionCapturedAtJST": stamp(race["capturedAtJST"]).isoformat(),
        "predictionRecordPath": _relative(root, prediction_path),
        "predictionRecordSHA256": _file_sha256(prediction_path),
        "oddsEvidence": evidence,
        "recordedAtJST": stamp(recorded_at).isoformat(),
        "recommendationEnabled": False,
        "autoBetEnabled": False,
        "selectedBets": 0,
        "stakes": None,
    }
    record["recordFingerprint"] = digest(record)
    return record


def validate_manifest(record: dict) -> dict:
    value = dict(record)
    fingerprint = value.pop("recordFingerprint", None)
    if fingerprint != digest(value) or record.get("schemaVersion") != SCHEMA_VERSION or record.get("version") != VERSION:
        raise ValueError("ticket shadow manifest fingerprint/version mismatch")
    if record.get("researchLabel") != LABEL or record.get("mode") != "prospective_shadow_only" or record.get("predictionTiming") != FORMAL_TIMING_MARKER:
        raise ValueError("ticket shadow manifest mode/timing mismatch")
    if record.get("recommendationEnabled") is not False or record.get("autoBetEnabled") is not False or record.get("selectedBets") != 0 or record.get("stakes") is not None:
        raise ValueError("ticket shadow manifest must never contain a wagering decision")
    evidence = record.get("oddsEvidence") or {}
    if set(evidence) != set(BET_TYPES):
        raise ValueError("ticket shadow odds evidence incomplete")
    for ref in evidence.values():
        if ref.get("usableForForwardROI") not in {True, False} or not isinstance(ref.get("status"), str) or not isinstance(ref.get("path"), str):
            raise ValueError("invalid ticket shadow odds reference")
        if "sha256" in ref and (not isinstance(ref["sha256"], str) or len(ref["sha256"]) != 64):
            raise ValueError("invalid ticket shadow evidence hash")
    return record


def record_manifest_first(root, race: dict, *, clock=lambda: datetime.now(JST)) -> Path:
    root = Path(root)
    path = _manifest_path(root, race)
    if path.exists():
        old = validate_manifest(json.loads(path.read_text(encoding="utf-8")))
        if old["raceID"] != race["raceID"] or stamp(old["formalPredictionCapturedAtJST"]) != stamp(race["capturedAtJST"]):
            raise ValueError("existing ticket shadow manifest belongs to another capture")
        return path
    record = _build_manifest(root, race, clock().astimezone(JST).isoformat())
    validate_manifest(record)
    return _atomic_first(path, record)
