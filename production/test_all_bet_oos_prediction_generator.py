from datetime import date
import math
import unittest

import numpy as np
import pandas as pd

import generate_all_bet_oos_predictions as g


class WalkForwardPredictionPlanTests(unittest.TestCase):
    def test_2023_2025_windows_use_only_prior_years(self):
        expected = {
            2023: ('2021-12-31', '2022-01-01', '2022-12-31', '2022-12-31', '2023-01-01'),
            2024: ('2022-12-31', '2023-01-01', '2023-12-31', '2023-12-31', '2024-01-01'),
            2025: ('2023-12-31', '2024-01-01', '2024-12-31', '2024-12-31', '2025-01-01'),
        }
        for year, values in expected.items():
            w = g.walkforward_window(year)
            self.assertEqual(
                (w.development_end, w.selection_start, w.selection_end, w.refit_end, w.predict_start),
                values,
            )
            self.assertLess(w.selection_end, w.predict_start)
            self.assertLess(w.refit_end, w.predict_start)

    def test_2025_does_not_reuse_fixed_2025_round_count(self):
        w = g.walkforward_window(2025)
        self.assertEqual(w.selection_start, '2024-01-01')
        self.assertEqual(w.selection_end, '2024-12-31')
        self.assertNotIn('212', w.fold_id)

    def test_2015_is_first_valid_prediction_year_for_2014_train_start(self):
        with self.assertRaisesRegex(ValueError, 'no prior selection year'):
            g.walkforward_window(2015)
        w = g.walkforward_window(2016)
        self.assertEqual(w.development_end, '2014-12-31')
        self.assertEqual(w.selection_start, '2015-01-01')

    def test_fixed_feature_count_is_37_and_market_logit_present(self):
        self.assertEqual(len(g.FEATURES_37), 37)
        self.assertIn('市場ロジット', g.FEATURES_37)
        self.assertEqual(len(set(g.FEATURES_37)), 37)

    def test_market_base_is_added_back_before_sigmoid(self):
        base = np.array([math.log(0.2 / 0.8), math.log(0.7 / 0.3)])
        residual = np.array([0.0, 0.0])
        p = g.sigmoid_from_base_and_residual(base, residual)
        self.assertAlmostEqual(p[0], 0.2, places=12)
        self.assertAlmostEqual(p[1], 0.7, places=12)
        boosted = g.sigmoid_from_base_and_residual(base, np.array([0.5, -0.5]))
        self.assertGreater(boosted[0], 0.2)
        self.assertLess(boosted[1], 0.7)


class SchemaTests(unittest.TestCase):
    def base_frame(self):
        row = {name: 0.0 for name in g.FEATURES_37}
        row.update({'日付': '2025-01-05', 'レースID': '202506010101', '馬番': 1, '着順': 1, '枠': 1})
        row['市場ロジット'] = -1.0
        return pd.DataFrame([row])

    def test_schema_requires_identifiers_target_and_all_37_features(self):
        frame = self.base_frame()
        schema = g.inspect_schema(frame)
        self.assertEqual(schema['date'], '日付')
        self.assertEqual(schema['race_id'], 'レースID')
        self.assertEqual(schema['horse_no'], '馬番')
        self.assertEqual(schema['finish'], '着順')
        self.assertEqual(schema['frame'], '枠')
        broken = frame.drop(columns=['前走秒差f'])
        with self.assertRaisesRegex(ValueError, 'missing fixed-model features'):
            g.inspect_schema(broken)

    def test_prepare_frame_builds_binary_win_target_and_dates(self):
        frame = self.base_frame()
        second = frame.iloc[0].copy()
        second['馬番'] = 2
        second['着順'] = 4
        frame = pd.concat([frame, pd.DataFrame([second])], ignore_index=True)
        schema = g.inspect_schema(frame)
        prepared = g.prepare_frame(frame, schema)
        self.assertEqual(prepared['_target_win'].tolist(), [1, 0])
        self.assertTrue(prepared['_trainable'].all())
        self.assertEqual(prepared['_race_date'].dt.strftime('%Y-%m-%d').tolist(), ['2025-01-05', '2025-01-05'])

    def test_non_numeric_finish_is_not_trainable(self):
        frame = self.base_frame()
        frame.loc[0, '着順'] = '中止'
        prepared = g.prepare_frame(frame, g.inspect_schema(frame))
        self.assertFalse(bool(prepared.loc[0, '_trainable']))
        self.assertTrue(bool(prepared.loc[0, '_predictable']))

    def test_date_slicing_enforces_boundaries(self):
        rows = []
        for day in ('2023-12-31', '2024-01-01', '2024-12-31', '2025-01-01'):
            row = {name: 0.0 for name in g.FEATURES_37}
            row.update({'日付': day, 'レースID': day.replace('-', ''), '馬番': 1, '着順': 1})
            row['市場ロジット'] = -1.0
            rows.append(row)
        frame = pd.DataFrame(rows)
        prepared = g.prepare_frame(frame, g.inspect_schema(frame))
        selection = g._slice_dates(prepared, '2024-01-01', '2024-12-31', trainable=True)
        self.assertEqual(selection['_race_date'].dt.strftime('%Y-%m-%d').tolist(), ['2024-01-01', '2024-12-31'])


if __name__ == '__main__':
    unittest.main()
