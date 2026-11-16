from pathlib import Path
import tempfile
import unittest

import research_all_bet_retrospective as r


def predictions(with_frames=True, oos=True):
    probs = [40, 25, 15, 12, 8]
    rows = []
    for number, prob in enumerate(probs, 1):
        row = {'レースID': '202501010101', '馬番': number, 'AI勝率': prob, 'OOS': oos}
        if with_frames:
            row['枠'] = min(8, (number + 1) // 2)
        rows.append(row)
    return rows


class CanonicalTests(unittest.TestCase):
    def test_order_rules_and_compact_keys(self):
        self.assertEqual(r.canonical_combo('馬連', '5-2'), '2-5')
        self.assertEqual(r.canonical_combo('ワイド', '0205'), '2-5')
        self.assertEqual(r.canonical_combo('馬単', '5-2'), '5-2')
        self.assertEqual(r.canonical_combo('3連複', '5-1-3'), '1-3-5')
        self.assertEqual(r.canonical_combo('3連単', '05-01-03'), '5-1-3')
        self.assertEqual(r.canonical_combo('枠連', '0101'), '1-1')

    def test_no_vote_quote_is_not_a_zero_odds_bet(self):
        odds = [
            {'レースID': '202501010101', '券種': '3連単', '組合せ': '1-2-3', '最終オッズ': '票数なし'},
            {'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'},
        ]
        parsed = r.odds_rows(odds)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['bet_type'], '単勝')


class ProbabilityTests(unittest.TestCase):
    def setUp(self):
        self.weights, self.meta, verified = r.prediction_index(predictions())
        self.assertTrue(verified)
        self.w = self.weights['202501010101']
        self.m = self.meta['202501010101']

    def test_ordered_and_unordered_pair_probability(self):
        exact = r.probability(self.w, self.m, '馬単', '1-2')
        reverse = r.probability(self.w, self.m, '馬単', '2-1')
        self.assertAlmostEqual(exact, .4 * .25 / .6)
        self.assertAlmostEqual(r.probability(self.w, self.m, '馬連', '1-2'), exact + reverse)

    def test_triple_and_wide_are_valid_probabilities(self):
        for bet, combo in [('3連単', '1-2-3'), ('3連複', '1-2-3'), ('ワイド', '1-2'), ('複勝', '1')]:
            value = r.probability(self.w, self.m, bet, combo)
            self.assertGreater(value, 0)
            self.assertLessEqual(value, 1)

    def test_frame_missing_skips_only_frame_quote(self):
        preds = predictions(with_frames=False)
        odds = [
            {'レースID': '202501010101', '券種': '枠連', '組合せ': '1-2', '最終オッズ': '5.0'},
            {'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'},
        ]
        payouts = [{'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '払戻円': 250}]
        scored = r.score_quotes(preds, odds, payouts)
        self.assertEqual([(x['bet_type'], x['combo']) for x in scored], [('単勝', '1')])


class SettlementTests(unittest.TestCase):
    def test_settlement_coverage_prevents_false_losses(self):
        odds = [
            {'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'},
            {'レースID': '202501010101', '券種': '馬単', '組合せ': '1-2', '最終オッズ': '8.0'},
            {'レースID': '202501010101', '券種': '3連単', '組合せ': '1-2-3', '最終オッズ': '20.0'},
        ]
        payouts = [
            {'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '払戻円': 250},
            # Establish 馬単 payout coverage while making 1-2 a genuine loss.
            {'レースID': '202501010101', '券種': '馬単', '組合せ': '2-1', '払戻円': 700},
            # No 3連単 row: the 3連単 candidate is unresolved, not a loss.
        ]
        scored = r.score_quotes(predictions(), odds, payouts)
        by_bet = {x['bet_type']: x for x in scored}
        self.assertTrue(by_bet['単勝']['settled'])
        self.assertTrue(by_bet['単勝']['hit'])
        self.assertTrue(by_bet['馬単']['settled'])
        self.assertFalse(by_bet['馬単']['hit'])
        self.assertFalse(by_bet['3連単']['settled'])
        self.assertFalse(by_bet['3連単']['hit'])
        self.assertIsNone(by_bet['3連単']['payout_yen_per_100'])

    def test_roi_uses_only_settled_bets(self):
        odds = [
            {'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'},
            {'レースID': '202501010101', '券種': '3連単', '組合せ': '1-2-3', '最終オッズ': '100.0'},
        ]
        payouts = [{'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '払戻円': 250}]
        scored = r.score_quotes(predictions(), odds, payouts)
        summary = r.summarise(scored, thresholds=(1.10,), stake_yen=100)
        win = next(x for x in summary if x['bet_type'] == '単勝')
        trifecta = next(x for x in summary if x['bet_type'] == '3連単')
        self.assertEqual(win['settled_bets'], 1)
        self.assertEqual(win['stake_yen'], 100)
        self.assertEqual(win['return_yen'], 250)
        self.assertEqual(win['roi_pct'], 250)
        self.assertEqual(trifecta['settled_bets'], 0)
        self.assertGreaterEqual(trifecta['unresolved_bets'], 0)

    def test_in_sample_flag_aborts(self):
        with self.assertRaisesRegex(ValueError, 'in-sample'):
            r.score_quotes(predictions(oos=False), [], [])

    def test_output_names_are_retrospective_only(self):
        odds = [{'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'}]
        payouts = [{'レースID': '202501010101', '券種': '単勝', '組合せ': '1', '払戻円': 250}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, rows in [('pred.csv', predictions()), ('odds.csv', odds), ('pay.csv', payouts)]:
                r.write_csv(root / name, rows)
            out = root / 'out'
            r.run(root / 'pred.csv', root / 'odds.csv', root / 'pay.csv', out, thresholds=(1.10,))
            self.assertTrue((out / 'retrospective_all_bet_samples.csv').exists())
            self.assertTrue((out / 'retrospective_all_bet_summary.csv').exists())
            self.assertTrue((out / 'retrospective_ev_grid.csv').exists())
            self.assertFalse((out / 'research_all_bet_summary.csv').exists())


if __name__ == '__main__':
    unittest.main()
