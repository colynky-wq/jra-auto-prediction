from itertools import combinations
import unittest

import numpy as np
import pandas as pd

from research_longshot_wide_coherent import Race, combinations_for_field
from research_wide_four_way import select_cards, quote_summary, COLUMNS


class WideFourWayTests(unittest.TestCase):
    def test_shared_three_ticket_and_quote_support(self):
        pairs, _, edges = combinations_for_field(4)
        field = Race(pd.Timestamp("2026-01-01"), np.log(np.full((4, 1), .25)), edges,
                     None, np.arange(6), np.arange(6), 6)
        models = {name: {"spec": "market"} for name in ["market", "coherentV2"]}
        cards = select_cards(field, models, pairs)
        self.assertAlmostEqual(cards["market", "pairRank"][0], .75)
        self.assertAlmostEqual(cards["market", "maximumAnyHit"][0], 1)
        quotes = {pair: (2, 3) for pair in combinations(range(4), 2)}
        self.assertTrue(all(not tickets for _, tickets in select_cards(field, models, pairs, quotes).values()))
        for pair in [(0, 1), (0, 2), (2, 3)]:
            quotes[pair] = (3, 4)
        plans = select_cards(field, models, pairs, quotes)
        self.assertTrue(all({tuple(pairs[i]) for i, _ in picks} == {(0, 1), (0, 2), (2, 3)}
                            for _, picks in plans.values()))

    def test_no_settlement_is_not_a_zero_return(self):
        summaries = quote_summary(pd.DataFrame(columns=COLUMNS), 1)
        self.assertEqual(len(summaries), 4)
        self.assertTrue(all(row["returnPct"] is None and row["skippedRaces"] == 1 for row in summaries))


if __name__ == "__main__":
    unittest.main()
