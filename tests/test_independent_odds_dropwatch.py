"""Tests for offline authorized-export drop-folder ingestion (all synthetic)."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from independent_odds_dropwatch import (
    ingest_one, scan_once, validate_relative_path,
    check_prospective_freshness,
)


class OddsDropwatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.inbox=self.root/"licensed_odds_drop"
        self.accepted=self.root/"research_independent_odds"
        date="20261115"
        rid="202608050412"
        self.name=f"{rid}.json"
        self.source_dir=self.inbox/date
        self.source_dir.mkdir(parents=True)
        base=self.root/"reports"/date
        (base/"all_bet_v1"/"win").mkdir(parents=True)
        (base/"wide_odds_live10").mkdir(parents=True)
        prediction={"prediction":{
            "raceDate":"2026-11-15","raceID":rid,"startTime":"16:15",
            "capturedAtJST":"2026-11-15T16:03:30+09:00",
            "horses":[{"number":1,"odds":3.2},{"number":2,"odds":4.2},
                      {"number":3,"odds":9.8}]}}
        wide={"raceDate":"2026-11-15","raceID":rid,
            "startTime":"16:15","pairs":[
                {"number1":1,"number2":2,"oddsLow":5.0,"oddsHigh":6.0},
                {"number1":1,"number2":3,"oddsLow":10.0,"oddsHigh":11.0},
                {"number1":2,"number2":3,"oddsLow":12.0,"oddsHigh":13.0},
            ]}
        (base/"all_bet_v1"/"win"/self.name).write_text(json.dumps(prediction))
        (base/"wide_odds_live10"/self.name).write_text(json.dumps(wide))
        self.payload={
            "schemaVersion":1,"source":"licensed_odds_provider",
            "sourceReference":"local-licensed-export-receipt",
            "permissionConfirmed":True,
            "raceDate":"2026-11-15","raceID":rid,"startTime":"16:15",
            "status":"pre_post",
            "observedAtJST":"2026-11-15T16:04:00+09:00",
            "asOfJST":"2026-11-15T16:03:00+09:00",
            "winOdds":[{"number":1,"odds":3.3}],
            "wideOdds":[{"number1":1,"number2":2,"oddsLow":5.2,"oddsHigh":5.8}]
        }
        self.input=self.source_dir/self.name

    def save(self):
        self.input.write_text(json.dumps(self.payload),encoding="utf-8")

    def test_prospective_freshness_accepts_recent_snapshot(self):
        now=datetime.fromisoformat("2026-11-15T16:05:00+09:00")
        check=check_prospective_freshness(self.payload, now=now)
        self.assertTrue(check["eligible"])
        self.assertEqual(check["ingestLagSeconds"],60.0)
        self.assertFalse(check["sourceAuthenticityVerified"])

    def test_prospective_freshness_rejects_replay(self):
        now=datetime.fromisoformat("2026-11-15T16:10:00+09:00")
        check=check_prospective_freshness(self.payload, now=now)
        self.assertFalse(check["eligible"])
        self.assertEqual(check["reason"],"observation_not_recent")

    def test_prospective_freshness_rejects_future_clock(self):
        now=datetime.fromisoformat("2026-11-15T16:03:00+09:00")
        check=check_prospective_freshness(self.payload, now=now)
        self.assertFalse(check["eligible"])
        self.assertEqual(check["reason"],"observation_from_future")

    def test_prospective_ingest_rejects_historical_file(self):
        self.save()
        now=datetime.fromisoformat("2026-11-17T20:00:00+09:00")
        receipt=ingest_one(self.root,self.inbox,self.accepted,self.input,
                           prospective=True,now=now)
        self.assertEqual(receipt["status"],"rejected")
        self.assertEqual(receipt["reason"],"observation_not_recent")
        self.assertFalse((self.accepted/"20261115"/self.name).exists())

    def test_prospective_ingest_accepts_recent_and_marks_clock_check(self):
        self.save()
        now=datetime.fromisoformat("2026-11-15T16:05:00+09:00")
        receipt=ingest_one(self.root,self.inbox,self.accepted,self.input,
                           prospective=True,now=now)
        self.assertEqual(receipt["status"],"accepted")
        self.assertTrue(receipt["prospectiveClockCheckPassed"])
        self.assertEqual(receipt["ingestLagSeconds"],60.0)
        self.assertFalse(receipt["sourceAuthenticityVerified"])

    def test_offline_ingest_never_claims_prospective_proof(self):
        self.save()
        receipt=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(receipt["status"],"accepted")
        self.assertFalse(receipt["prospectiveClockCheckPassed"])
        self.assertIsNone(receipt["ingestLagSeconds"])

    def test_accept_valid_authorized_export_once(self):
        self.save()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["status"],"accepted")
        out=self.accepted/"20261115"/self.name
        self.assertEqual(json.loads(out.read_text()),self.payload)
        self.assertEqual(ingest_one(self.root,self.inbox,self.accepted,self.input)["status"],
                         "already_accepted")

    def test_immutable_conflict_rejected(self):
        self.save()
        self.assertEqual(ingest_one(self.root,self.inbox,self.accepted,self.input)["status"],
                         "accepted")
        self.payload["winOdds"][0]["odds"]=12.3
        self.save()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["status"],"rejected")
        self.assertEqual(result["reason"],"immutable_capture_conflict")

    def test_permission_not_confirmed_rejected(self):
        self.payload["permissionConfirmed"]=False
        self.save()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["status"],"rejected")
        self.assertIn("permission_not_confirmed",result["errors"])

    def test_after_post_observation_rejected(self):
        self.payload["observedAtJST"]="2026-11-15T16:16:00+09:00"
        self.payload["asOfJST"]="2026-11-15T16:15:00+09:00"
        self.save()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["status"],"rejected")
        self.assertIn("outside_8_to_12_minute_window",result["errors"])

    def test_missing_formal_evidence_stays_pending(self):
        self.save()
        (self.root/"reports"/"20261115"/"wide_odds_live10"/self.name).unlink()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["status"],"pending")
        self.assertFalse((self.accepted/"20261115"/self.name).exists())

    def test_filename_identity_mismatch_is_rejected(self):
        self.save()
        self.payload["raceID"]="202608050411"
        self.save()
        result=ingest_one(self.root,self.inbox,self.accepted,self.input)
        self.assertEqual(result["reason"],"filename_race_identity_mismatch")

    def test_bad_and_traversal_paths_are_rejected(self):
        self.assertTrue(validate_relative_path(Path("20261115")/self.name))
        self.assertFalse(validate_relative_path(Path("20261115")/"../fake.json"))
        self.assertFalse(validate_relative_path(Path("not-a-day")/self.name))
        self.assertFalse(validate_relative_path(Path("20261115")/"../../fake.json"))

    def test_ingest_does_not_modify_frozen_files(self):
        self.save()
        formal=self.root/"reports"/"20261115"/"all_bet_v1"/"win"/self.name
        before=formal.read_bytes()
        result=scan_once(self.root,self.inbox,self.accepted)
        self.assertEqual(result["counts"]["accepted"],1)
        self.assertEqual(result["networkRequests"],0)
        self.assertEqual(result["existingModelMutations"],0)
        self.assertEqual(formal.read_bytes(),before)


if __name__=="__main__":
    unittest.main()
