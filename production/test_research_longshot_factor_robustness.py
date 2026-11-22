"""Network-free tests for fixed longshot subgroup diagnostics (not selection)."""
from __future__ import annotations
import hashlib
import unittest

import numpy as np
import pandas as pd

from train_no_current_odds_challenger import FEATURES
from train_longshot_top3 import VERSION as LONGSHOT_VERSION
from research_longshot_factor_robustness import (
    ODDS_LABELS, POPULARITY_LABELS, PRIOR_FEATURES,
    verify_meta, partition, summarise, segment_report,
    reliable_across_years, case_examples,
)


def fixture(year=2026):
    rows = []
    for day in (1, 2):
        for i, (odd, pop, finish) in enumerate((
            (10., 6, 3), (19.9, 7, 7), (20., 8, 8),
            (49.9, 9, 2), (50., 12, 10), (100., 13, 13),
        )):
            row = {"日付": pd.Timestamp(year, 3, day),
                   "レースID": f"R{year}{day}", "馬番": i + 1,
                   "馬名": f"H{i}", "着順": finish, "人気": pop,
                   "単勝オッズ": odd, "芝ダ": "芝" if day == 1 else "ダ",
                   "距離": 1400 if day == 1 else 2000}
            for f in FEATURES:
                row[f] = float(i + 1)
            row["前走着順率f"] = i / 6.
            row["同芝ダ回数c"] = [0, 1, 2, 3, 4, 5][i]
            rows.append(row)
    data = pd.DataFrame(rows)
    base = np.array([0.24, 0.2, 0.18, 0.12, 0.08, 0.03] * 2)
    cand = np.array([0.28, 0.22, 0.20, 0.13, 0.075, 0.025] * 2)
    return data, base, cand


class FakeModel:
    def __init__(self):
        self.feature_names = list(FEATURES)
        self.seen_cols = None
    def predict(self, frame, pred_contrib=False, validate_features=False):
        self.seen_cols = list(frame.columns)
        assert pred_contrib and validate_features
        assert "着順" not in frame.columns
        assert "市場ロジット" not in frame.columns
        m = np.zeros((len(frame), len(FEATURES) + 1))
        m[:, 0] = 0.5
        m[:, 1] = 0.25
        return m


class LongshotRobustnessTests(unittest.TestCase):
    def test_register_partition_edges_and_correct_cohorts(self):
        frame,market,cand = fixture()
        d=partition(frame,cand,market)
        self.assertEqual(d["単勝オッズ帯"].iloc[:6].tolist(),
                         ["10–19.9", "10–19.9", "20–49.9",
                          "20–49.9", "50–99.9", "100+"])
        self.assertEqual(d["人気順位帯"].iloc[:6].tolist(),
                         ["6–8","6–8","6–8","9–12","9–12","13+"])
        self.assertEqual(set(d["芝ダ区分"]),{"芝","ダート"})
        self.assertEqual(set(d["距離帯"]),{"≤1400m","1801–2200m"})
        self.assertEqual(d["同馬場経験"].iloc[:6].tolist(),
                         ["0走","1–2走","1–2走","3走以上","3走以上","3走以上"])
        self.assertTrue(all(f in d.columns for f in FEATURES))
        self.assertEqual(int(d["actual"].sum()),4)
        self.assertEqual(len(segment_report(d)["単勝オッズ帯"]),4)

    def test_no_nonlongshot_or_invalid_probabilities_are_admitted(self):
        frame,market,cand=fixture()
        frame.loc[0,"人気"]=5
        with self.assertRaisesRegex(ValueError,"non-longshot"):
            partition(frame,cand,market)
        frame,market,cand=fixture()
        cand[0]=float("nan")
        with self.assertRaisesRegex(ValueError,"invalid prediction"):
            partition(frame,cand,market)
        frame,market,cand=fixture(2024)
        with self.assertRaisesRegex(ValueError,"evaluation periods"):
            partition(frame,cand,market)

    def test_subgroups_are_descriptive_and_cannot_reweight_training(self):
        frame,market,cand=fixture()
        p=partition(frame,cand,market)
        stats=summarise(p)
        self.assertEqual(stats["horseRows"],12)
        self.assertEqual(stats["distinctRaces"],2)
        self.assertEqual(stats["top3ActualCount"],4)
        self.assertFalse(stats["adequateSample"])
        self.assertIsNone(stats["equalRaceBootstrap95"])
        whole=segment_report(p)
        expected = {"ALL", "単勝オッズ帯", "人気順位帯", "芝ダ区分", "距離帯",
                    "同馬場経験", "前走着順位置", "AI市場比"}
        self.assertEqual(set(whole),expected)
        replicated=reliable_across_years(whole,whole)
        self.assertEqual(replicated,[]) # inadequate groups cannot become rules

    def test_cases_include_successes_and_failures_with_no_labels_as_inputs(self):
        frame,market,cand=fixture()
        # Force a meaningful uplift for each high-odds subgroup.
        cand[4]=0.14
        cand[5]=0.07
        cand[10]=0.14
        cand[11]=0.07
        d=partition(frame,cand,market)
        m=FakeModel()
        examples=case_examples(d,m,max_per_outcome=2)
        self.assertGreater(len(examples),0)
        self.assertTrue(any(x["actuallyTop3"] for x in examples))
        self.assertTrue(any(not x["actuallyTop3"] for x in examples))
        self.assertEqual(m.seen_cols,list(FEATURES))
        self.assertTrue(all(x["selectionUsesKnownOutcomeForIllustration"]
                            for x in examples))

    def test_model_identity_fails_closed_on_future_training(self):
        raw=b"model"
        meta={"version":LONGSHOT_VERSION,
              "modelSha256":hashlib.sha256(raw).hexdigest(),
              "features":list(FEATURES),"featureCount":36,
              "popularityMinimum":6,"winOddsMinimum":10.0,
              "trainThroughFor2025Test":"2023-12-31",
              "refitThroughFor2026Test":"2025-12-31",
              "yearUsedForEarlyStoppingAndMultiplier":2024,
              "selectedResidualMultiplier":1.0,
              "researchOnly":True,"candidatePromotionApproved":False,
              "formalPredictionAIChanged":False,"autoBetEnabled":False}
        verify_meta(meta,raw)
        meta["refitThroughFor2026Test"]="2026-11-21"
        with self.assertRaisesRegex(ValueError,"training or underdog contract"):
            verify_meta(meta,raw)


if __name__=="__main__":
    unittest.main()
