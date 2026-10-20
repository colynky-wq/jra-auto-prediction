import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import run_all_bet_oos_drive_2025 as drive_run


class Drive2025RunnerTests(unittest.TestCase):
    def test_default_layout_matches_current_drive_structure(self):
        root = Path('/content/gdrive_live/MyDrive/code_output')
        p = drive_run.resolve_paths(root)
        self.assertEqual(
            p['history'],
            root / 'github_actions_stage' / 'production' / 'production_history.parquet',
        )
        self.assertEqual(
            p['odds_dir'],
            root / 'jra_data' / 'historical_odds_2025' / 'races',
        )
        self.assertEqual(
            p['payout_dir'],
            root / 'jra_data' / 'historical_odds_2025' / 'payout_raw',
        )
        self.assertEqual(
            p['output_dir'],
            root / 'jra_data' / 'research_all_bet_oos_2025',
        )

    def test_validate_paths_fails_closed_on_missing_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = drive_run.resolve_paths(Path(tmp))
            with self.assertRaisesRegex(FileNotFoundError, 'required historical research inputs'):
                drive_run.validate_paths(paths)

    def test_smoke_run_calls_strict_generation_then_bounded_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / 'github_actions_stage' / 'production' / 'production_history.parquet'
            odds_dir = root / 'jra_data' / 'historical_odds_2025' / 'races'
            payout_dir = root / 'jra_data' / 'historical_odds_2025' / 'payout_raw'
            history.parent.mkdir(parents=True)
            history.write_bytes(b'placeholder')
            odds_dir.mkdir(parents=True)
            payout_dir.mkdir(parents=True)

            predictions = pd.DataFrame([
                {
                    'レースID': '202506040609', '馬番': 1, 'AI勝率': 0.6,
                    'race_date': '2025-09-01', 'train_end_date': '2024-12-31',
                    'OOS': True,
                },
                {
                    'レースID': '202506040609', '馬番': 2, 'AI勝率': 0.4,
                    'race_date': '2025-09-01', 'train_end_date': '2024-12-31',
                    'OOS': True,
                },
            ])
            gen_audit = {
                'feature_build': {'feature_count': 37},
                'folds': [{'prediction_year': 2025}],
            }
            fake_batch = {
                'summary': [],
                'manifest': {
                    'processed_races': 1,
                    'counts': {'scored_samples': 10},
                    'forward_ledgers_touched': False,
                },
            }

            with mock.patch.object(drive_run.portable, 'load_portable_history', return_value=pd.DataFrame({'x': [1]})), \
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

    def test_full_run_removes_race_cap_and_can_reuse_only_verified_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = drive_run.resolve_paths(root)
            paths['history'].parent.mkdir(parents=True)
            paths['history'].write_bytes(b'placeholder')
            paths['odds_dir'].mkdir(parents=True)
            paths['payout_dir'].mkdir(parents=True)
            paths['predictions'].parent.mkdir(parents=True)
            paths['predictions'].write_text(
                'レースID,馬番,AI勝率,race_date,train_end_date,OOS\n'
                '202506040609,1,0.6,2025-09-01,2024-12-31,True\n',
                encoding='utf-8-sig',
            )
            fake_batch = {
                'summary': [],
                'manifest': {'processed_races': 1, 'counts': {}, 'forward_ledgers_touched': False},
            }
            with mock.patch.object(
                drive_run.provenance, 'validate_rows',
                return_value={'row_count': 1, 'race_count': 1},
            ) as prov, mock.patch.object(drive_run.batch, 'run', return_value=fake_batch) as batch_run:
                result = drive_run.run(root, full=True, reuse_predictions=True)
            prov.assert_called_once()
            self.assertIsNone(batch_run.call_args.kwargs['max_races'])
            self.assertTrue(result['manifest']['prediction_stage']['reused'])
            self.assertEqual(result['manifest']['mode'], 'full')


if __name__ == '__main__':
    unittest.main()
