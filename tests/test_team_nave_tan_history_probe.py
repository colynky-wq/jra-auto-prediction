"""No-network tests of documented Team-Nave historical TAN response."""
from __future__ import annotations

from datetime import datetime
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from team_nave_tan_history_probe import decode_tan_history,strict_json


class TeamNaveTanHistoryProbeTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.fromisoformat("2026-11-15T16:14:00+09:00")
        self.response={"ret":0,"msg":"","results":{"tan":[
            json.dumps({"01":"4.1","02":"10.0","rlsdttm":"202611151606"}),
            json.dumps({"01":"4.5","02":"9.4","rlsdttm":"202611151612"}),
            json.dumps({"01":"3.3","02":"11.1","rlsdttm":"202611151618"}),
            json.dumps({"01":"4.9","02":"7.5","rlsdttm":"LAST-ODDS"}),
        ]}}

    def test_selects_latest_timestamp_before_observation_not_after(self):
        e=decode_tan_history(self.response,at_jst=self.now)
        self.assertEqual(e["releasedAtJST"],"2026-11-15T16:12+09:00")
        self.assertEqual(e["ageMinutes"],2.0)
        self.assertEqual(e["prices"],{1:4.5,2:9.4})
        self.assertTrue(e["withinResearchMaxAge"])
        self.assertFalse(e["vendorWIDEFormatVerified"])
        self.assertFalse(e["oddsUsedForLiveTrading"])

    def test_last_odds_alone_does_not_establish_fresh_price(self):
        self.response["results"]["tan"]=[
            json.dumps({"01":"3.3","rlsdttm":"LAST-ODDS"})
        ]
        with self.assertRaisesRegex(ValueError,"no timestamped historical"):
            decode_tan_history(self.response,at_jst=self.now)

    def test_source_age_over_five_minutes_fails_quality_flag(self):
        self.response["results"]["tan"]=self.response["results"]["tan"][:1]
        e=decode_tan_history(self.response,at_jst=self.now)
        self.assertFalse(e["withinResearchMaxAge"])
        self.assertEqual(e["ageMinutes"],8.0)

    def test_response_error_is_not_silently_used(self):
        self.response["ret"]=-1
        with self.assertRaisesRegex(ValueError,"non-success"):
            decode_tan_history(self.response,at_jst=self.now)

    def test_malformed_horse_and_nan_are_rejected(self):
        self.response["results"]["tan"]=[
            json.dumps({"1.0":"2.2","rlsdttm":"202611151612"})
        ]
        with self.assertRaisesRegex(ValueError,"horse key"):
            decode_tan_history(self.response,at_jst=self.now)
        self.response["results"]["tan"]=[
            json.dumps({"01":"NaN","rlsdttm":"202611151612"})
        ]
        with self.assertRaisesRegex(ValueError,"invalid TAN odds"):
            decode_tan_history(self.response,at_jst=self.now)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"duplicate field"):
            strict_json('{"results":{},"results":{}}')


if __name__=="__main__":
    unittest.main()
