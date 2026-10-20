import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import run_all_bet_oos_drive_2025 as drive_run


def archive_audit(full=False):
    return {
        'scope_label': 'full_manifest' if full else 'contiguous_partial_year_prefix',
        'manifest_races': 100,
        'contiguous_prefix_races': 80 if not full else 100,
        'first_covered_date': '20250105',
        'last_contiguous_date': '20250920' if not full else '20251228',
        'first_covered_race_id': '202506010101',
        'last_contiguous_race_id': '202506040609',
        'first_missing_race_id': None if full else '202506040610',
        'archive_covers_full_manifest': bool(full),
    }


def create_required_inputs(root: Path):
    paths = drive_run.resolve_paths(root)
    paths['history'].parent.mkdir(parents=True)
    paths['history'].write_bytes(b'placeholder')
    paths['race_manifest'].parent.mkdir(parents=True, exist_ok=True)
    paths['race_manifest'].write_text('レースID,日付\n202506010101,20250105\n', encoding='utf-8-sig')
    paths['odds_dir'].mkdir(parents=True, exist_ok=True)
    paths['payout_dir'].mkdir(parents=True, exist_ok=True)
    return paths


class Drive2025RunnerTests(unittest.TestCase):
    def test_default_layout_matches_current_drive_structure(self):
        root = Path('/content/gdrive_live/MyDrive/code_output')
        p = drive_run.resolve_paths(root)
        self.assertEqual(p['history'], root / 'github_actions_stage' / 'production' / 'production_history.parquet')
        self.assertEqual(p['race_manifest'], root / 'jra_data' / 'historical_odds_2025' / 'race_manifest_2025.csv')
        self.assertEqual(p['odds_dir'], root / 'jra_data' / 'historical_odds_2025' / 'races')
        self.assertEqual(p['payout_dir'], root / 'jra_data' / 'historical_odds_2025' / 'payout_raw')
        self.assertEqual(p['output_dir'], root / 'jra_data' / 'research_all_bet_oos_2025')

    def test_validate_paths_fails_closed_on_missing_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = drive_run.resolve_paths(Path(tmp))
            with self.assertRaisesRegex(FileNotFoundError, 'required historical research inputs'):
                drive_run.validate_paths(paths)

    def test_smoke_run_calls_strict_generation_then_bounded_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_required_inputs(root)
            predictions = pd.DataFrame([
                {'レースID': '202506040609', '馬番': 1, 'AI勝率': 0.6,
                 'race_date': '2025-09-20', 'train_end_date': '2024-12-31', 'OOS': True},
                {'レースID': '202506040609', '馬番': 2, 'AI勝率': 0.4,
                 'race_date': '2025-09-20', 'train_end_date': '2024-12-31', 'OOS': True},
            ])
            gen_audit = {'feature_build': {'feature_count': 37}, 'folds': [{'prediction_year': 2025}]}
            fake_batch = {
                'summary': [],
                'manifest': {'processed_races': 1, 'counts': {'scored_samples': 10},
                             'forward_ledgers_touched': False},
            }
            with mock.patch.object(drive_run, 'audit_archive', return_value=archive_audit()), \
                 mock.patch.object(drive_run.portable, 'load_portable_history', return_value=pd.DataFrame({'x': [1]})), \
                 mock.patch.object(drive_run.portable, 'generate_from_history', return_value=(predictions, gen_audit)) as gen, \
                 mock.patch.object(drive_run.provenance, 'validate_rows', return_value={'row_count': 2, 'race_count': 1}), \
                 mock.patch.object(drive_run.batch, 'run', return_value=fake_batch) as batch_run:
                result = drive_run.run(root, smoke_races=7)
            gen.assert_called_once()
            self.assertTrue(result['paths']['predictions'].is_file())
            self.assertTrue(result['paths']['prediction_audit'].is_file())
            self.assertTrue(result['paths']['run_manifest'].is_file())
            self.assertEqual(batch_run.call_args.kwargs['max_races'], 7)
            self.assertFalse(result['manifest']['fixed_2025_model_used'])
            self.assertFalse(result['manifest']['forward_ledgers_touched'])
            self.assertEqual(result['manifest']['mode'], 'smoke')
            self.assertEqual(result['manifest']['study_scope']['last_contiguous_date'], '20250920')
            self.assertFalse(result['manifest']['full_calendar_year'])

    def test_full_run_means_full_available_archive_not_full_calendar_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = create_required_inputs(root)
            paths['predictions'].parent.mkdir(parents=True, exist_ok=True)
            paths['predictions'].write_text(
                'レースID,馬番,AI勝率,race_date,train_end_date,model_id,OOS\n'
                '202506040609,1,0.6,2025-09-20,2024-12-31,walkforward_2025,True\n',
                encoding='utf-8-sig',
            )
            fake_batch = {
                'summary': [],
                'manifest': {'processed_races': 1, 'counts': {}, 'forward_ledgers_touched': False},
            }
            with mock.patch.object(drive_run, 'audit_archive', return_value=archive_audit()), \
                 mock.patch.object(drive_run.provenance, 'validate_rows', return_value={'row_count': 1, 'race_count': 1}) as prov, \
                 mock.patch.object(drive_run.batch, 'run', return_value=fake_batch) as batch_run:
                result = drive_run.run(root, full=True, reuse_predictions=True)
            prov.assert_called_once()
            self.assertIsNone(batch_run.call_args.kwargs['max_races'])
            self.assertTrue(result['manifest']['prediction_stage']['reused'])
            self.assertEqual(result['manifest']['mode'], 'full_available_archive')
            self.assertFalse(result['manifest']['full_calendar_year'])


if __name__ == '__main__':
    unittest.main()
