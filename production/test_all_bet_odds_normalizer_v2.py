import unittest

import normalize_historical_all_bet_v2 as n


class HistoricalOddsNormalizerV2Tests(unittest.TestCase):
    def test_unavailable_frame_row_with_blank_combo_is_skipped(self):
        rows = [{
            'レースID': '202501010101',
            '券種': '枠連',
            '番号1': None,
            '番号2': None,
            '最終オッズ': None,
            '最終オッズ下限': None,
            '最終オッズ上限': None,
            'オッズ状態': '発売なし',
        }]
        self.assertEqual(n.normalize_odds_rows(rows, 'sample.parquet'), [])

    def test_quoted_frame_row_still_requires_combo(self):
        rows = [{
            'レースID': '202501010101',
            '券種': '枠連',
            '番号1': None,
            '番号2': None,
            '最終オッズ': 12.3,
        }]
        with self.assertRaisesRegex(ValueError, 'quoted row has invalid/missing combination'):
            n.normalize_odds_rows(rows, 'sample.parquet')

    def test_normal_frame_row_uses_generic_numbers(self):
        rows = [{
            'レースID': '202501010101',
            '券種': '枠連',
            '番号1': 1,
            '番号2': 6.0,
            '最終オッズ': 44.3,
        }]
        out = n.normalize_odds_rows(rows, 'sample.parquet')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['券種'], '枠連')
        self.assertEqual(out[0]['組合せ'], '1-6')
        self.assertEqual(out[0]['最終オッズ'], 44.3)


if __name__ == '__main__':
    unittest.main()
