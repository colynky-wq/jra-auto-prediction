"""Freeze official Wide responses in an isolated, first-write research archive."""

from datetime import datetime, timedelta
from itertools import combinations
import json
import math
from pathlib import Path
import re

from all_bet_forward import write_first
from formal_portfolio_capture import digest, stamp, window
from jra_entry_source import ACCESS_D, _race_id_from_url
from win_odds_freshness import JST, MAX_AGE, validate_win_odds_freshness


VERSION = "jra-wide-research-v1"


def path_for(archive, date8, race_id):
    if not re.fullmatch(r"[0-9]{8}", date8) or not re.fullmatch(r"[0-9]{12}", race_id):
        raise ValueError("invalid official Wide archive date or race")
    datetime.strptime(date8, "%Y%m%d")
    return Path(archive) / date8 / f"{race_id}.json"


def verify_snapshot(snapshot, date8, race_id, decision_at):
    """Recheck the provider evidence, decision clock and complete pair coverage."""
    path_for(".", date8, race_id)
    day = datetime.strptime(date8, "%Y%m%d").date().isoformat()
    entry_url = snapshot["sourceEntryURL"]
    if (
        snapshot.get("schemaVersion") != 1
        or snapshot.get("researchOnly") is not True
        or snapshot.get("status") != "verified_interim"
        or snapshot.get("sourceProvider") != "jra_official_web"
        or snapshot.get("raceID") != race_id or snapshot.get("sourceRaceID") != race_id
        or snapshot.get("raceDate") != day or snapshot.get("sourceRaceDateJST") != date8
        or not entry_url.startswith(ACCESS_D + "?")
        or _race_id_from_url(entry_url, date8) != race_id
        or not re.fullmatch(r"[a-f0-9]{64}", snapshot["sourceEntryPageSha256"])
        or snapshot.get("sourceHashEncoding") != "decoded UTF-8 response text"
    ):
        raise ValueError("expected an official interim Wide snapshot with bound race identity")
    if decision_at.utcoffset() is None:
        raise ValueError("aware official Wide decision timestamp required")
    post = stamp(snapshot["scheduledPostJST"])
    start = post.strftime("%H:%M")
    if post.date().isoformat() != day or post.second or post.microsecond:
        raise ValueError("invalid official Wide scheduled post")
    receipt = window(day, start, snapshot["receivedAtJST"])
    decision = window(day, start, decision_at.isoformat())
    if receipt > decision:
        raise ValueError("official Wide receipt is after the decision")
    evidence = snapshot["freshnessEvidence"]
    expected = validate_win_odds_freshness({
        **snapshot,
        "sourceTimePrecision": evidence["sourceTimePrecision"],
        "data": {"official_datetime": evidence["sourceUpdatedAtJST"]},
    }, receipt)
    if evidence != expected:
        raise ValueError("official Wide freshness provenance differs")
    source = stamp(evidence["sourceUpdatedAtJST"])
    if decision - source > MAX_AGE:
        raise ValueError("official Wide odds exceed 30 minutes at decision")

    numbers = snapshot["horseNumbers"]
    if (not isinstance(numbers, list) or not 3 <= len(numbers) <= 18
            or any(type(n) is not int or not 1 <= n <= 18 for n in numbers)
            or numbers != sorted(set(numbers))):
        raise ValueError("invalid official Wide entrant roster")
    expected_pairs = list(combinations(numbers, 2))
    pairs = snapshot["pairs"]
    if (type(snapshot["pairCount"]) is not int or snapshot["pairCount"] != len(pairs)
            or [(p["number1"], p["number2"]) for p in pairs] != expected_pairs):
        raise ValueError("official Wide pair roster differs")
    for pair in pairs:
        a, b, low, high = (pair[key] for key in ("number1", "number2", "oddsLow", "oddsHigh"))
        if (type(a) is not int or type(b) is not int or type(low) not in (int, float)
                or type(high) not in (int, float) or not math.isfinite(low)
                or not math.isfinite(high) or not 1 <= low <= high):
            raise ValueError("invalid official Wide pair bounds")
    return (decision - source).total_seconds()


def read_record(path, date8, race_id):
    record = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in record.items() if key != "recordFingerprint"}
    if record.get("version") != VERSION or record.get("recordFingerprint") != digest(unsigned):
        raise ValueError("official Wide archive fingerprint mismatch")
    snapshot = record["snapshot"]
    verify_snapshot(snapshot, date8, race_id, stamp(record["recordedAtJST"]))
    return record


def record_first(archive, snapshot, *, clock=lambda: datetime.now(JST)):
    date8, race_id = snapshot["sourceRaceDateJST"], snapshot["raceID"]
    path = path_for(archive, date8, race_id)
    if path.is_file():
        return read_record(path, date8, race_id)
    now = clock()
    verify_snapshot(snapshot, date8, race_id, now)
    record = {"version": VERSION, "recordedAtJST": now.astimezone(JST).isoformat(),
              "snapshot": snapshot}
    record["recordFingerprint"] = digest(record)
    deadline = min(
        stamp(snapshot["scheduledPostJST"]) - timedelta(minutes=8) + timedelta(microseconds=1),
        stamp(snapshot["freshnessEvidence"]["sourceUpdatedAtJST"]) + MAX_AGE + timedelta(microseconds=1),
    )
    try:
        saved = write_first(path, record, deadline, clock)
    except ValueError:
        if path.is_file():
            return read_record(path, date8, race_id)
        raise
    if saved is None:
        raise ValueError("official Wide research write missed its pre-race freshness deadline")
    return read_record(saved, date8, race_id)
