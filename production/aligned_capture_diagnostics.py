#!/usr/bin/env python3
"""Offline, read-only triage of aligned T-10 missing captures and audit freshness.

Never re-fetch predictions, odds or results; never promote missing records.
"""
import argparse
import json
from datetime import datetime
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
        if observed and post:
            try:
                late = datetime.fromisoformat(observed) >= datetime.fromisoformat(post)
            except ValueError:
                pass
        frozen = base / "portfolio_live10" / (rid + ".json")
        events = base / "prerace" / rid / "events"
        output.append({
            "raceID": rid, "raceDate": date,
            "scheduleObservedAtJST": observed, "scheduledPostJST": post,
            "scheduleFirstObservedTooLate": late,
            "frozenPortfolioFilePresent": frozen.is_file(),
            "savedEventFiles": len(list(events.glob("*.json"))) if events.is_dir() else 0,
            "classification": ("schedule_observed_after_post" if late else
                               "schedule_available_but_capture_missing" if row else
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
