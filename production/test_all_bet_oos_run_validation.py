import unittest

import research_all_bet_retrospective as core
import validate_all_bet_oos_run as gate


def manifest(full=True):
    scored = {bet: 10 for bet in core.ALL_BETS}
    settled = {bet: 8 for bet in core.ALL_BETS}
    return {
        'strict_oos_verified': True,
        'fixed_2025_model_used': False,
        'fixed_2025_rounds_212_reused': False,
        'forward_ledgers_touched': False,
        'batch_manifest': {
            'oos_scope_verified': True,
            'forward_ledgers_touched': False,
            'processed_races': 20,
            'counts': {
                'scored_samples': 80,
                'settled_samples': 64,
                'frame_mapping_horse_file_frames': 20,
            },
            'scored_samples_by_bet': scored,
            'settled_samples_by_bet': settled,
        },
    }


class ResearchRunGateTests(unittest.TestCase):
    def test_full_run_passes_with_all_eight_bets(self):
        report = gate.validate_manifest(manifest(), require_all_bets=True)
        self.assertTrue(report['eligible_for_roi_interpretation'])
        self.assertEqual(set(report['settled_bets_present']), set(core.ALL_BETS))

    def test_smoke_can_pass_without_every_pool(self):
        m = manifest()
        m['batch_manifest']['scored_samples_by_bet']['枠連'] = 0
        m['batch_manifest']['settled_samples_by_bet']['枠連'] = 0
        report = gate.validate_manifest(m, require_all_bets=False)
        self.assertIn('枠連', report['missing_settled_bets'])

    def test_full_run_fails_when_one_pool_has_no_settled_sample(self):
        m = manifest()
        m['batch_manifest']['settled_samples_by_bet']['3連単'] = 0
        with self.assertRaisesRegex(gate.ResearchRunValidationError, '3連単'):
            gate.validate_manifest(m, require_all_bets=True)

    def test_full_run_fails_without_frame_source(self):
        m = manifest()
        m['batch_manifest']['counts'].pop('frame_mapping_horse_file_frames')
        m['batch_manifest']['counts']['frame_mapping_frame_unavailable'] = 20
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'frame'):
            gate.validate_manifest(m, require_all_bets=True)

    def test_forward_touch_fails_closed(self):
        m = manifest()
        m['forward_ledgers_touched'] = True
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'forward'):
            gate.validate_manifest(m)

    def test_no_settled_samples_fails_even_smoke(self):
        m = manifest()
        m['batch_manifest']['counts']['settled_samples'] = 0
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'settled_samples'):
            gate.validate_manifest(m)


if __name__ == '__main__':
    unittest.main()
