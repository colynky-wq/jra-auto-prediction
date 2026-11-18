"""Single-process Mac/local research bridge for authorized provider exports.

Per tick: normalize complete eligible CSV rows -> immutable JSON candidate ->
validate frozen T-10 evidence and local receipt timing -> immutable research
observation. NEVER fetch external odds, change predictions, or enable betting.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from independent_odds_csv_adapter import scan_csv_once
from independent_odds_dropwatch import scan_once

JST = timezone(timedelta(hours=9))


def run_once(root, *, csv_source, json_inbox, accepted, now=None, today_only=True):
    root = Path(root).resolve()
    csv_source = Path(csv_source).resolve()
    json_inbox = Path(json_inbox).resolve()
    accepted = Path(accepted).resolve()
    paths = [csv_source, json_inbox, accepted]
    if any(
        a == b or a in b.parents or b in a.parents
        for i,a in enumerate(paths) for b in paths[i+1:]
    ):
        raise ValueError("source, JSON inbox and accepted directories must not overlap")
    stamp = now or datetime.now(JST)
    if stamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    day = stamp.astimezone(JST).strftime("%Y%m%d") if today_only else None
    converted = scan_csv_once(csv_source, json_inbox, only_day=day)
    accepted_result = scan_once(root, json_inbox, accepted,
                                prospective=True, now=stamp, only_day=day)
    return {
        "mode":"prospective_local_authorized_export_bridge",
        "networkRequests":0,"liveBetsEnabled":False,
        "modelFilesModified":0,"sourceAuthenticityVerified":False,
        "dayFilterJST":day,
        "conversion":converted,"ingestion":accepted_result,
        "anyProviderDataPresent":bool(
            sum(converted["counts"].values()) or sum(accepted_result["counts"].values())
        ),
    }


def should_log_report(report, previous_signature, seconds_since_log,
                      *, watch_mode=True):
    """Prevent noisy 30-second empty loop logs in a long-running Mac agent."""
    signature = json.dumps({
        "conversion": report["conversion"],
        "ingestion": {
            "counts": report["ingestion"]["counts"],
            "items": report["ingestion"]["items"],
        },
    }, sort_keys=True, ensure_ascii=False)
    return (not watch_mode or signature != previous_signature
            or seconds_since_log >= 3600), signature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--csv-source", type=Path,
                        default=Path("licensed_odds_csv"))
    parser.add_argument("--json-inbox", type=Path,
                        default=Path("licensed_odds_drop"))
    parser.add_argument("--accepted", type=Path,
                        default=Path("research_independent_odds"))
    parser.add_argument("--watch",action="store_true",
                        help="poll continuously (intended for a Mac launchd agent)")
    parser.add_argument("--poll-seconds",type=float,default=30)
    parser.add_argument("--include-history",action="store_true",
                        help="test all date folders, but still enforce live clock receipts")
    args = parser.parse_args()
    if args.poll_seconds < 5:
        parser.error("poll seconds must be at least 5")
    root = args.root.resolve()
    def local_path(p):
        return p.resolve() if p.is_absolute() else (root/p).resolve()
    last_signature = None
    last_logged_at = 0.0
    while True:
        report=run_once(root,csv_source=local_path(args.csv_source),
                        json_inbox=local_path(args.json_inbox),
                        accepted=local_path(args.accepted),
                        today_only=not args.include_history)
        now_monotonic=time.monotonic()
        log, signature=should_log_report(report,last_signature,
                    now_monotonic-last_logged_at,watch_mode=args.watch)
        if log:
            print(json.dumps(report,ensure_ascii=False,sort_keys=True,
                             allow_nan=False),flush=True)
            last_signature=signature
            last_logged_at=now_monotonic
        if not args.watch:
            break
        time.sleep(args.poll_seconds)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
