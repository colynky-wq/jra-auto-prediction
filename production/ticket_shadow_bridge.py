"""Fail-closed bridge between formal T-10 capture and ticket shadow research.

A ticket-specific research failure must never prevent the already-created formal
A prediction or its formal odds sidecars from being persisted.  Instead, the
first failure is frozen as an immutable marker.  Missing prospective data is
never reconstructed from a later model/odds run.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from ticket_shadow_forward import JST, LABEL, VERSION as SHADOW_VERSION
from ticket_probability_runtime import VERSION as TICKET_RUNTIME_VERSION

VERSION = "ticket-shadow-bridge-v1-20261031"


def prediction_path(root, race: dict) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "ticket_shadow_v1" / "prediction" / f"{race['raceID']}.json"


def manifest_path(root, race: dict) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "ticket_shadow_v1" / "manifest" / f"{race['raceID']}.json"


def failure_path(root, race: dict) -> Path:
    day = str(race["raceDate"]).replace("-", "")
    return Path(root) / "reports" / day / "ticket_shadow_v1" / "failure" / f"{race['raceID']}.json"


def _relative(root, path: Path) -> str:
    root = Path(root)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _atomic_first(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ticket-shadow-failure-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # The first prospective outcome is authoritative.  Never replace it.
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def _error_text(error: BaseException) -> str:
    text = str(error).replace("\n", " ").strip()
    return text[:240]


def _record_failure_first(root, race: dict, stage: str, error: BaseException, *, clock=lambda: datetime.now(JST)) -> Path:
    captured = race.get("capturedAtJST")
    if race.get("predictionTiming") != "live_10min" or not isinstance(captured, str) or not captured:
        raise ValueError("ticket shadow failure marker requires formal live_10min identity")
    value = {
        "schemaVersion": 1,
        "version": VERSION,
        "researchLabel": LABEL,
        "shadowOnly": True,
        "autoBet": False,
        "recommendationEnabled": False,
        "status": "error",
        "stage": str(stage),
        "raceDate": str(race["raceDate"]),
        "raceID": str(race["raceID"]),
        "predictionTiming": "live_10min",
        # Deliberately copy the exact formal string; never mint a second prediction timestamp.
        "capturedAtJST": captured,
        "recordedAtJST": clock().astimezone(JST).isoformat(),
        "ticketRuntimeVersion": TICKET_RUNTIME_VERSION,
        "ticketShadowVersion": SHADOW_VERSION,
        "prospectiveProbabilitiesPresent": False,
        "oddsCaptured": False,
        "oddsSnapshot": None,
        "errorType": type(error).__name__,
        "error": _error_text(error),
    }
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    value["recordFingerprint"] = hashlib.sha256(canonical).hexdigest()
    return _atomic_first(failure_path(root, race), value)


def _status(root, path: Path, status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "path": _relative(root, path), **extra}


def record_prediction_best_effort(root, race: dict, inputs: dict, *, first_formal_capture: bool) -> dict[str, Any]:
    """Freeze the research prediction or an immutable failure marker, never raise.

    On a resumed formal race, a missing research prediction is *not* rebuilt.
    A failure marker records that the prospective evidence was absent so resume
    logic can finish without silently creating hindsight data.
    """
    pred = prediction_path(root, race)
    failure = failure_path(root, race)
    if pred.is_file():
        return _status(root, pred, "ready")
    if failure.is_file():
        return _status(root, failure, "error")
    try:
        if not first_formal_capture:
            raise RuntimeError("prospective ticket shadow missing on resume; hindsight backfill refused")
        from ticket_shadow_forward import record_prediction_first
        saved = record_prediction_first(root, race, inputs)
        return _status(root, saved, "ready")
    except Exception as error:
        try:
            marker = _record_failure_first(root, race, "prediction", error)
            return _status(root, marker, "error", errorType=type(error).__name__)
        except Exception as marker_error:
            return {
                "status": "error_unrecorded",
                "path": None,
                "errorType": type(error).__name__,
                "markerErrorType": type(marker_error).__name__,
            }


def record_manifest_best_effort(root, race: dict) -> dict[str, Any]:
    """Freeze odds linkage after formal odds capture without endangering formal A."""
    pred = prediction_path(root, race)
    failure = failure_path(root, race)
    if failure.is_file():
        return _status(root, failure, "skipped_after_prediction_failure")
    if not pred.is_file():
        try:
            marker = _record_failure_first(
                root,
                race,
                "manifest",
                RuntimeError("ticket shadow prediction missing; manifest not backfilled"),
            )
            return _status(root, marker, "error", errorType="RuntimeError")
        except Exception as marker_error:
            return {"status": "error_unrecorded", "path": None, "markerErrorType": type(marker_error).__name__}
    manifest = manifest_path(root, race)
    if manifest.is_file():
        return _status(root, manifest, "ready")
    try:
        from ticket_shadow_forward import record_manifest_first
        saved = record_manifest_first(root, race)
        return _status(root, saved, "ready")
    except Exception as error:
        try:
            marker = _record_failure_first(root, race, "manifest", error)
            return _status(root, marker, "error", errorType=type(error).__name__)
        except Exception as marker_error:
            return {
                "status": "error_unrecorded",
                "path": None,
                "errorType": type(error).__name__,
                "markerErrorType": type(marker_error).__name__,
            }
