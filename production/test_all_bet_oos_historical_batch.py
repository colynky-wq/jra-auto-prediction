import tempfile
import unittest
from pathlib import Path

import run_all_bet_oos_historical_batch as batch


class StreamingHistoricalBatchTests(unittest.TestCase):
    def test_race_id_from_real_collector_filenames(self):
        self.assertEqual(
            batch.race_id_from_odds_path(Path('202506040609_odds.parquet')),
            '202506040609',
        )
        self.assertEqual(
            batch.race_id_from_odds_path(Path('202506040609_odds.csv')),
            '202506040609',
        )
        with self.assertRaises(ValueError):
            batch.race_id_from_odds_path(Path('202506040609_horses.parquet'))

    def test_payout_file_prefers_gzip_and_missing_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(batch.payout_file_for(root, '202506040609'))
            html = root / '202506040609.html'
            html.write_text('<html></html>', encoding='utf-8')
            self.assertEqual(batch.payout_file_for(root, '202506040609'), html)
            gz = root / '202506040609.html.gz'
            gz.write_bytes(b'fake')
            self.assertEqual(batch.payout_file_for(root, '202506040609'), gz)

    def test_streaming_summary_unresolved_is_not_loss_or_stake(self):
        agg = batch.StreamingSummary(thresholds=(1.0,), stake_yen=100)
        agg.add({
            'race_id': 'R1', 'bet_type': '単勝', 'model_ev': 1.20,
            'odds': 6.0, 'settled': False, 'hit': False,
            'payout_yen_per_100': None,
        })
        row = next(r for r in agg.rows(10, 1)
                   if r['bet_type'] == '単勝' and r['ev_threshold'] == 1.0)
        self.assertEqual(row['candidate_bets'], 1)
        self.assertEqual(row['settled_bets'], 0)
        self.assertEqual(row['unresolved_bets'], 1)
        self.assertEqual(row['stake_yen'], 0)
        self.assertIsNone(row['roi_pct'])
        self.assertEqual(row['max_losing_streak'], 0)

    def test_streaming_summary_roi_and_losing_streak(self):
        agg = batch.StreamingSummary(thresholds=(1.0, 1.2), stake_yen=100)
        samples = [
            {'race_id': 'R1', 'bet_type': '馬連', 'model_ev': 1.30, 'odds': 10.0,
             'settled': True, 'hit': False, 'payout_yen_per_100': 0.0},
            {'race_id': 'R2', 'bet_type': '馬連', 'model_ev': 1.25, 'odds': 8.0,
             'settled': True, 'hit': False, 'payout_yen_per_100': 0.0},
            {'race_id': 'R3', 'bet_type': '馬連', 'model_ev': 1.10, 'odds': 5.0,
             'settled': True, 'hit': True, 'payout_yen_per_100': 500.0},
        ]
        for sample in samples:
            agg.add(sample)
        rows = agg.rows(30, 3)
        r10 = next(r for r in rows if r['bet_type'] == '馬連' and r['ev_threshold'] == 1.0)
        self.assertEqual(r10['settled_bets'], 3)
        self.assertEqual(r10['hits'], 1)
        self.assertEqual(r10['stake_yen'], 300)
        self.assertEqual(r10['return_yen'], 500.0)
        self.assertAlmostEqual(r10['roi_pct'], 500 / 300 * 100)
        self.assertEqual(r10['max_losing_streak'], 2)
        self.assertEqual(r10['evaluated_races'], 3)

        r12 = next(r for r in rows if r['bet_type'] == '馬連' and r['ev_threshold'] == 1.2)
        self.assertEqual(r12['settled_bets'], 2)
        self.assertEqual(r12['hits'], 0)
        self.assertEqual(r12['return_yen'], 0.0)
        self.assertEqual(r12['roi_pct'], 0.0)
        self.assertEqual(r12['max_losing_streak'], 2)

    def test_group_predictions_preserves_race_rows(self):
        rows = [
            {'レースID': 'R1', '馬番': 1, 'AI勝率': 0.4},
            {'レースID': 'R2', '馬番': 1, 'AI勝率': 0.5},
            {'レースID': 'R1', '馬番': 2, 'AI勝率': 0.6},
        ]
        grouped = batch.group_predictions(rows)
        self.assertEqual([r['馬番'] for r in grouped['R1']], [1, 2])
        self.assertEqual(len(grouped['R2']), 1)


if __name__ == '__main__':
    unittest.main()
