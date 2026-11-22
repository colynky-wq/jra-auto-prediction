"""Load only previously committed date-specific RaceData without a forecast replay.

The historical fallback selects a Git blob committed no later than the next
JST midnight. It NEVER regenerates probabilities from post-race outcomes or
promotes this display data into independently verified formal T-10 captures.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess

JST = timezone(timedelta(hours=9))


class SnapshotUnavailable(RuntimeError):
    pass


def verify_rows(payload, date_iso):
    if not isinstance(payload, list) or not payload:
        raise SnapshotUnavailable("no published RaceData rows")
    if any(not isinstance(r, dict) for r in payload):
        raise ValueError("RaceData row must be an object")
    if {r.get("raceDate") for r in payload} != {date_iso}:
        raise ValueError("RaceData snapshot date does not match target")
    ids = [r.get("raceID") for r in payload]
    if any(not isinstance(r, str) or re.fullmatch(r"\d{12}", r) is None for r in ids):
        raise ValueError("invalid RaceData race ID")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate RaceData race ID")
    if any(not isinstance(r.get("horses"), list) or not r["horses"] for r in payload):
        raise ValueError("published RaceData is missing runners")
    return payload


def git_stdout(root, *args):
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True,
                           timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotUnavailable("published Git history unavailable") from exc
    if p.returncode:
        raise SnapshotUnavailable("published Git version unavailable")
    return p.stdout.decode("utf-8")


def load_published_day(root, target_date8):
    if re.fullmatch(r"\d{8}", target_date8 or "") is None:
        raise ValueError("target day must be YYYYMMDD")
    day = datetime.strptime(target_date8, "%Y%m%d")
    day_iso = day.date().isoformat()
    root = Path(root)
    path = root / "RaceData.json"
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, list):
            raise ValueError("current RaceData must be an array")
        days = {r.get("raceDate") for r in current if isinstance(r, dict)}
        if days == {day_iso}:
            return verify_rows(current, day_iso), {
                "kind": "current_committed_display", "sourceCommitSHA": None,
                "formalT10EvidenceVerified": False}
        if day_iso in days:
            raise ValueError("current RaceData mixes target and other days")
    cutoff = (day.replace(tzinfo=JST) + timedelta(days=1)).isoformat()
    sha = git_stdout(root, "rev-list", "-1", "--before="+cutoff,
                     "HEAD", "--", "RaceData.json").strip()
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise SnapshotUnavailable("no already published RaceData snapshot before target-day midnight")
    previous = json.loads(git_stdout(root, "show", sha+":RaceData.json"))
    try:
        verify_rows(previous, day_iso)
    except (ValueError, SnapshotUnavailable) as exc:
        raise SnapshotUnavailable("published predecessor is not for requested date") from exc
    return previous, {
        "kind": "historical_committed_display", "sourceCommitSHA": sha,
        "formalT10EvidenceVerified": False}
