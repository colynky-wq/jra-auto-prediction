"""Local offline drop-folder bridge for licensed independent T-10 odds exports.

Operator-controlled export files in SOURCE/YYYYMMDD/RACEID.json are checked
against the frozen pre-race win and Wide snapshots, and accepted *only once*
into research_independent_odds/YYYYMMDD/RACEID.json.

No API calls, scraping, external uploads, wagering or changes to forecasts.
The operator must separately ensure that the exporter and local data use are
authorized. This script does not authenticate provenance or source licenses.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from independent_odds_audit import compare_observation, read_json

MAX_BYTES = 2_000_000
JST = timezone(timedelta(hours=9))
DAY_RE = re.compile(r"^20\d{6}$")
RACE_RE = re.compile(r"^\d{12}\.json$")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_relative_path(relative):
    parts = relative.parts
    if len(parts) != 2 or not DAY_RE.fullmatch(parts[0]) or not RACE_RE.fullmatch(parts[1]):
        return False
    try:
        datetime.strptime(parts[0], "%Y%m%d")
    except ValueError:
        return False
    return True


def safe_atomic_create(path, payload):
    """No overwrites: preserve the earliest accepted research observation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", prefix=".new-independent-odds-", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as f:
            temp = Path(f.name)
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temp, path)
            return "created"
        except FileExistsError:
            return "already_exists"
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def ingest_one(root, inbox, accepted_dir, candidate):
    """Return a receipt without copying unverified/late/malformed input."""
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return {"status": "rejected", "reason": "not_regular_file"}
        relative = candidate.relative_to(inbox)
        if not validate_relative_path(relative):
            return {"status": "rejected", "reason": "bad_source_path"}
        date, name = relative.parts
        id = name.removesuffix(".json")
        if candidate.stat().st_size > MAX_BYTES:
            return {"status": "rejected", "reason": "oversized_input"}
        data = candidate.read_bytes()
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            return {"status": "rejected", "reason": "unsupported_schema"}
        if str(payload.get("raceID")) != id or str(payload.get("raceDate", "")).replace("-", "") != date:
            return {"status": "rejected", "reason": "filename_race_identity_mismatch"}
        base = root / "reports" / date
        win_file = base / "all_bet_v1" / "win" / name
        wide_file = base / "wide_odds_live10" / name
        if not win_file.exists() or not wide_file.exists():
            return {"status": "pending", "reason": "frozen_formal_evidence_missing"}
        diff = compare_observation(payload, read_json(win_file), read_json(wide_file))
        if diff["comparisonStatus"] != "structurally_comparable_not_authenticated":
            return {"status": "rejected", "reason": "not_comparable", "errors": diff["errors"]}
        output = accepted_dir / relative
        if output.is_symlink():
            return {"status": "rejected", "reason": "destination_is_symlink"}
        if output.exists():
            existing = output.read_bytes()
            if _sha(existing) == _sha(data):
                return {"status": "already_accepted", "raceID": id}
            return {"status": "rejected", "reason": "immutable_capture_conflict"}
        state = safe_atomic_create(output, data)
        return {"status": "accepted" if state == "created" else "already_accepted",
                "raceID": id, "sha256": _sha(data),
                "sourceAuthenticityVerified": False,
                "monetizableEdgeValidated": False}
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as err:
        return {"status": "rejected", "reason": "malformed_or_unreadable_input",
                "errorType": type(err).__name__}


def scan_once(root, inbox, accepted_dir):
    """Poll only local files; source may be written by a separately licensed exporter."""
    counts = {"accepted": 0, "already_accepted": 0, "rejected": 0, "pending": 0}
    items = []
    if inbox.exists():
        for day_dir in sorted(inbox.iterdir()):
            if day_dir.is_symlink() or not day_dir.is_dir():
                continue
            for candidate in sorted(day_dir.iterdir()):
                if not candidate.name.endswith(".json"):
                    continue
                receipt = ingest_one(root, inbox, accepted_dir, candidate)
                counts[receipt["status"]] += 1
                if receipt["status"] != "already_accepted":
                    items.append({"path": candidate.relative_to(inbox).as_posix(), **receipt})
    return {
        "mode": "local_offline_only", "networkRequests": 0,
        "existingModelMutations": 0,
        "independentSourceAuthenticated": False,
        "polledAtJST": datetime.now(JST).isoformat(timespec="seconds"),
        "counts": counts, "items": items,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-dir", type=Path, default=Path("licensed_odds_drop"))
    parser.add_argument("--accepted-dir", type=Path, default=Path("research_independent_odds"))
    parser.add_argument("--watch", action="store_true",
                        help="keep polling an operator-authorized local export folder")
    parser.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args()
    if args.poll_seconds < 5:
        parser.error("--poll-seconds must be at least 5")
    root = args.root.resolve()
    inbox = args.source_dir.resolve()
    accepted = args.accepted_dir.resolve()
    if inbox == accepted or inbox in accepted.parents or accepted in inbox.parents:
        parser.error("source and destination must be distinct, non-nested directories")
    while True:
        print(json.dumps(scan_once(root, inbox, accepted), ensure_ascii=False,
                         sort_keys=True, allow_nan=False), flush=True)
        if not args.watch:
            break
        time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
