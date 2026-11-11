#!/usr/bin/env python3
"""Offline, read-only triage of aligned T-10 missing captures and audit freshness.

Never re-fetch predictions, odds or results; never promote missing records.
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

def inspect(root):
    audit = load(root / "AlignedPortfolioReview.json")
    if not isinstance(audit, dict):
        return {"status": "blocked", "reason": "audit_unavailable", "races": []}
    aligned = audit.get("aligned") or {}
    state_ok = (audit.get("projectionState") == "verified"
                and aligned.get("countsAreCurrent") is True
                and audit.get("cohortLockRecoveryRequired") is not True)
    issues = (audit.get("quality") or {}).get("issues") or []
    missing = [i for i in issues if isinstance(i, dict)
               and i.get("code") == "capture_missing_after_window"]
    output = []
    for issue in missing:
        rid = str(issue.get("raceID") or "")
        date = str(issue.get("raceDate") or "")
        if len(rid) != 12 or not rid.isdigit() or len(date) != 10:
            output.append({"raceID": rid or None, "status": "identity_unverified"})
            continue
        day = date.replace("-", "")
        base = root / "reports" / day
        schedule = load(base / "PaceRecordingSchedule.json")
        row = next((r for r in (schedule or {}).get("races", [])
                    if r.get("raceID") == rid), None)
        observed = (schedule or {}).get("observedAtJST")
        post = row.get("scheduledPostJST") if row else None
        late = None
        after_window = None
        if observed and post:
            try:
                observed_dt = datetime.fromisoformat(observed)
                post_dt = datetime.fromisoformat(post)
                late = observed_dt >= post_dt
                after_window = observed_dt > post_dt - timedelta(minutes=8)
            except ValueError:
                pass
        frozen = base / "portfolio_live10" / (rid + ".json")
        events = base / "prerace" / rid / "events"
        sources = {
            "win": base / "all_bet_v1" / "win" / (rid + ".json"),
            "trio": base / "triple_v1" / "trio" / (rid + ".json"),
            "wideOdds": base / "wide_odds_live10" / (rid + ".json"),
        }
        event_rows = []
        if events.is_dir():
            for event_path in sorted(events.glob("*.json")):
                event = load(event_path)
                if isinstance(event, dict) and event.get("raceID") == rid:
                    event_rows.append({
                        "stage": event.get("stage"),
                        "atJST": event.get("atJST"),
                        "detail": str(event.get("detail") or "")[:300],
                    })
        missing_sources = [name for name, path in sources.items() if not path.is_file()]
        output.append({
            "raceID": rid, "raceDate": date,
            "scheduleObservedAtJST": observed, "scheduledPostJST": post,
            "scheduleFirstObservedTooLate": late,
            "scheduleSnapshotAfterT10Window": after_window,
            "frozenPortfolioFilePresent": frozen.is_file(),
            "savedEventFiles": len(event_rows),
            "savedEvents": event_rows,
            "sourceFilesPresent": {name: path.is_file() for name, path in sources.items()},
            "missingSourceKinds": missing_sources,
            "classification": ("schedule_snapshot_after_post" if late else
                               "schedule_snapshot_after_t10_window" if after_window else
                               "schedule_snapshot_within_or_before_t10_window" if row and after_window is False else
                               "schedule_evidence_unavailable"),
            "note": "Diagnostic only; does not establish root cause or recover T-10 evidence"
        })
    return {
        "status": "audit_verified" if state_ok else "latest_unverified",
        "auditGeneratedAtJST": audit.get("generatedAtJST"),
        "qualityIssueCount": len(issues),
        "missingCaptureIssueCount": len(missing),
        "races": output,
        "rules": "Read-only saved evidence; no backfill, no beta eligibility change"
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    print(json.dumps(inspect(args.root), ensure_ascii=False, indent=2))
if __name__ == "__main__":
    main()
