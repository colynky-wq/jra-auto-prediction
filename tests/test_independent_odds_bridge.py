"""Synthetic integration tests of the Mac CSV -> JSON -> intake bridge."""
from __future__ import annotations

from datetime import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"production"))
from independent_odds_bridge import run_once, should_log_report


class MacOddsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.csv=self.root/"licensed_odds_csv"
        self.inbox=self.root/"licensed_odds_drop"
        self.accepted=self.root/"research_independent_odds"
        self.date="20261115"
        self.rid="202608050412"
        self.now=datetime.fromisoformat("2026-11-15T16:14:45+09:00")
        frozen=self.root/"reports"/self.date
        (frozen/"all_bet_v1"/"win").mkdir(parents=True)
        (frozen/"wide_odds_live10").mkdir(parents=True)
        self.frozen_win=frozen/"all_bet_v1"/"win"/f"{self.rid}.json"
        self.frozen_win.write_text(json.dumps({"prediction":{
            "raceID":self.rid,"raceDate":"2026-11-15",
            "startTime":"16:25","capturedAtJST":"2026-11-15T16:13:30+09:00",
            "horses":[{"number":1,"odds":4.1},{"number":2,"odds":3.3}]}}))
        (frozen/"wide_odds_live10"/f"{self.rid}.json").write_text(
            json.dumps({"raceID":self.rid,"raceDate":"2026-11-15",
                "startTime":"16:25","pairs":[{
                    "number1":1,"number2":2,"oddsLow":5.5,"oddsHigh":6.1}]}))

    def put_csv(self, *, day="20261115", observed="2026-11-15T16:14:00+09:00"):
        path=self.csv/day/f"{self.rid}.csv"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(
            "raceDate,raceID,startTime,source,sourceReference,"
            "permissionConfirmed,status,observedAtJST,asOfJST,"
            "betType,number1,number2,oddsLow,oddsHigh\n"
            f"2026-11-15,{self.rid},16:25,licensed_odds_provider,"
            "synthetic-licensed-export,true,pre_post,"
            f"{observed},2026-11-15T16:13:00+09:00,"
            "wide,1,2,5.1,5.9\n",
            encoding="utf-8")
        return path

    def run_bridge(self, **kwargs):
        return run_once(self.root,csv_source=self.csv,
                        json_inbox=self.inbox,accepted=self.accepted,
                        now=kwargs.pop("now",self.now),**kwargs)

    def test_csv_to_immutable_research_snapshot_end_to_end(self):
        self.put_csv()
        before=self.frozen_win.read_bytes()
        result=self.run_bridge()
        self.assertEqual(result["conversion"]["counts"]["converted"],1)
        self.assertEqual(result["ingestion"]["counts"]["accepted"],1)
        self.assertEqual(result["networkRequests"],0)
        self.assertFalse(result["liveBetsEnabled"])
        self.assertEqual(self.frozen_win.read_bytes(),before)
        accepted=self.accepted/self.date/f"{self.rid}.json"
        self.assertTrue(accepted.exists())
        receipt=json.loads(accepted.with_suffix(".receipt.json").read_text())
        self.assertTrue(receipt["prospectiveClockCheckPassed"])
        self.assertFalse(receipt["sourceAuthenticityVerified"])

    def test_only_the_active_jst_date_is_processed(self):
        self.put_csv()
        later=self.run_bridge(now=datetime.fromisoformat("2026-11-16T16:14:45+09:00"))
        self.assertEqual(later["conversion"]["counts"]["converted"],0)
        self.assertEqual(later["ingestion"]["counts"]["accepted"],0)
        self.assertFalse((self.accepted/self.date/f"{self.rid}.json").exists())

    def test_replay_cannot_be_accepted_in_live_pipeline(self):
        self.put_csv()
        late=self.run_bridge(now=datetime.fromisoformat("2026-11-15T16:20:00+09:00"))
        self.assertEqual(late["conversion"]["counts"]["converted"],1)
        self.assertEqual(late["ingestion"]["counts"]["accepted"],0)
        self.assertEqual(late["ingestion"]["counts"]["rejected"],1)
        self.assertFalse((self.accepted/self.date/f"{self.rid}.json").exists())

    def test_mac_bridge_does_not_log_empty_status_every_30_seconds(self):
        report=self.run_bridge()
        first,sig=should_log_report(report,None,0,watch_mode=True)
        later,_=should_log_report(report,sig,30,watch_mode=True)
        hour,_=should_log_report(report,sig,3601,watch_mode=True)
        self.assertTrue(first)
        self.assertFalse(later)
        self.assertTrue(hour)

    def test_mac_bridge_logs_new_acceptance_without_delay(self):
        empty=self.run_bridge()
        _,sig=should_log_report(empty,None,0,watch_mode=True)
        self.put_csv()
        changed=self.run_bridge()
        log,_=should_log_report(changed,sig,1,watch_mode=True)
        self.assertTrue(log)
        self.assertEqual(changed["ingestion"]["counts"]["accepted"],1)

    def test_empty_folder_fails_closed(self):
        response=self.run_bridge()
        self.assertEqual(response["conversion"]["counts"]["converted"],0)
        self.assertEqual(response["ingestion"]["counts"]["accepted"],0)
        self.assertFalse(response["anyProviderDataPresent"])

    def test_source_folder_cannot_overlap_accepted_directory(self):
        with self.assertRaisesRegex(ValueError,"must not overlap"):
            run_once(self.root,csv_source=self.accepted,
                     json_inbox=self.inbox,accepted=self.accepted,now=self.now)


if __name__=="__main__":
    unittest.main()
