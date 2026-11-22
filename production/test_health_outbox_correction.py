import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime

from live_bundle_publisher import Outbox
from monitor_health_runtime import JST, MONITOR_STEP, REPOSITORY, WORKFLOW, health_from_event


def event(run_id, status, conclusion, started):
    return {"repository": {"full_name": REPOSITORY}, "workflow_run": {
        "id": run_id, "name": WORKFLOW, "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY}, "event": "schedule",
        "status": status, "conclusion": conclusion,
        "created_at": started, "run_started_at": started}}


class HealthOutboxCorrection(unittest.TestCase):
    def test_verified_journal_displaces_stale_remote_interruption(self):
        now = datetime(2026, 11, 22, 14, 47, tzinfo=JST)
        bad = health_from_event(event(44977082119, "completed", "cancelled",
             "2026-11-22T02:39:11Z"), None, datetime(2026, 11, 22, 12, tzinfo=JST))
        corrected = health_from_event(event(44977082119, "completed", "cancelled",
             "2026-11-22T02:39:11Z"), [], now)
        actual = health_from_event(event(44973990350, "in_progress", None,
             "2026-11-22T01:59:50Z"),
             [{"status": "in_progress", "steps": [{"name": MONITOR_STEP}]}], now)
        source = {"generatedAtJST": "2026-11-22 14:47:00",
                  "races": [{"raceID": "foo", "recordingState": "saved"}],
                  "warnings": [], "monitorWarnings": [], "monitoring": actual}
        prior = {"races": [{"raceID": "foo", "recordingState": "saved"}],
                 "warnings": ["stale"], "monitorWarnings": ["stale"],
                 "monitoring": bad}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/"outbox"
            blobdir = folder/"blobs"
            blobdir.mkdir(parents=True)
            blob = (json.dumps(corrected)+"\n").encode()
            digest = hashlib.sha256(blob).hexdigest()
            (blobdir/digest).write_bytes(blob)
            out = object.__new__(Outbox)
            out.role, out.date8, out.day, out.folder = (
                "health", "20261122", "2026-11-22", folder)
            out.state = {"pending": {
                f"reports/20261122/monitor_sessions/{corrected['runID']}.json": digest}}
            fixed = json.loads(out.bytes_for_remote(
                "PaceRecordingStatus.json", json.dumps(source).encode(),
                json.dumps(prior).encode()))
            self.assertEqual(fixed["monitoring"]["runID"], actual["runID"])
            self.assertEqual(fixed["monitoring"]["status"], "active")
            self.assertEqual(fixed["races"], prior["races"])


if __name__ == "__main__":
    unittest.main()
