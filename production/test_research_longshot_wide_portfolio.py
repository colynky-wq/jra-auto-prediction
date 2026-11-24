from itertools import combinations
import unittest

import numpy as np
import pandas as pd

from research_longshot_wide_coherent import (
    Race,
    combinations_for_field,
    pair_marginals,
    probability_of_any,
)
from research_longshot_wide_portfolio import maximum_any_three


class ExactWidePortfolioTests(unittest.TestCase):
    def race(self, size, candidate_edges):
        pairs, _, edges = combinations_for_field(size)
        return Race(
            pd.Timestamp("2026-01-05"), np.empty((len(edges), 0)), edges, None,
            np.arange(len(candidate_edges)), np.asarray(candidate_edges), len(pairs),
        )

    def test_overlap_changes_the_best_three_tickets(self):
        race = self.race(4, np.arange(6))
        outcomes = np.full(4, 0.25)
        marginals = pair_marginals(race, outcomes)
        ranked = race.candidate_edges
        self.assertEqual(probability_of_any(race.edges, outcomes, ranked[:3]), 0.75)
        best = maximum_any_three(race, outcomes, ranked, marginals)
        self.assertEqual(probability_of_any(race.edges, outcomes, best), 1)

    def test_exact_search_agrees_with_exhaustive_top_three_sets(self):
        rng = np.random.default_rng(20261125)
        for field in range(4, 9):
            _, _, edges = combinations_for_field(field)
            for _ in range(20):
                count = field * (field - 1) // 2
                eligible = np.sort(rng.choice(count, rng.integers(3, min(count, 15) + 1), replace=False))
                race = self.race(field, eligible)
                weights = rng.dirichlet(np.full(len(edges), rng.choice((0.1, 1, 10))))
                p = pair_marginals(race, weights)
                order = eligible[np.argsort(-p[eligible], kind="stable")]
                picked = maximum_any_three(race, weights, order, p)
                expected = max(probability_of_any(edges, weights, group) for group in combinations(eligible, 3))
                self.assertEqual(len(set(picked)), 3)
                self.assertTrue(set(picked) <= set(eligible))
                self.assertAlmostEqual(probability_of_any(edges, weights, picked), expected, places=11)


if __name__ == "__main__":
    unittest.main()
