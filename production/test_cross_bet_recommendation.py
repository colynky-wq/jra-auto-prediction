import unittest

import cross_bet_recommendation as cross


class CrossBetRecommendationTests(unittest.TestCase):
    def c(self, kind, key, low, high=None, probability=10):
        if high is None:
            high = low
        return {
            'betType': kind,
            'betTypeLabel': cross.BET_LABELS[kind],
            'id': key,
            'label': key,
            'probabilityPct': probability,
            'marketProbabilityPct': None,
            'oddsLow': None,
            'oddsHigh': None,
            'expectedMultiplierLow': low,
            'expectedMultiplierHigh': high,
        }

    def race(self):
        return {'raceDate': '2026-10-24', 'raceID': '202605040101', 'venue': '東京',
                'raceNumber': 1, 'startTime': '10:05', 'raceName': 'テスト'}

    def full_states(self):
        return {kind: 'awaiting_results' for kind in cross.BET_TYPES}

    def test_conservative_lower_bound_wins_even_if_other_high_bound_is_larger(self):
        a = self.c('place', '01', 1.08, 1.90, 25)
        b = self.c('quinella', '0102', 1.12, 1.12, 8)
        value = cross.build_recommendation(self.race(), [a, b], self.full_states())
        self.assertEqual(value['decision'], 'RESEARCH_CANDIDATE')
        self.assertEqual(value['candidate']['betType'], 'quinella')
        self.assertEqual(value['candidate']['id'], '0102')
        self.assertEqual(value['breakEvenExpectedMultiplier'], 1.0)

    def test_pass_when_every_lower_bound_is_below_break_even(self):
        value = cross.build_recommendation(
            self.race(), [self.c('win', '01', .99), self.c('wide', '0102', .97)], self.full_states())
        self.assertEqual(value['decision'], 'PASS')
        self.assertIsNone(value['candidate'])
        self.assertEqual(value['bestObserved']['expectedMultiplierLow'], .99)
        self.assertFalse(value['formalRecommendation'])
        self.assertFalse(value['autoBetEnabled'])
        self.assertFalse(value['stakeSizingEnabled'])

    def test_no_odds_when_pool_empty(self):
        value = cross.build_recommendation(self.race(), [], {})
        self.assertEqual(value['decision'], 'NO_ODDS')
        self.assertIsNone(value['recommendedBetType'])
        self.assertEqual(value['usableOddsBetTypes'], [])
        self.assertFalse(value['coverageComplete'])

    def test_win5_is_not_part_of_cross_bet_universe(self):
        self.assertNotIn('win5', cross.BET_TYPES)
        self.assertNotIn('win5', cross.BET_LABELS)
        value = cross.build_recommendation(
            self.race(), [self.c('trifecta', '010203', 1.3)], self.full_states())
        self.assertTrue(value['win5Excluded'])
        self.assertNotIn('win5', value['evaluatedBetTypes'])

    def test_deterministic_tie_break(self):
        rows = [self.c('win', '02', 1.1, probability=9), self.c('win', '01', 1.1, probability=10)]
        ranked = cross.rank_candidates(rows)
        self.assertEqual([r['id'] for r in ranked], ['01', '02'])

    def test_duplicate_candidate_is_rejected(self):
        row = self.c('win', '01', 1.1)
        with self.assertRaises(ValueError):
            cross.rank_candidates([row, dict(row)])


if __name__ == '__main__':
    unittest.main()
