import unittest

import research_all_bet_oos_pipeline as pipeline


def strict_predictions():
    probs = [40, 25, 15, 12, 8]
    rows = []
    for number, prob in enumerate(probs, 1):
        rows.append({
            'レースID': '202506010101',
            '馬番': number,
            'AI勝率': prob,
            '枠': min(8, (number + 1) // 2),
            'race_date': '2025-01-05',
            'train_end_date': '2024-12-31',
            'model_id': 'fold_2025_train_through_2024',
            'fold_id': '2025-A',
        })
    return rows


class StrictOOSPipelineTests(unittest.TestCase):
    def test_strict_provenance_is_required_and_propagated(self):
        odds = [{'レースID': '202506010101', '券種': '単勝', '組合せ': '1', '最終オッズ': '3.0'}]
        payouts = [{'レースID': '202506010101', '券種': '単勝', '組合せ': '1', '払戻円': 250}]
        samples, audit = pipeline.score_quotes_strict(strict_predictions(), odds, payouts)
        self.assertEqual(audit['race_count'], 1)
        self.assertTrue(samples[0]['oos_scope_verified'])
        self.assertEqual(samples[0]['oos_verification_method'], 'train_end_date<race_date')

    def test_oos_boolean_alone_is_rejected(self):
        predictions = [
            {'レースID': '202506010101', '馬番': 1, 'AI勝率': 60, 'OOS': True},
            {'レースID': '202506010101', '馬番': 2, 'AI勝率': 40, 'OOS': True},
        ]
        with self.assertRaisesRegex(ValueError, 'missing provenance field'):
            pipeline.score_quotes_strict(predictions, [], [])

    def test_fixed_2025_metadata_cannot_be_used_for_2025_race(self):
        predictions = strict_predictions()
        for row in predictions:
            row['race_date'] = '2025-10-12'
            row['train_end_date'] = '2025-12-31'
            row['model_id'] = 'LightGBM_市場ロジット補正_固定2025'
        with self.assertRaisesRegex(ValueError, 'OOS provenance violation'):
            pipeline.score_quotes_strict(predictions, [], [])


if __name__ == '__main__':
    unittest.main()
