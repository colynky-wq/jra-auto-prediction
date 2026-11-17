"""Standard-library CSV adapter for explicitly licensed, operator-provided odds.

This does NOT fetch web pages or connect to a provider. Input files are
licensed_odds_csv/YYYYMMDD/RACEID.csv with one row per win horse or Wide pair.
Every row must repeat the same source/time/race metadata. Mixed quote time
snapshots are rejected, not silently merged. Output is a candidate snapshot
for independent_odds_dropwatch, never a verified live market feed.
"""
from __future__ import annotations

import argparse
import csv
from io import StringIO
import json
from pathlib import Path

from independent_odds_audit import (ALLOWED_SOURCES, normalize_quote,
                                    parse_horse_number, minutes_between)
from odds_integrity_audit import parse_jst
from independent_odds_dropwatch import (safe_atomic_create,
                                        validate_relative_path)

REQUIRED_FIELDS = {
    "raceDate", "raceID", "startTime", "source", "sourceReference",
    "permissionConfirmed", "status", "observedAtJST", "asOfJST",
    "betType", "number1", "number2", "oddsLow", "oddsHigh",
}
COMMON_FIELDS = (
    "raceDate", "raceID", "startTime", "source", "sourceReference",
    "permissionConfirmed", "status", "observedAtJST", "asOfJST",
)
MAX_CSV_BYTES = 2_000_000
MAX_ROWS = 1000


def validate_t10_csv_time(snapshot, *, max_quote_age_minutes=5):
    """Do not let early morning exports freeze the only T-10 candidate filename."""
    time_text = snapshot.get("startTime")
    if not isinstance(time_text, str) or len(time_text) != 5 or time_text[2] != ":":
        raise ValueError("invalid scheduled start time")
    post = parse_jst(f"{snapshot['raceDate']}T{time_text}:00")
    captured_text = snapshot.get("observedAtJST")
    asof_text = snapshot.get("asOfJST")
    if any(
        not isinstance(t, str) or not (
            t.endswith("Z") or (
                len(t) >= 6 and t[-6] in "+-" and t[-3] == ":"
                and t[-5:-3].isdigit() and t[-2:].isdigit()
            )
        ) for t in (captured_text, asof_text)
    ):
        raise ValueError("independent CSV timestamps require explicit timezone offset")
    captured, asof = parse_jst(captured_text), parse_jst(asof_text)
    if post is None or captured is None or asof is None:
        raise ValueError("missing scheduled/captured/source time")
    lead = minutes_between(post, captured)
    source_age = minutes_between(captured, asof)
    if lead is None or not 8 <= lead <= 12:
        raise ValueError("outside 8-to-12 minute formal capture window")
    if source_age is None or not 0 <= source_age <= max_quote_age_minutes:
        raise ValueError("CSV source quote not recent enough")
    return {"minutesBeforePost": lead, "quoteAgeMinutes": source_age}


def convert_csv_bytes(data, *, race_id=None, date_folder=None):
    if len(data) > MAX_CSV_BYTES:
        raise ValueError("CSV exceeds maximum input size")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise ValueError("CSV encoding must be UTF-8") from err
    reader = csv.DictReader(StringIO(raw), strict=True)
    names = reader.fieldnames or []
    if not names or len(set(names)) != len(names):
        raise ValueError("missing or duplicate CSV headers")
    if not REQUIRED_FIELDS.issubset(set(names)):
        raise ValueError("missing required CSV columns")
    rows = []
    try:
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError("too many CSV quote rows")
            if None in row or any(v is None for v in row.values()):
                raise ValueError("malformed CSV row or incorrect column count")
            rows.append(row)
    except csv.Error as err:
        raise ValueError("malformed CSV input") from err
    if not rows:
        raise ValueError("CSV has no observations")
    common = {k: rows[0][k].strip() for k in COMMON_FIELDS}
    for row in rows:
        if any(row[k].strip() != common[k] for k in COMMON_FIELDS):
            raise ValueError("mixed race/source or capture time in one file")
    rid = common["raceID"]
    day = common["raceDate"]
    if len(rid) != 12 or not rid.isascii() or not rid.isdigit():
        raise ValueError("raceID must be 12 digits")
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError("invalid raceDate")
    if race_id is not None and race_id != rid:
        raise ValueError("filename raceID mismatch")
    if date_folder is not None and day.replace("-", "") != date_folder:
        raise ValueError("filename date mismatch")
    if common["source"] not in ALLOWED_SOURCES:
        raise ValueError("unknown source")
    if common["permissionConfirmed"] != "true":
        raise ValueError("permissionConfirmed must be explicit 'true'")
    if not common["sourceReference"]:
        raise ValueError("sourceReference required")

    win, wide = [], []
    seen = set()
    for row in rows:
        kind = row["betType"].strip().lower()
        a = parse_horse_number(row["number1"].strip())
        low = normalize_quote(row["oddsLow"].strip())
        if low is None:
            raise ValueError("invalid oddsLow")
        if kind == "win":
            if row["number2"].strip() or row["oddsHigh"].strip():
                raise ValueError("win row must leave number2 and oddsHigh empty")
            key = ("win", a)
            val = {"number": a, "odds": low}
            dest = win
        elif kind == "wide":
            b = parse_horse_number(row["number2"].strip())
            high = normalize_quote(row["oddsHigh"].strip())
            if a == b or high is None or low > high:
                raise ValueError("invalid wide pair or odds range")
            key = ("wide", min(a, b), max(a, b))
            val = {"number1": min(a, b), "number2": max(a, b),
                   "oddsLow": low, "oddsHigh": high}
            dest = wide
        else:
            raise ValueError("unsupported betType; only win and wide")
        if key in seen:
            raise ValueError("duplicate win or Wide pair")
        seen.add(key)
        dest.append(val)
    result = {
        "schemaVersion": 1,
        "source": common["source"],
        "sourceReference": common["sourceReference"],
        "permissionConfirmed": True,
        "raceDate": day,
        "raceID": rid,
        "startTime": common["startTime"],
        "status": common["status"],
        "observedAtJST": common["observedAtJST"],
        "asOfJST": common["asOfJST"],
        "winOdds": sorted(win, key=lambda x: x["number"]),
        "wideOdds": sorted(wide, key=lambda x: (x["number1"], x["number2"])),
    }
    validate_t10_csv_time(result)
    return result


def convert_one(input_path, source_dir, dest_dir):
    """Atomically generate one candidate JSON; never replace an existing one."""
    try:
        if input_path.is_symlink() or not input_path.is_file():
            return {"status": "rejected", "reason": "not_regular_file"}
        relative = input_path.relative_to(source_dir)
        fake_json = relative.with_suffix(".json")
        if not validate_relative_path(fake_json):
            return {"status": "rejected", "reason": "invalid_filename"}
        if input_path.stat().st_size > MAX_CSV_BYTES:
            return {"status": "rejected", "reason": "oversized_csv"}
        snapshot = convert_csv_bytes(input_path.read_bytes(),
            race_id=input_path.stem, date_folder=relative.parts[0])
        output = dest_dir / fake_json
        if dest_dir.is_symlink() or output.parent.is_symlink() or output.is_symlink():
            return {"status": "rejected", "reason": "symlink_destination"}
        data = (json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                           allow_nan=False) + "\n").encode("utf-8")
        if output.exists():
            if output.read_bytes() != data:
                return {"status": "rejected", "reason": "immutable_conversion_conflict"}
            return {"status": "already_converted", "raceID":input_path.stem}
        created = safe_atomic_create(output, data)
        if created != "created" and output.read_bytes() != data:
            return {"status": "rejected", "reason": "immutable_conversion_conflict"}
        return {"status": ("converted" if created == "created" else "already_converted"),
                "raceID": input_path.stem,
                "sourceAuthenticityVerified": False}
    except (OSError, UnicodeError, TypeError, ValueError, KeyError) as err:
        return {"status": "rejected", "reason": "invalid_csv",
                "errorType": type(err).__name__}


def scan_csv_once(source_dir, dest_dir):
    counts = {"converted":0, "already_converted":0, "rejected":0}
    items = []
    if source_dir.exists():
        for folder in sorted(source_dir.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.csv")):
                report = convert_one(path,source_dir,dest_dir)
                counts[report["status"]] += 1
                if report["status"] != "already_converted":
                    items.append({"source":path.relative_to(source_dir).as_posix(),
                                  **report})
    return {"mode":"offline_licensed_csv_adapter","networkRequests":0,
            "sourceAuthenticityVerified":False,"counts":counts,"items":items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path,
                        default=Path("licensed_odds_csv"))
    parser.add_argument("--dest-dir", type=Path,
                        default=Path("licensed_odds_drop"))
    args = parser.parse_args()
    source, dest = args.source_dir.resolve(), args.dest_dir.resolve()
    if source == dest or source in dest.parents or dest in source.parents:
        parser.error("CSV source and JSON destination must be distinct directories")
    print(json.dumps(scan_csv_once(source,dest),ensure_ascii=False,
                     sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
