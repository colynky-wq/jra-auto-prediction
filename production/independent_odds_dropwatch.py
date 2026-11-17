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

from independent_odds_audit import (compare_observation, read_json,
                                    parse_independent_json_bytes)
from odds_integrity_audit import parse_jst

MAX_BYTES = 2_000_000
MAX_LIVE_INGEST_LAG_SECONDS = 180
MAX_CLOCK_SKEW_SECONDS = 20
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


def check_prospective_freshness(snapshot, *, now=None,
                                max_lag_seconds=MAX_LIVE_INGEST_LAG_SECONDS):
    """Reject delayed/replayed observation timestamps at the intake clock.

    A close timestamp is only a timing plausibility check; it does not
    authenticate the source, true quote update, or license permission.
    """
    observed = parse_jst(snapshot.get("observedAtJST"))
    reference = now or datetime.now(JST)
    if reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if observed is None:
        return {"eligible": False, "reason": "observation_timestamp_missing"}
    age = round((reference - observed).total_seconds(), 2)
    if age < -MAX_CLOCK_SKEW_SECONDS:
        return {"eligible": False, "reason": "observation_from_future",
                "ingestLagSeconds": age}
    if age > max_lag_seconds:
        return {"eligible": False, "reason": "observation_not_recent",
                "ingestLagSeconds": age}
    return {"eligible": True, "ingestLagSeconds": age,
            "sourceAuthenticityVerified": False}


def make_intake_receipt(payload, raw_sha256, *, prospective, now, lag_seconds):
    """Bind local capture-clock assertion to the exact stored observation hash."""
    receipt_time = now or datetime.now(JST)
    if receipt_time.tzinfo is None:
        raise ValueError("intake receipt clock must be timezone-aware")
    return {
        "schemaVersion": 1,
        "raceID": str(payload["raceID"]),
        "raceDate": str(payload["raceDate"]),
        "observationSha256": raw_sha256,
        "intakeAtJST": receipt_time.astimezone(JST).isoformat(timespec="milliseconds"),
        "observedAtJST": payload["observedAtJST"],
        "prospectiveClockCheckPassed": bool(prospective),
        "intakeLagSeconds": lag_seconds,
        "sourceAuthenticityVerified": False,
        "providerIndependenceAuthenticated": False,
        "monetizableEdgeValidated": False,
        "proofScope": "local_clock_and_sha_only",
    }


def ingest_one(root, inbox, accepted_dir, candidate, *,
               prospective=False, now=None):
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
        if len(data) > MAX_BYTES:
            return {"status": "rejected", "reason": "oversized_input"}
        payload = parse_independent_json_bytes(data)
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
        freshness = (check_prospective_freshness(payload, now=now)
                     if prospective else {"eligible": False,
                                         "reason": "historical_or_offline_research"})
        if prospective and not freshness["eligible"]:
            return {"status": "rejected", "reason": freshness["reason"],
                    "ingestLagSeconds": freshness.get("ingestLagSeconds")}
        output = accepted_dir / relative
        if accepted_dir.is_symlink() or output.parent.is_symlink():
            return {"status": "rejected", "reason": "destination_directory_is_symlink"}
        if output.is_symlink():
            return {"status": "rejected", "reason": "destination_is_symlink"}
        if output.exists():
            existing = output.read_bytes()
            if _sha(existing) == _sha(data):
                return {"status": "already_accepted", "raceID": id}
            return {"status": "rejected", "reason": "immutable_capture_conflict"}
        state = safe_atomic_create(output, data)
        if state == "already_exists":
            if _sha(output.read_bytes()) != _sha(data):
                return {"status": "rejected", "reason": "immutable_capture_conflict"}
            return {"status": "already_accepted", "raceID": id}
        receipt = make_intake_receipt(
            payload, _sha(data), prospective=prospective, now=now,
            lag_seconds=freshness.get("ingestLagSeconds"),
        )
        receipt_path = output.with_suffix(".receipt.json")
        receipt_state = safe_atomic_create(
            receipt_path,
            (json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                        allow_nan=False) + "\n").encode("utf-8"),
        )
        return {"status": "accepted",
                "raceID": id, "sha256": _sha(data),
                "receiptStatus": "created" if receipt_state == "created" else "existing",
                "prospectiveClockCheckPassed": bool(prospective),
                "ingestLagSeconds": freshness.get("ingestLagSeconds"),
                "sourceAuthenticityVerified": False,
                "monetizableEdgeValidated": False}
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as err:
        return {"status": "rejected", "reason": "malformed_or_unreadable_input",
                "errorType": type(err).__name__}


def scan_once(root, inbox, accepted_dir, *, prospective=False, now=None):
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
                receipt = ingest_one(root, inbox, accepted_dir, candidate,
                                     prospective=prospective, now=now)
                counts[receipt["status"]] += 1
                if receipt["status"] != "already_accepted":
                    items.append({"path": candidate.relative_to(inbox).as_posix(), **receipt})
    return {
        "mode": "local_offline_only", "networkRequests": 0,
        "existingModelMutations": 0,
        "independentSourceAuthenticated": False,
        "prospectiveClockCheckEnabled": bool(prospective),
        "polledAtJST": (now or datetime.now(JST)).isoformat(timespec="seconds"),
        "counts": counts, "items": items,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-dir", type=Path, default=Path("licensed_odds_drop"))
    parser.add_argument("--accepted-dir", type=Path, default=Path("research_independent_odds"))
    parser.add_argument("--watch", action="store_true",
                        help="live-only local polling; require observation within 180 seconds of intake")
    parser.add_argument("--prospective", action="store_true",
                        help="apply live clock freshness check in one-shot mode too")
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
        print(json.dumps(scan_once(root, inbox, accepted,
                                   prospective=args.watch or args.prospective),
                         ensure_ascii=False, sort_keys=True, allow_nan=False),
              flush=True)
        if not args.watch:
            break
        time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
