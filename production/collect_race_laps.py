"""Collect one completed JRA day into an independent, resumable sidecar."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pandas as pd
import requests

from race_lap_runtime import METHOD_VERSION, parse_official_laps

REPO = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).strftime("%Y%m%d"))
    parser.add_argument("--history", type=Path, default=REPO / "production/production_history.parquet")
    parser.add_argument("--output-root", type=Path, default=REPO / "research/laps")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    target_day = datetime.strptime(args.date, "%Y%m%d").date()
    if target_day > datetime.now(JST).date() or args.limit < 0:
        raise ValueError("future dates and negative limits are not accepted")
    history_before = hashlib.sha256(args.history.read_bytes()).hexdigest()
    history = pd.read_parquet(args.history, columns=["日付", "レースID", "芝ダ", "距離", "障害"])
    current = history[(pd.to_datetime(history["日付"]).dt.date == target_day)
                      & ~history["障害"].fillna(False).astype(bool)]
    metadata = current.drop_duplicates("レースID").set_index("レースID")
    race_ids = sorted(metadata.index)
    expected = len(race_ids)
    if args.limit:
        race_ids = race_ids[:args.limit]
    if not race_ids:
        print("NO_COMPLETED_FLAT_RACES", args.date)
        return
    output = args.output_root / args.date / "RaceLapData.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    if previous and (previous.get("schemaVersion") != 1 or previous.get("raceDate") != target_day.isoformat()):
        raise ValueError("existing sidecar schema or date does not match")
    old_records = {record["raceID"]: record for record in previous.get("races", [])}
    if len(old_records) != len(previous.get("races", [])):
        raise ValueError("duplicate race ID in existing sidecar")
    successful, failures, reused = {}, [], 0
    os.environ["KEIBAAI_AGGREGATE_DATE"] = args.date
    os.environ["KEIBAAI_HISTORY_DATE"] = args.date
    from update_production_history import resolve_result_url, decode_response
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 KeibaAI-lap-research/1.0", "Accept-Language": "ja,en;q=0.8"})
    for race_id in race_ids:
        if not args.force and race_id in old_records:
            record = old_records[race_id]
            row = metadata.loc[race_id]
            if (record.get("methodVersion") == METHOD_VERSION and record.get("raceDate") == target_day.isoformat()
                    and record.get("distanceM") == int(row["距離"]) and record.get("surface") == row["芝ダ"]):
                successful[race_id] = record
                reused += 1
                continue
        try:
            url = resolve_result_url(race_id, args.date)
            response = session.get(url, timeout=30)
            response.raise_for_status()
            record = parse_official_laps(decode_response(response), race_id, args.date, url)
            row = metadata.loc[race_id]
            if record["distanceM"] != int(row["距離"]) or record["surface"] != row["芝ダ"]:
                raise ValueError("official course disagrees with saved history")
            record["retrievedAtJST"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            record["sourceSHA256"] = hashlib.sha256(response.content).hexdigest()
            successful[race_id] = record
            print("LAP_OK", race_id, record["distanceM"], record["first600mSeconds"], flush=True)
        except Exception as exc:
            failures.append({"raceID": race_id, "reason": str(exc)})
            print("LAP_FAILED", race_id, str(exc), flush=True)
        time.sleep(0.4)
    # Existing records are retained even when an explicit refresh fails.
    combined = {**old_records, **successful}
    records = [combined[key] for key in sorted(combined)]
    assert history_before == hashlib.sha256(args.history.read_bytes()).hexdigest()
    result = {
        "schemaVersion": 1, "methodVersion": METHOD_VERSION, "raceDate": target_day.isoformat(),
        "status": "research_data_only", "paceForecastEnabled": False,
        "stakeSizingEnabled": False, "formalSignalsChanged": False,
        "expectedFlatRaces": expected, "races": records,
        "meaning": "各地点で先頭の馬を基準とするJRA公式ハロンタイム。600m/1000mが計測地点にない距離では該当値を補間せずnullにする。ペース分類や購入判断にはまだ接続しない。",
    }
    if args.apply and successful and records != previous.get("races"):
        write_json(output, result)
    report = {
        "raceDate": target_day.isoformat(), "requestedRaces": len(race_ids), "expectedFlatRaces": expected,
        "successfulRaces": len(successful), "reusedRaces": reused, "failedRaces": len(failures),
        "storedRaces": len(records), "applied": args.apply, "failures": failures,
        "exactFirst600mRaces": sum(record["first600mSeconds"] is not None for record in records),
        "originalHistorySHA256": history_before, "historyUnchanged": True,
    }
    write_json(REPO / "reports/race_laps" / (args.date + ".json"), report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
