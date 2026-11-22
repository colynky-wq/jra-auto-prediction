"""Offline regression tests: 2026-only, frozen candidate, no promotion."""
from __future__ import annotations
from copy import deepcopy
import unittest
import hashlib

import numpy as np
import pandas as pd

from train_no_current_odds_challenger import FEATURES, VERSION
from evaluate_no_current_odds_2026_history import (
    verified_candidate, build_report, score, markdown,
)


class DummyModel:
    def feature_name(self):
        return list(FEATURES)
    def predict(self, df, validate_features=True):
        if "市場ロジット" in df.columns:
            raise AssertionError("current market made it into model input")
        return np.asarray([0.6, 0.2, 0.2, 0.3, 0.5, 0.2])


def fixture():
    data = pd.DataFrame({
        "日付": pd.to_datetime(["2026-11-07"] * 3 + ["2026-11-08"] * 3),
        "レースID": ["202601010101"] * 3 + ["202601010201"] * 3,
        "馬番": [1, 2, 3, 1, 2, 3],
        "着順": [1, 3, 2, 3, 1, 2],
        "市場確率": [0.6, 0.2, 0.2, 0.3, 0.5, 0.2],
    })
    for feature in FEATURES:
        data[feature] = 1.0
    return data


class NoOdds2026RetrospectiveTests(unittest.TestCase):
    def test_mismatching_candidate_sha_or_training_policy_blocks(self):
        raw = b"frozen model"
        meta = {
            "version": VERSION,
            "modelSha256": hashlib.sha256(raw).hexdigest(),
            "featureCount": 36,
            "features": list(FEATURES),
            "finalRefitThrough": "2025-12-31",
            "candidatePromotionApproved": False,
            "autoBetEnabled": False,
            "formalPredictionAIChanged": False,
        }
        verified_candidate(meta, raw)
        bad = deepcopy(meta)
        bad["finalRefitThrough"] = "2026-11-21"
        with self.assertRaises(ValueError):
            verified_candidate(bad, raw)
        bad = deepcopy(meta)
        bad["modelSha256"] = "changed"
        with self.assertRaises(ValueError):
            verified_candidate(bad, raw)
        bad = deepcopy(meta)
        bad["candidatePromotionApproved"] = True
        with self.assertRaises(ValueError):
            verified_candidate(bad, raw)

    def test_scoring_excludes_current_market_model_feature(self):
        data = fixture()
        events = score(data, DummyModel())
        self.assertEqual(events["win"]["raceCount"], 2)
        self.assertEqual(events["top2"]["raceCount"], 2)
        self.assertEqual(events["top3"]["raceCount"], 2)
        self.assertEqual(events["win"]["horseRows"], 6)
        self.assertIsNone(events["win"]["pairedRaceBootstrap95"])
        self.assertIn("pairedRaceMeanBrierDelta", events["win"])
        self.assertAlmostEqual(
            events["win"]["pairedRaceMeanBrierDelta"],
            events["win"]["candidateMinusFinalMarketBrier"], places=9
        )

    def test_report_never_claims_prospective_validation(self):
        data = fixture()
        meta = {"version": VERSION, "finalRefitThrough": "2025-12-31",
                "modelSha256": "frozen"}
        report = build_report(meta, data, data,
                              score(data, DummyModel()), "frozen")
        self.assertEqual(report["status"], "retrospective_research_only")
        self.assertFalse(report["candidatePromotionApproved"])
        self.assertFalse(report["autoBetEnabled"])
        self.assertFalse(report["formalPredictionAIChanged"])
        self.assertIn("not available at T-10", " ".join(report["limitations"]))
        self.assertIn("reconstructed offline", markdown(report))
        bad = data.copy()
        bad.loc[0, "日付"] = pd.Timestamp("2025-05-01")
        with self.assertRaises(ValueError):
            build_report(meta, data, bad, score(data, DummyModel()), "frozen")


if __name__ == "__main__":
    unittest.main()
