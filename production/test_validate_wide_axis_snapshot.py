import json
from pathlib import Path
import tempfile
import unittest
from validate_wide_axis_snapshot import check


class WideAxisReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/"reports/20261122/wide_axis_v1").mkdir(parents=True)
        (self.root/"reports/20261121/wide_axis_v1").mkdir(parents=True)
        for date, rid in [("20261121","202605050401"),("20261122","202605050501")]:
            (self.root/"reports"/date/"wide_axis_v1"/(rid+".json")).write_text('{"frozen":true}')
        self.report={"schemaVersion":1,"ruleVersion":"wide-axis-v1-20261014",
                     "generatedAtJST":"2026-11-22 18:15:00",
                     "autoBetEnabled":False,"moneyAllocationEnabled":False,
                     "counts":{"recordedRaces":2,"held":1,"awaitingResults":0,
                               "matched":1,"entrantChanged":0,"invalid":0,
                               "selectedBets":2}}
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        (self.root/"reports/WideAxisShadow.json").write_text(json.dumps(self.report))

    def test_valid_verified_snapshot(self):
        self.assertEqual(check(self.root),2)

    def test_stale_summary_blocked_when_new_frozen_record_appears(self):
        (self.root/"reports/20261122/wide_axis_v1/202605050502.json").write_text("{}")
        with self.assertRaisesRegex(ValueError,"stale"):
            check(self.root)

    def test_invalid_money_allocation_or_rule_never_published(self):
        self.report["moneyAllocationEnabled"]=True
        self.save()
        with self.assertRaisesRegex(ValueError,"forbidden"):
            check(self.root)
        self.report["moneyAllocationEnabled"]=False
        self.report["ruleVersion"]="different-rule"
        self.save()
        with self.assertRaisesRegex(ValueError,"identity"):
            check(self.root)

    def test_no_synthetic_reconciliation_of_status_counts(self):
        self.report["counts"]["matched"]=2
        self.save()
        with self.assertRaisesRegex(ValueError,"partition"):
            check(self.root)

    def test_no_working_report_fails_closed(self):
        (self.root/"reports/WideAxisShadow.json").unlink()
        with self.assertRaisesRegex(ValueError,"summary"):
            check(self.root)


if __name__=="__main__":
    unittest.main()
