import unittest
import numpy as np

from research_longshot_top3_ranker import (
    RANK_FEATURES, evaluate_ranker, grouped, rank_scores, select_rank_weight,
)
from research_longshot_top3_v2 import top_picks
from test_research_longshot_top3_v2 import FakeModel
import test_research_longshot_top3_v2 as fixtures


class RankerTests(unittest.TestCase):
    def data(self, year=2024):
        return fixtures.SelectionTests().data(year)

    def test_race_groups_remain_contiguous_after_shuffle(self):
        d, groups = grouped(self.data().sample(frac=1, random_state=4))
        self.assertEqual(groups.tolist(), [3, 3])
        self.assertEqual(d["レースID"].nunique(), 2)

    def test_ranking_scores_require_no_outcomes(self):
        d = self.data().drop(columns=["着順", "日付", "馬名"])
        score = rank_scores(d, FakeModel(RANK_FEATURES), 1.)
        self.assertEqual(score.shape, (6,))
        self.assertTrue(np.isfinite(score).all())

    def test_no_selection_with_test_years(self):
        for year in (2025, 2026):
            with self.assertRaisesRegex(ValueError, "2024 only"):
                select_rank_weight(self.data(year), FakeModel(RANK_FEATURES))

    def test_identical_methods_have_zero_paired_difference(self):
        d = self.data()
        score = d["市場3着内基準"].to_numpy()
        previous = top_picks(d, score)
        previous["marketFinish"] = previous["着順"]
        previous["control36Finish"] = previous["着順"]
        r, picked = evaluate_ranker(d, score, previous)
        self.assertFalse(r["rankingScoresAreProbabilities"])
        self.assertIn("rankingScore", picked)
        self.assertNotIn("probability", picked)
        self.assertEqual(r["versusControl36"]["hitRateDelta"], 0.)


if __name__ == "__main__":
    unittest.main()
