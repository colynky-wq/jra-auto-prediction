"""Resume historical JRA lap collection using observed annual report links."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests

from collect_race_laps import write_json
from race_lap_pdf_runtime import METHOD_VERSION, VENUE_SLUGS, parse_official_report
from historical_jra_laps import HistoricalResults

REPO = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def collect_report(task: dict) -> tuple[list, list]:
    path = Path(task["cache"])
    if path.exists():
        data = path.read_bytes()
    else:
        response = requests.get(task["url"], headers={"User-Agent": "Mozilla/5.0 KeibaAI-research/1.0"}, timeout=45)
        response.raise_for_status()
        data = response.content
        if not data.startswith(b"%PDF-"):
            raise ValueError("official report response is not a PDF")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        time.sleep(.5)
    records, failures = parse_official_report(data, task["url"], task["expected"])
    for record in records:
        record["sourceSHA256"] = hashlib.sha256(data).hexdigest()
        record["retrievedAtJST"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    return records, failures


def validate_years(years: list[int], through_date: str | None, today=None):
    today = today or datetime.now(JST).date()
    cutoff = datetime.strptime(through_date, "%Y%m%d").date() if through_date else None
    if cutoff and cutoff >= today:
        raise ValueError("cutoff must be before today in JST")
    if any(year < 2017 or year > today.year or (year == today.year and cutoff is None) for year in years):
        raise ValueError("current annual reports require an explicit completed-date cutoff")
    return cutoff


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    parser.add_argument("--through-date", help="YYYYMMDD, strictly before today JST; required for current-year reports.")
    parser.add_argument("--history", type=Path, default=REPO / "production/production_history.parquet")
    parser.add_argument("--output-root", type=Path, default=REPO / "research/laps")
    parser.add_argument("--cache-root", type=Path, default=REPO / "tmp/lap_reports")
    parser.add_argument("--max-reports", type=int, default=0)
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--html-retry", action="store_true", help="Retry report gaps using the official past-results selector.")
    args = parser.parse_args()
    cutoff = validate_years(args.years, args.through_date)
    if args.max_reports < 0:
        raise ValueError("negative report limits are not accepted")
    before = hashlib.sha256(args.history.read_bytes()).hexdigest()
    history = pd.read_parquet(args.history, columns=["レースID", "日付", "障害", "距離", "芝ダ", "着順", "タイム秒"])
    history = history[~history["障害"].fillna(False).astype(bool)].copy()
    history["date8"] = pd.to_datetime(history["日付"]).dt.strftime("%Y%m%d")
    history = history[history["date8"].str[:4].astype(int).isin(args.years)]
    if cutoff:
        history = history[history["date8"] <= cutoff.strftime("%Y%m%d")]
    winner_times = history[pd.to_numeric(history["着順"], errors="coerce") == 1].groupby("レースID")["タイム秒"].min()
    metadata = history.drop_duplicates("レースID").copy()
    existing = {}
    for path in args.output_root.glob("*/RaceLapData.json"):
        payload = json.loads(path.read_text())
        for record in payload["races"]:
            if record["raceID"] in existing:
                raise ValueError("duplicate stored race ID")
            existing[record["raceID"]] = record
    tasks, expected_total = [], len(metadata)
    for year in sorted(set(args.years)):
        index_url = f"https://www.jra.go.jp/datafile/seiseki/report/{year}.html"
        response = requests.get(index_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        observed = {urljoin(index_url, a["href"]) for a in soup.select("a[href]")}
        subset = metadata[metadata["date8"].str.startswith(str(year))]
        for prefix, rows in subset.groupby(subset["レースID"].str[:10], sort=True):
            expected = []
            for _, row in rows.iterrows():
                old = existing.get(row["レースID"])
                winner_time = float(winner_times.get(row["レースID"], float("nan")))
                if (old and old.get("methodVersion") in {METHOD_VERSION, "jra-official-race-laps-v1"}
                        and old.get("raceDate", "").replace("-", "") == row["date8"]
                        and old["distanceM"] == int(row["距離"]) and old["surface"] == row["芝ダ"]
                        and abs(old["winnerTimeSeconds"]-winner_time) <= .050000001
                        and all(old["validation"].values())):
                    continue
                expected.append({"raceID": row["レースID"], "date8": row["date8"],
                                 "distanceM": int(row["距離"]), "surface": row["芝ダ"],
                                 "winnerTimeSeconds": winner_time})
            if not expected:
                continue
            filename = f"{year}-{int(prefix[6:8])}{VENUE_SLUGS[prefix[4:6]]}{int(prefix[8:10])}.pdf"
            url = f"https://www.jra.go.jp/datafile/seiseki/report/{year}/{filename}"
            if url not in observed:
                raise ValueError(f"report URL absent from official annual index: {filename}")
            tasks.append({"url": url, "expected": expected, "cache": str(args.cache_root / filename)})
    requested_tasks = tasks[:args.max_reports] if args.max_reports else tasks
    totals = metadata.groupby("date8").size().to_dict()
    failures, added, changed_dates = [], 0, set()
    historical = None
    print("BACKFILL_PLAN", "expected", expected_total, "pendingReports", len(tasks), "requestedReports", len(requested_tasks), flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(collect_report, task): task for task in requested_tasks}
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                records, errors = future.result()
            except Exception as exc:
                records, errors = [], [{"raceID": row["raceID"], "reason": str(exc)} for row in task["expected"]]
            if args.html_retry and errors:
                expected_map = {row["raceID"]: row for row in task["expected"]}
                remaining = []
                for error in errors:
                    try:
                        if historical is None:
                            historical = HistoricalResults()
                        record = historical.fetch(expected_map[error["raceID"]])
                        record["retrievedAtJST"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                        records.append(record)
                    except Exception as exc:
                        remaining.append({**error, "htmlRetryReason": str(exc)})
                errors = remaining
            failures.extend(errors)
            for record in records:
                existing[record["raceID"]] = record
                changed_dates.add(record["raceDate"].replace("-", ""))
            added += len(records)
            if args.apply:
                # Persist after each report. Interrupted runs keep completed work.
                for date8 in {r["raceDate"].replace("-", "") for r in records}:
                    day_records = sorted((r for r in existing.values() if r["raceDate"].replace("-", "") == date8), key=lambda r:r["raceID"])
                    write_json(args.output_root / date8 / "RaceLapData.json", {
                        "schemaVersion": 1, "methodVersion": METHOD_VERSION,
                        "raceDate": datetime.strptime(date8, "%Y%m%d").date().isoformat(),
                        "status": "research_data_only", "paceForecastEnabled": False,
                        "stakeSizingEnabled": False, "formalSignalsChanged": False,
                        "expectedFlatRaces": int(totals[date8]), "races": day_records,
                        "meaning": "JRA公式のレース全体のラップ。実測地点がない600m/1000mは補間しない。"})
            print("REPORT_DONE", index, "/", len(requested_tasks), Path(task["url"]).name,
                  "races", len(records), "failed", len(errors), "added", added, flush=True)
    after = hashlib.sha256(args.history.read_bytes()).hexdigest()
    assert before == after, "production history changed during backfill"
    target_ids = set(metadata["レースID"])
    stored = len(target_ids & existing.keys())
    report = {"years": sorted(set(args.years)), "throughDate": args.through_date, "expectedRaces": expected_total, "storedRaces": stored,
              "remainingRaces": expected_total-stored, "addedRaces": added, "processedReports": len(requested_tasks),
              "pendingReportsAtStart": len(tasks), "failedRaces": len(failures), "failures": failures[:32],
              "failureExamplesTruncated": len(failures) > 32,
              "failureReasonCounts": dict(Counter("report download unavailable" if "HTTP" in e["reason"] else e["reason"] for e in failures)),
              "historyUnchanged": True, "originalHistorySHA256": before, "applied": args.apply}
    if args.apply:
        write_json(REPO / "reports/race_laps/backfill.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
