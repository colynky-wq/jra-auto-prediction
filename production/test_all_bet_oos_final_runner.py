import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_all_bet_oos_drive_2025_final as final


class FinalRunnerTests(unittest.TestCase):
    def test_full_runner_adds_uncertainty_and_quality_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'out'
            batch = output / 'retrospective_2025'
            batch.mkdir(parents=True)
            samples = batch / 'retrospective_oos_all_bet_samples.csv'
            samples.write_text('race_id,bet_type\nR1,単勝\n', encoding='utf-8-sig')
            manifest_path = output / 'drive_2025_run_manifest.json'
            manifest_path.write_text(json.dumps({'strict_oos_verified': True}), encoding='utf-8')
            paths = {
                'output_dir': output,
                'batch_output': batch,
                'race_manifest': root / 'manifest.csv',
                'run_manifest': manifest_path,
            }
            paths['race_manifest'].write_text('レースID,日付\nR1,20250105\n', encoding='utf-8-sig')
            base = {'paths': paths, 'manifest': {}, 'summary': [], 'portfolio_grid': []}
            uncertainty_rows = [
                {'bet_type': '単勝', 'selected_bets': 5, 'day_count': 2, 'roi_pct': 120.0},
                {'bet_type': 'ワイド', 'selected_bets': 0, 'day_count': 0, 'roi_pct': None},
            ]
            technical = {
                'processed_races': 80,
                'settled_samples': 1000,
                'calendar_year_complete': False,
            }
            with mock.patch.object(final.drive_run, 'run', return_value=base) as base_run, \
                 mock.patch.object(final.uncertainty, 'run', return_value=uncertainty_rows) as boot, \
                 mock.patch.object(final.quality_gate, 'validate_manifest', return_value=technical) as gate:
                result = final.run(root, output_dir=output, bootstrap_reps=100, bootstrap_seed=7)
            self.assertTrue(base_run.call_args.kwargs['full'])
            self.assertFalse(base_run.call_args.kwargs['reuse_predictions'])
            boot.assert_called_once()
            gate.assert_called_once()
            self.assertTrue(gate.call_args.kwargs['require_all_bets'])
            saved = json.loads(manifest_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['uncertainty']['bootstrap_unit'], 'calendar_day')
            self.assertEqual(saved['uncertainty']['bootstrap_reps'], 100)
            self.assertEqual(saved['uncertainty']['nonempty_configurations'], 1)
            self.assertEqual(result['final_report']['quality_gate'], 'PASS')
            self.assertTrue(result['report_path'].is_file())

    def test_no_uncertainty_portfolio_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'out'; batch = output / 'retrospective_2025'; batch.mkdir(parents=True)
            (batch / 'retrospective_oos_all_bet_samples.csv').write_text('', encoding='utf-8')
            manifest_path = output / 'drive_2025_run_manifest.json'
            manifest_path.write_text('{}', encoding='utf-8')
            paths = {'output_dir': output, 'batch_output': batch,
                     'race_manifest': root / 'manifest.csv', 'run_manifest': manifest_path}
            base = {'paths': paths}
            with mock.patch.object(final.drive_run, 'run', return_value=base), \
                 mock.patch.object(final.uncertainty, 'run', return_value=[
                     {'selected_bets': 0, 'day_count': 0}
                 ]):
                with self.assertRaisesRegex(RuntimeError, 'no evaluated portfolio'):
                    final.run(root, output_dir=output)


if __name__ == '__main__':
    unittest.main()
