import unittest

import all_bet_oos_provenance as p


class OOSProvenanceTests(unittest.TestCase):
    def row(self, race_date='2025-01-05', train_end='2024-12-31', model='fold_2025', **extra):
        row = {
            'レースID': '202506010101',
            '馬番': 1,
            'race_date': race_date,
            'train_end_date': train_end,
            'model_id': model,
        }
        row.update(extra)
        return row

    def test_valid_prior_training_window_is_verified(self):
        meta = p.validate_row(self.row())
        self.assertTrue(meta['oos_verified'])
        self.assertEqual(meta['train_end_date'], '2024-12-31')

    def test_same_day_training_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'earlier than race_date'):
            p.validate_row(self.row(train_end='2025-01-05'))

    def test_future_training_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'earlier than race_date'):
            p.validate_row(self.row(train_end='2025-12-31'))

    def test_fixed_2025_model_cannot_score_2025_race_if_training_ends_2025(self):
        with self.assertRaisesRegex(ValueError, 'OOS provenance violation'):
            p.validate_row(self.row(
                race_date='2025-10-12',
                train_end='2025-12-31',
                model='LightGBM_市場ロジット補正_固定2025',
            ))

    def test_date_formats_are_normalized(self):
        meta = p.validate_row(self.row(race_date='20250105', train_end='2024/12/31'))
        self.assertEqual(meta['race_date'], '2025-01-05')
        self.assertEqual(meta['train_end_date'], '2024-12-31')

    def test_boolean_oos_without_dates_is_not_strict_provenance(self):
        row = {'レースID': '202506010101', '馬番': 1, 'AI勝率': 12.3, 'OOS': True}
        self.assertFalse(p.has_strict_provenance(row))
        with self.assertRaisesRegex(ValueError, 'missing provenance field'):
            p.validate_row(row)

    def test_inconsistent_model_or_fold_within_race_is_rejected(self):
        a = self.row(fold_id='2025-A')
        b = self.row(fold_id='2025-B')
        b['馬番'] = 2
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            p.validate_rows([a, b])

    def test_consistent_race_rows_pass(self):
        a = self.row(fold_id='2025-A')
        b = self.row(fold_id='2025-A')
        b['馬番'] = 2
        result = p.validate_rows([a, b])
        self.assertTrue(result['verified'])
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(result['race_count'], 1)


if __name__ == '__main__':
    unittest.main()
