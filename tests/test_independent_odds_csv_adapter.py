"""Synthetic regression tests for the offline licensed CSV adapter."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "production"))
from independent_odds_csv_adapter import (convert_csv_bytes, convert_one,
                                          scan_csv_once)
from independent_odds_dropwatch import ingest_one


class CsvAdapterTests(unittest.TestCase):
    HEADER = (
        "raceDate,raceID,startTime,source,sourceReference,"
        "permissionConfirmed,status,observedAtJST,asOfJST,"
        "betType,number1,number2,oddsLow,oddsHigh"
    )

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root=Path(self.dir.name)
        self.source=self.root/"licensed_odds_csv"
        self.output=self.root/"licensed_odds_drop"
        self.accepted=self.root/"research_independent_odds"
        self.source_file=self.source/"20261115"/"202608050412.csv"
        self.source_file.parent.mkdir(parents=True)
        self.meta={
            "raceDate":"2026-11-15","raceID":"202608050412",
            "startTime":"16:25","source":"licensed_odds_provider",
            "sourceReference":"synthetic-provider-receipt-1",
            "permissionConfirmed":"true","status":"pre_post",
            "observedAtJST":"2026-11-15T16:14:00+09:00",
            "asOfJST":"2026-11-15T16:13:00+09:00",
        }
        self.rows=[
            dict(self.meta,betType="win",number1="1",number2="",oddsLow="5.2",oddsHigh=""),
            dict(self.meta,betType="wide",number1="2",number2="1",oddsLow="8.1",oddsHigh="8.9"),
        ]

    def data(self, rows=None):
        buf=StringIO()
        writer=csv.DictWriter(buf,fieldnames=self.HEADER.split(","))
        writer.writeheader()
        writer.writerows(self.rows if rows is None else rows)
        return buf.getvalue().encode("utf-8")

    def save(self):
        self.source_file.write_bytes(self.data())

    def test_valid_mixed_win_wide_and_reversed_pair(self):
        x=convert_csv_bytes(self.data(),race_id="202608050412",date_folder="20261115")
        self.assertEqual(x["winOdds"],[{"number":1,"odds":5.2}])
        self.assertEqual(x["wideOdds"],[{
            "number1":1,"number2":2,"oddsLow":8.1,"oddsHigh":8.9
        }])
        self.assertFalse(x.get("sourceAuthenticityVerified",False))

    def test_different_asof_times_in_one_file_rejected(self):
        rows=[dict(x) for x in self.rows]
        rows[1]["asOfJST"]="2026-11-15T16:12:00+09:00"
        with self.assertRaisesRegex(ValueError,"mixed race/source"):
            convert_csv_bytes(self.data(rows))

    def test_duplicate_pair_rejected_regardless_of_order(self):
        rows=[dict(x) for x in self.rows]
        rows.append(dict(self.meta,betType="wide",number1="1",number2="2",
                         oddsLow="8.3",oddsHigh="9.2"))
        with self.assertRaisesRegex(ValueError,"duplicate"):
            convert_csv_bytes(self.data(rows))

    def test_permission_must_be_explicitly_true(self):
        rows=[dict(x,permissionConfirmed="false") for x in self.rows]
        with self.assertRaisesRegex(ValueError,"permissionConfirmed"):
            convert_csv_bytes(self.data(rows))

    def test_noninteger_horse_number_rejected(self):
        rows=[dict(x) for x in self.rows]
        rows[0]["number1"]="1.7"
        with self.assertRaisesRegex(ValueError,"non-integer horse number"):
            convert_csv_bytes(self.data(rows))

    def test_filename_identity_mismatch_rejected(self):
        self.save()
        wrong=self.source_file.with_name("202608050411.csv")
        self.source_file.rename(wrong)
        out=convert_one(wrong,self.source,self.output)
        self.assertEqual(out["status"],"rejected")

    def test_never_overwrites_earlier_normalization(self):
        self.save()
        first=convert_one(self.source_file,self.source,self.output)
        self.assertEqual(first["status"],"converted")
        self.assertEqual(convert_one(self.source_file,self.source,self.output)["status"],
                         "already_converted")
        original=(self.output/"20261115"/"202608050412.json").read_bytes()
        self.rows[0]["oddsLow"]="6.1"
        self.save()
        conflict=convert_one(self.source_file,self.source,self.output)
        self.assertEqual(conflict["reason"],"immutable_conversion_conflict")
        self.assertEqual((self.output/"20261115"/"202608050412.json").read_bytes(),
                         original)

    def test_folder_scan_no_network(self):
        self.save()
        result=scan_csv_once(self.source,self.output)
        self.assertEqual(result["counts"]["converted"],1)
        self.assertEqual(result["networkRequests"],0)

    def test_csv_normalization_flows_into_frozen_comparison(self):
        self.save()
        self.assertEqual(convert_one(self.source_file,self.source,self.output)["status"],
                         "converted")
        frozen=self.root/"reports"/"20261115"
        (frozen/"all_bet_v1"/"win").mkdir(parents=True)
        (frozen/"wide_odds_live10").mkdir(parents=True)
        name="202608050412.json"
        (frozen/"all_bet_v1"/"win"/name).write_text(json.dumps({
            "prediction":{"raceID":"202608050412","raceDate":"2026-11-15",
                          "startTime":"16:25","capturedAtJST":"2026-11-15T16:13:30+09:00",
                          "horses":[{"number":1,"odds":4.9},{"number":2,"odds":6.0}]}}))
        (frozen/"wide_odds_live10"/name).write_text(json.dumps({
            "raceID":"202608050412","raceDate":"2026-11-15","startTime":"16:25",
            "pairs":[{"number1":1,"number2":2,"oddsLow":7.9,"oddsHigh":8.8}]}))
        result=ingest_one(self.root,self.output,self.accepted,
                          self.output/"20261115"/name)
        self.assertEqual(result["status"],"accepted")
        out=self.accepted/"20261115"/name
        self.assertEqual(json.loads(out.read_text())["winOdds"][0]["odds"],5.2)
        self.assertTrue(out.with_suffix(".receipt.json").exists())


if __name__=="__main__":
    unittest.main()
