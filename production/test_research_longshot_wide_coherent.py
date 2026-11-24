import unittest
from itertools import permutations

import numpy as np
import pandas as pd

from research_longshot_wide_coherent import (
    build_races,
    combinations_for_field,
    fit,
    market_triples,
    outcome_probabilities,
    pair_marginals,
    probability_of_any,
    scores,
)
from research_longshot_wide_joint import joint, pair_frame


class TestCoherentWide(unittest.TestCase):
    def test_uneven_market_matches_ordered_finishes_and_wide_baseline(self):
        weights = np.array([0.45, 0.25, 0.17, 0.08, 0.05])
        pairs, triples, edges = combinations_for_field(len(weights))
        p = market_triples(weights, triples)
        expected = [
            sum(weights[i] * weights[j] * weights[k] / ((1 - weights[i]) * (1 - weights[i] - weights[j]))
                for i, j, k in permutations(triple))
            for triple in triples
        ]
        np.testing.assert_allclose(p, expected)
        marginal = np.bincount(edges.ravel(), weights=np.repeat(p, 3))
        np.testing.assert_allclose(marginal, joint(weights)[pairs[:, 0], pairs[:, 1]])
        self.assertAlmostEqual(p.sum(), 1.0)
        self.assertAlmostEqual(marginal.sum(), 3.0)

    def test_overlapping_hits_count_once_in_portfolio(self):
        pairs, triples, edges = combinations_for_field(4)
        p = market_triples(np.ones(4), triples)
        selected = [list(map(tuple, pairs)).index(pair) for pair in [(0, 1), (0, 2)]]
        self.assertAlmostEqual(probability_of_any(edges, p, selected), 0.75)
        self.assertAlmostEqual(probability_of_any(edges, p, []), 0.0)

    def test_fit_and_score_preserve_full_field_and_holdout(self):
        def example(race_id, date, popularity):
            horses = [1, 3, 4, 5, 6, 8, 10, 12]
            weights = np.geomspace(1.0, 0.03, len(horses))
            full = pd.DataFrame({
                "レースID": race_id, "日付": pd.Timestamp(date), "馬番": horses,
                "馬名": [f"runner{i}" for i in horses], "頭数": 8, "着順": [1, 2, 4, 5, 6, 7, 3, 8],
                "市場確率": weights / weights.sum(), "人気": popularity,
                "単勝オッズ": np.geomspace(2, 50, len(horses)),
            })
            short = full.loc[(full["人気"] >= 6) & (full["単勝オッズ"] >= 10)].copy()
            short["市場3着内基準"] = np.linspace(0.15, 0.08, len(short))
            pairs, _ = pair_frame(full, short, short["市場3着内基準"] + 0.01, np.arange(len(short)))
            return full, pairs

        full, pairs = example("a", "2025-06-01", [1, 2, 3, 4, 5, 5, 7, 5])
        race, = build_races(full, pairs)
        self.assertEqual(len(race.features), 56)
        self.assertEqual(len(race.candidate_edges), 7)
        self.assertEqual(race.pair_count, 28)
        model, = fit([race], "coherentV2", [0.01])
        test_full, test_pairs = example("b", "2026-06-01", [1, 2, 3, 4, 5, 6, 7, 8])
        test = build_races(test_full, test_pairs)
        fitted = outcome_probabilities(test[0], model)
        self.assertAlmostEqual(fitted.sum(), 1.0)
        self.assertAlmostEqual(pair_marginals(test[0], fitted).sum(), 3.0)
        quality, p = scores(test, model, test_pairs, evaluate_tickets=True)
        self.assertEqual(len(p), len(test_pairs))
        self.assertAlmostEqual(quality["portfolio"]["1"]["meanPredictedProbabilityAnyHit"], max(p))
        with self.assertRaisesRegex(ValueError, "restricted to 2025"):
            fit(test, "coherentV2", [0.01])
        with self.assertRaisesRegex(ValueError, "longshot identities"):
            build_races(test_full, test_pairs.iloc[1:])


if __name__ == "__main__":
    unittest.main()
