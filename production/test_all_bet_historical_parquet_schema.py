import tempfile
import unittest
from pathlib import Path

import normalize_historical_all_bet as n


class ProductionHistoricalOddsSchemaTests(unittest.TestCase):
    def test_generic_number_columns_match_real_collector_schema(self):
        rows = n.normalize_odds_rows([
            {
                'レースID': '202506040609', '券種': '3連単',
                '番号1': 8, '番号2': 1, '番号3': 4,
                '最終オッズ': 3836.9,
            },
            {
                'レースID': '202506040609', '券種': 'ワイド',
                '番号1': 8, '番号2': 1, '番号3': None,
                '最終オッズ': None, '最終オッズ下限': 52.1, '最終オッズ上限': 54.0,
            },
            {
                'レースID': '202506040609', '券種': '複勝',
                '番号1': 8, '番号2': None, '番号3': None,
                '最終オッズ': None, '最終オッズ下限': 8.2, '最終オッズ上限': 9.0,
            },
            {
                'レースID': '202506040609', '券種': '枠連',
                '番号1': 1, '番号2': 6, '番号3': None,
                '最終オッズ': 22.1,
            },
        ], '202506040609_odds.parquet')
        got = {(r['券種'], r['組合せ']): r for r in rows}
        self.assertEqual(got[('3連単', '8-1-4')]['最終オッズ'], 3836.9)
        self.assertEqual(got[('ワイド', '1-8')]['最終オッズ'], 52.1)
        self.assertEqual(got[('ワイド', '1-8')]['最終オッズ上限_監査用'], 54.0)
        self.assertEqual(got[('複勝', '8')]['最終オッズ'], 8.2)
        self.assertEqual(got[('枠連', '1-6')]['最終オッズ'], 22.1)

    def test_discovery_ignores_horse_parquets_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'nested').mkdir()
            (root / '202506040609_odds.parquet').write_bytes(b'')
            (root / '202506040609_horses.parquet').write_bytes(b'')
            (root / 'nested' / '202506040610_odds.csv').write_text('', encoding='utf-8')
            (root / 'collector_run_2025.csv').write_text('', encoding='utf-8')
            names = [p.name for p in n.discover_odds_files(root)]
        self.assertEqual(set(names), {'202506040609_odds.parquet', '202506040610_odds.csv'})


if __name__ == '__main__':
    unittest.main()
