import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import generate_all_bet_oos_from_portable_history as portable
import generate_all_bet_oos_predictions as oos


def race_rows(day, race_id, weight1=450.0, weight2=470.0, horse1='H1', horse2='H2'):
    common = {
        '日付': pd.Timestamp(day), 'レースID': race_id, 'タイム秒': 100.0,
        '上がり3F': 35.0, '頭数': 2, '出典': '新データ', '人気': 1,
        '距離': 1600, '芝ダ': '芝', '斤量': 55.0, '増減': 0.0,
        '調教師': 'T1', 'クラス': '1勝', '障害': False, '年齢': 4,
    }
    a = dict(common)
    a.update({'馬番': 1, '着順': 1, '初角': 1, '馬キー': horse1, '騎手': 'J1',
              '馬体重': weight1, '単勝オッズ': 2.0})
    b = dict(common)
    b.update({'馬番': 2, '着順': 2, '初角': 2, '馬キー': horse2, '騎手': 'J2',
              '馬体重': weight2, '単勝オッズ': 3.0})
    return [a, b]


def synthetic_history():
    rows = []
    rows += race_rows('2021-01-10', '202101010101', 450, 470)
    rows += race_rows('2022-06-10', '202206010101', np.nan, 490)
    rows += race_rows('2023-02-10', '202302010101', np.nan, 900)
    rows += race_rows('2024-02-10', '202402010101', 500, 510)
    rows += race_rows('2025-02-10', '202502010101', 520, 530)
    # Deliberately reverse input order: the builder must impose chronology.
    return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)


class PortableOOSFeatureTests(unittest.TestCase):
    def test_builder_sorts_and_creates_exact_fixed_feature_order(self):
        featured, audit = portable.validate_and_prepare_raw(synthetic_history(), (2023, 2024, 2025))
        self.assertEqual(tuple(oos.FEATURES_37), tuple(portable.feature_engine.ALLF))
        self.assertTrue(all(c in featured.columns for c in oos.FEATURES_37))
        ordered = featured[['日付', 'レースID', '馬番']].reset_index(drop=True)
        expected = ordered.sort_values(['日付', 'レースID', '馬番'], kind='stable').reset_index(drop=True)
        pd.testing.assert_frame_equal(ordered, expected)
        self.assertFalse(audit['input_already_chronological'])
        self.assertTrue(audit['feature_order_matches_portable_engine'])
        self.assertEqual(audit['feature_count'], 37)

    def test_body_weight_fill_uses_only_pre_oos_rows(self):
        featured, audit = portable.validate_and_prepare_raw(synthetic_history(), (2023, 2024, 2025))
        # Before 2023 the positive observed weights are 450,470,490 => median 470.
        self.assertEqual(audit['body_weight_fill_median'], 470.0)
        target = featured[(featured['レースID'] == '202302010101') & (featured['馬番'] == 1)]
        self.assertEqual(float(target.iloc[0]['馬体重']), 470.0)

        changed = synthetic_history()
        changed.loc[(changed['レースID'] == '202302010101') & (changed['馬番'] == 2), '馬体重'] = 9999
        _, audit_changed = portable.validate_and_prepare_raw(changed, (2023, 2024, 2025))
        self.assertEqual(audit_changed['body_weight_fill_median'], 470.0)

    def test_rows_after_last_oos_year_cannot_influence_features(self):
        base = synthetic_history()
        future = pd.DataFrame(race_rows('2026-01-10', '202601010101', 9999, 9999, 'H3', 'H4'))
        featured, audit = portable.validate_and_prepare_raw(pd.concat([base, future], ignore_index=True),
                                                            (2023, 2024, 2025))
        self.assertFalse((featured['日付'].dt.year == 2026).any())
        self.assertEqual(audit['future_rows_discarded'], 2)
        self.assertEqual(audit['last_date'], '2025-02-10')

    def test_duplicate_race_horse_fails_closed(self):
        frame = synthetic_history()
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, 'duplicate race/horse'):
            portable.validate_and_prepare_raw(frame, (2023, 2024, 2025))

    def test_same_horse_twice_same_day_fails_closed(self):
        frame = synthetic_history()
        extra = pd.DataFrame(race_rows('2022-06-10', '202206010102', 480, 490, 'H1', 'HX'))
        frame = pd.concat([frame, extra], ignore_index=True)
        with self.assertRaisesRegex(ValueError, 'horse appears multiple times'):
            portable.validate_and_prepare_raw(frame, (2023, 2024, 2025))

    def test_drive_materialized_parquet_bin_name_is_supported(self):
        frame = synthetic_history()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'production_history.parquet.bin'
            frame.to_parquet(path, index=False)
            loaded = portable.load_portable_history(path)
        self.assertEqual(len(loaded), len(frame))
        self.assertEqual(set(portable.REQUIRED_RAW_COLUMNS) - set(loaded.columns), set())


if __name__ == '__main__':
    unittest.main()
