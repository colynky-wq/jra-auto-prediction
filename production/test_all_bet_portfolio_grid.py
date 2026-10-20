import unittest

import research_all_bet_portfolio_grid as grid


def sample(race, combo, ev, *, hit=False, payout=0, bet='ワイド', settled=True):
    return {
        'race_id': race, 'bet_type': bet, 'combo': combo,
        'model_ev': ev, 'settled': settled, 'hit': hit,
        'payout_yen_per_100': payout,
    }


class PortfolioGridTests(unittest.TestCase):
    def test_top_k_keeps_highest_ev_within_each_race(self):
        rows = [
            sample('R1', '1-2', 1.1),
            sample('R1', '1-3', 1.4),
            sample('R1', '1-4', 1.2),
            sample('R2', '2-3', 1.3),
            sample('R2', '2-4', 0.9),
        ]
        selected = grid.select_top_k(rows, bet='ワイド', threshold=1.0, top_k=2)
        self.assertEqual([(r['race_id'], r['combo']) for r in selected], [
            ('R1', '1-3'), ('R1', '1-4'), ('R2', '2-3')
        ])

    def test_unsettled_rows_are_not_staked(self):
        rows = [sample('R1', '1-2', 1.5, settled=False)]
        result = grid.evaluate(rows, thresholds=(1.0,), top_ks=(1,))
        wide = next(r for r in result if r['bet_type'] == 'ワイド')
        self.assertEqual(wide['selected_bets'], 0)
        self.assertEqual(wide['stake_yen'], 0)
        self.assertIsNone(wide['roi_pct'])

    def test_trigami_is_race_level_total_return_vs_total_stake(self):
        rows = [
            sample('R1', '1-2', 1.4, hit=True, payout=180),
            sample('R1', '1-3', 1.3, hit=False, payout=0),
            sample('R1', '1-4', 1.2, hit=False, payout=0),
        ]
        result = grid.evaluate(rows, thresholds=(1.0,), top_ks=(3,), stake_yen=100)
        wide = next(r for r in result if r['bet_type'] == 'ワイド')
        self.assertEqual(wide['selected_bets'], 3)
        self.assertEqual(wide['hit_races'], 1)
        self.assertEqual(wide['trigami_races'], 1)
        self.assertEqual(wide['return_yen'], 180)
        self.assertAlmostEqual(wide['roi_pct'], 60.0)

    def test_top_one_can_remove_trigami_created_by_extra_tickets(self):
        rows = [
            sample('R1', '1-2', 1.4, hit=True, payout=180),
            sample('R1', '1-3', 1.3),
            sample('R1', '1-4', 1.2),
        ]
        result = grid.evaluate(rows, thresholds=(1.0,), top_ks=(1, 3), stake_yen=100)
        top1 = next(r for r in result if r['bet_type'] == 'ワイド' and r['top_k_per_race'] == 1)
        top3 = next(r for r in result if r['bet_type'] == 'ワイド' and r['top_k_per_race'] == 3)
        self.assertEqual(top1['trigami_races'], 0)
        self.assertEqual(top1['profitable_races'], 1)
        self.assertEqual(top3['trigami_races'], 1)
        self.assertEqual(top3['profitable_races'], 0)

    def test_losing_streak_is_counted_by_race_not_ticket(self):
        rows = [
            sample('R1', '1-2', 1.4), sample('R1', '1-3', 1.3),
            sample('R2', '2-3', 1.4), sample('R2', '2-4', 1.3),
            sample('R3', '3-4', 1.4, hit=True, payout=200),
        ]
        result = grid.evaluate(rows, thresholds=(1.0,), top_ks=(2,), stake_yen=100)
        wide = next(r for r in result if r['bet_type'] == 'ワイド')
        self.assertEqual(wide['max_losing_race_streak'], 2)


if __name__ == '__main__':
    unittest.main()
