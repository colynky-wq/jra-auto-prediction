"""Tests for frozen LightGBM market feature introspection, synthetic trees."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"production"))
from market_logit_feature_audit import audit_model_text


class MarketLogitAuditTests(unittest.TestCase):
    SAMPLE = """tree
version=v4
feature_names=値 市場ロジット 他
Tree=0
split_feature=1 0
split_gain=4 1
Tree=1
split_feature=2 1
split_gain=2 3
"""

    def test_market_logit_actual_usage(self):
        x=audit_model_text(self.SAMPLE)
        self.assertEqual(x["marketLogitFeatureIndex"],1)
        self.assertEqual(x["marketLogitSplitCount"],2)
        self.assertEqual(x["marketLogitSplitGain"],7.0)
        self.assertEqual(x["marketLogitSplitSharePct"],50.0)
        self.assertEqual(x["marketLogitGainSharePct"],70.0)
        self.assertFalse(x["productionModelChanged"])
        self.assertFalse(x["featureAblationPerformed"])

    def test_no_market_feature_reports_absent(self):
        s=self.SAMPLE.replace("値 市場ロジット 他","値 非市場 他")
        x=audit_model_text(s)
        self.assertFalse(x["marketLogitPresent"])
        self.assertEqual(x["marketLogitSplitCount"],0)

    def test_invalid_split_gain_rejected(self):
        with self.assertRaisesRegex(ValueError,"invalid model split"):
            audit_model_text(self.SAMPLE.replace("split_gain=2 3","split_gain=NaN 3"))

    def test_invalid_feature_index_rejected(self):
        with self.assertRaisesRegex(ValueError,"out of range"):
            audit_model_text(self.SAMPLE.replace("split_feature=2 1",
                                                  "split_feature=5 1"))


if __name__=="__main__":
    unittest.main()
