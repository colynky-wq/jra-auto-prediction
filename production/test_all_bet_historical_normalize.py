import unittest

import normalize_historical_all_bet as n


class HistoricalOddsNormalizationTests(unittest.TestCase):
    def test_wide_range_uses_lower_bound_conservatively(self):
        rows = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': 'ワイド',
            '馬番1': '1', '馬番2': '2',
            '最終オッズ下限': '186.1', '最終オッズ上限': '193.4',
        }], '202507020403_wide.csv')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['組合せ'], '1-2')
        self.assertEqual(row['最終オッズ'], 186.1)
        self.assertEqual(row['最終オッズ上限_監査用'], 193.4)
        self.assertEqual(row['オッズ採用基準'], 'lower_bound_conservative')

    def test_umatan_preserves_order_and_exact_quote(self):
        rows = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '馬単',
            '1着馬番': '7', '2着馬番': '16', '最終オッズ': '15.1',
        }])
        self.assertEqual(rows[0]['組合せ'], '7-16')
        self.assertEqual(rows[0]['最終オッズ'], 15.1)
        self.assertEqual(rows[0]['最終オッズ上限_監査用'], 15.1)
        self.assertEqual(rows[0]['オッズ採用基準'], 'exact_quote')

    def test_umaren_sorts_unordered_pair(self):
        rows = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '馬連',
            '馬番1': '16', '馬番2': '7', '最終オッズ': '6.8',
        }])
        self.assertEqual(rows[0]['組合せ'], '7-16')

    def test_trifecta_preserves_finish_order_and_no_vote_is_skipped(self):
        rows = n.normalize_odds_rows([
            {'レースID': '202507020403', '券種': '3連単', '1着馬番': 1, '2着馬番': 2, '3着馬番': 3, '最終オッズ': 5781.7},
            {'レースID': '202507020403', '券種': '3連単', '1着馬番': 1, '2着馬番': 6, '3着馬番': 15, '最終オッズ': '票数なし'},
        ])
        self.assertEqual([(r['組合せ'], r['最終オッズ']) for r in rows], [('1-2-3', 5781.7)])

    def test_trio_sorts_three_horses(self):
        rows = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '3連複',
            '馬番1': 12, '馬番2': 3, '馬番3': 8, '最終オッズ': 44.2,
        }])
        self.assertEqual(rows[0]['組合せ'], '3-8-12')

    def test_frame_quinella_same_frame_is_valid(self):
        rows = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '枠連',
            '枠1': 1, '枠2': 1, '最終オッズ': 19.5,
        }])
        self.assertEqual(rows[0]['組合せ'], '1-1')

    def test_single_win_and_place_shapes(self):
        win = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '単勝', '馬番': 7, '最終オッズ': 3.2,
        }])[0]
        place = n.normalize_odds_rows([{
            'レースID': '202507020403', '券種': '複勝', '馬番': 7,
            '最終オッズ下限': 1.3, '最終オッズ上限': 1.8,
        }])[0]
        self.assertEqual(win['組合せ'], '7')
        self.assertEqual(place['組合せ'], '7')
        self.assertEqual(place['最終オッズ'], 1.3)
        self.assertEqual(place['オッズ採用基準'], 'lower_bound_conservative')

    def test_reversed_range_and_duplicate_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'reversed'):
            n.normalize_odds_rows([{
                'レースID': '1', '券種': 'ワイド', '馬番1': 1, '馬番2': 2,
                '最終オッズ下限': 5.0, '最終オッズ上限': 4.0,
            }])
        duplicate = {
            'レースID': '202507020403', '券種': '馬連', '馬番1': 1, '馬番2': 2,
            '最終オッズ': 10.0,
        }
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            n.normalize_odds_rows([duplicate, dict(duplicate)])


if __name__ == '__main__':
    unittest.main()
