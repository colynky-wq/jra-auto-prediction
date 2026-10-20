from datetime import date
import tempfile
from pathlib import Path
import unittest

import build_all_bet_oos_folds as folds


class YearlyOOSFoldTests(unittest.TestCase):
    def test_default_2023_2025_windows_are_strict_and_expected(self):
        result = folds.build_folds()
        self.assertEqual([x.fold_id for x in result], [
            'oos_2023_train_through_2022',
            'oos_2024_train_through_2023',
            'oos_2025_train_through_2024',
        ])
        self.assertEqual(result[0].train_start_date, '2014-01-01')
        self.assertEqual(result[0].train_end_date, '2022-12-31')
        self.assertEqual(result[1].train_end_date, '2023-12-31')
        self.assertEqual(result[2].train_end_date, '2024-12-31')
        self.assertEqual(result[2].predict_start_date, '2025-01-01')
        self.assertEqual(result[2].predict_end_date, '2025-12-31')
        for fold in result:
            self.assertLess(fold.train_end_date, fold.predict_start_date)

    def test_unsorted_duplicate_years_are_deterministic(self):
        result = folds.build_folds([2025, 2023, 2024, 2025])
        self.assertEqual([x.predict_start_date for x in result], [
            '2023-01-01', '2024-01-01', '2025-01-01'
        ])

    def test_race_date_maps_to_exactly_one_fold(self):
        result = folds.build_folds()
        fold = folds.fold_for_race_date(date(2025, 10, 12), result)
        self.assertEqual(fold.fold_id, 'oos_2025_train_through_2024')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            folds.fold_for_race_date(date(2026, 1, 1), result)

    def test_2025_fold_rejects_fixed_2025_model_provenance(self):
        fold = folds.yearly_fold(2025)
        with self.assertRaisesRegex(ValueError, 'OOS model assignment violation'):
            folds.validate_model_assignment(
                fold,
                model_id='LightGBM_市場ロジット補正_固定2025',
                train_end_date='2025-12-31',
            )

    def test_2025_fold_accepts_train_through_2024_model(self):
        fold = folds.yearly_fold(2025)
        folds.validate_model_assignment(
            fold,
            model_id='LightGBM_walkforward_2025_train_through_2024',
            train_end_date='2024-12-31',
        )

    def test_earlier_but_wrong_training_end_is_rejected_for_auditability(self):
        fold = folds.yearly_fold(2025)
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            folds.validate_model_assignment(
                fold,
                model_id='some_model',
                train_end_date='2023-12-31',
            )

    def test_manifest_write_is_deterministic_and_contains_no_forward_name(self):
        result = folds.build_folds()
        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / 'oos_fold_manifest_a.csv'
            b = Path(temp) / 'oos_fold_manifest_b.csv'
            folds.write_manifest(a, result)
            folds.write_manifest(b, result)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            text = a.read_text(encoding='utf-8-sig')
            self.assertIn('oos_2025_train_through_2024', text)
            self.assertNotIn('forward_history', text)
            self.assertNotIn('production_history', text)

    def test_prediction_year_must_be_after_training_start_year(self):
        with self.assertRaisesRegex(ValueError, 'later than train_start'):
            folds.yearly_fold(2014)


if __name__ == '__main__':
    unittest.main()
