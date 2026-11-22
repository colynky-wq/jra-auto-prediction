"""Network-free prospective matched auditor regression tests."""
from datetime import datetime
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from market_shrink_shadow import JST, _write_first
from market_anchor_shadow import build, output_path
from evaluate_market_anchor_forward import evaluate
from test_market_anchor_shadow import fixture


def stamp():
    return datetime(2026,11,23,9,50,5,tzinfo=JST)


class MarketAnchorForwardReviewTests(unittest.TestCase):
    def audited(self):
        race,strategy,_=fixture()
        audit={
            "projectionState":"verified",
            "generatedAtJST":"2026-11-23T21:00:00+09:00",
            "aligned":{"countsAreCurrent":True,"racesObserved":1,"matchedResultRaces":1},
            "quality":{"issues":[],"resultExclusions":[]},
            "races":[{
                "raceDate":race["raceDate"],"raceID":race["raceID"],
                "resultState":"matched","inputFingerprint":"VALID",
                "actualFinish":{"1":1,"2":2,"3":3}
            }],
        }
        for horse in race["horses"]:
            horse.update({
                "aiProbability":horse["marketProbability"],
                "aiTop2Probability":60.0,
                "marketTop2Probability":60.0,
                "aiTop3Probability":99.0,
                "marketTop3Probability":99.0,
            })
        return race,strategy,audit

    def test_missing_market_candidate_is_visible_not_fabricated(self):
        race,strategy,audit=self.audited()
        with TemporaryDirectory() as temp:
            report=evaluate(Path(temp),audit,strategy,"frozenSHA")
            self.assertEqual(report["counts"]["missingCandidateSinceActivation"],1)
            self.assertEqual(report["counts"]["eligibleAlignedSinceActivation"],1)
            self.assertEqual(report["counts"]["firstFrozenSnapshots"],0)
            self.assertEqual(report["counts"]["candidateCaptureCoveragePct"],0.0)
            self.assertEqual(report["events"]["win"]["marketCalibrated"]["pairedRaces"],0)
            self.assertFalse(output_path(temp,race).exists())

    def test_calibrated_candidate_paired_with_original_capture(self):
        race,strategy,audit=self.audited()
        with TemporaryDirectory() as temp:
            root=Path(temp)
            _write_first(output_path(root,race),build(
                race,strategy,"frozenSHA",clock=stamp))
            report=evaluate(root,audit,strategy,"frozenSHA",load_bundle=lambda *_:{
                "fingerprint":"VALID","prediction":race})
            self.assertEqual(report["counts"]["firstFrozenSnapshots"],1)
            self.assertEqual(report["counts"]["pairedMatchedWithCalibrated"],1)
            self.assertEqual(report["counts"]["pairedMatchedWithHistory"],0)
            self.assertEqual(report["counts"]["candidateCaptureCoveragePct"],100.0)
            self.assertEqual(report["events"]["win"]["marketCalibrated"]["pairedRaces"],1)
            self.assertEqual(report["events"]["win"]["historyAugmented"]["pairedRaces"],0)
            self.assertFalse(report["candidatePromotionApproved"])

    def test_conflicting_model_or_frozen_result_rejected(self):
        race,strategy,audit=self.audited()
        with TemporaryDirectory() as temp:
            root=Path(temp)
            _write_first(output_path(root,race),build(
                race,strategy,"frozenSHA",clock=stamp))
            with self.assertRaisesRegex(ValueError,"strategy/version mismatch"):
                evaluate(root,audit,strategy,"changedSHA")
            audit["projectionState"]="failed"
            with self.assertRaisesRegex(ValueError,"verified audit"):
                evaluate(root,audit,strategy,"frozenSHA")


if __name__=="__main__":
    unittest.main()
