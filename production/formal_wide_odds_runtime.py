"""Formal Wide odds capture for live_10min predictions.

Morning/early snapshots are research observations and must never be reused as the
odds attached to a formal 12-to-8-minute prediction.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import wide_odds_runtime as wide

MIN_MINUTES_TO_POST = 8.0
MAX_MINUTES_TO_POST = 12.0
FOLDER = "wide_odds_live10"


def _valid_window(snapshot: dict) -> bool:
    value = wide._minutes_to_post(snapshot.get("raceDate"), snapshot.get("startTime"), snapshot.get("capturedAtJST"))
    return value is not None and MIN_MINUTES_TO_POST <= value <= MAX_MINUTES_TO_POST


def _archive(snapshot: dict) -> Path:
    day = str(snapshot["raceDate"]).replace("-", "")
    path = wide.REPORTS_DIR / day / FOLDER / (str(snapshot["raceID"]) + ".json")
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_window(saved):
            raise ValueError("saved formal Wide snapshot is outside 12-to-8-minute window")
        return path
    payload = dict(snapshot)
    payload["minutesToPost"] = wide._minutes_to_post(payload.get("raceDate"), payload.get("startTime"), payload.get("capturedAtJST"))
    payload["capturePolicy"] = "formal live_10min: 12-to-8 minutes before post"
    payload["frozenAtFirstSuccessfulCapture"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8") as f:
            temporary = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n"); f.flush(); os.fsync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not _valid_window(saved):
        raise ValueError("formal Wide archive failed window validation")
    return path


def record_first_wide_snapshot(*, race_id: str, race_date: str, venue=None, race_number=None, start_time=None, path: Path = wide.WIDE_ODDS_PATH) -> dict:
    """Fetch/freeze a snapshot specifically for the formal prediction window.

    Never falls back to the legacy/morning wide_odds archive.
    """
    archive = wide.REPORTS_DIR / race_date.replace("-", "") / FOLDER / (str(race_id) + ".json")
    if archive.exists():
        snapshot = json.loads(archive.read_text(encoding="utf-8"))
    else:
        snapshot = wide.fetch_wide_snapshot(race_id, race_date=race_date, venue=venue, race_number=race_number, start_time=start_time)
        if not _valid_window(snapshot):
            raise ValueError(f"formal Wide capture outside 12-to-8-minute window: {snapshot.get('minutesToPost')}")
        _archive(snapshot)
        snapshot = json.loads(archive.read_text(encoding="utf-8"))
    if not _valid_window(snapshot):
        raise ValueError("formal Wide snapshot outside 12-to-8-minute window")

    # The rotating cache must point to the formal snapshot for this race so a
    # later SignalData rebuild cannot silently revert to an early observation.
    with path.with_suffix(".json.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = wide._load_payload(path)
        snapshots = {str(x["raceID"]): x for x in payload.get("snapshots", []) if x.get("raceDate") == race_date}
        snapshots[str(race_id)] = snapshot
        payload = {"schemaVersion": 1, "betType": "ワイド",
                   "snapshotPolicy": "formal live_10min snapshot preferred; never reuse morning snapshot as formal odds",
                   "generatedAtJST": datetime.now(wide.JST).strftime("%Y-%m-%d %H:%M:%S"),
                   "snapshots": sorted(snapshots.values(), key=lambda x: (str(x.get("startTime") or ""), str(x["raceID"])))}
        wide._write_payload(payload, path)
    return snapshot
