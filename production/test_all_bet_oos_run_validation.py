import unittest

import research_all_bet_retrospective as core
import validate_all_bet_oos_run as gate


def manifest(calendar_full=False):
    scored = {bet: 10 for bet in core.ALL_BETS}
    settled = {bet: 8 for bet in core.ALL_BETS}
    archive = {
        'scope_label': 'full_manifest' if calendar_full else 'contiguous_partial_year_prefix',
        'contiguous_prefix_is_hole_free': True,
        'internal_hole_count': 0,
        'covered_after_first_gap_count': 0,
        'contiguous_prefix_races': 100 if calendar_full else 80,
        'manifest_races': 100,
        'first_covered_date': '20250105',
        'last_contiguous_date': '20251228' if calendar_full else '20250920',
        'first_missing_race_id': None if calendar_full else '202506040610',
        'archive_covers_full_manifest': calendar_full,
    }
    return {
        'strict_oos_verified': True,
        'fixed_2025_model_used': False,
        'fixed_2025_rounds_212_reused': False,
        'forward_ledgers_touched': False,
        'full_calendar_year': calendar_full,
        'archive_coverage': archive,
        'study_scope': {
            'scope_label': archive['scope_label'],
            'first_covered_date': archive['first_covered_date'],
            'last_contiguous_date': archive['last_contiguous_date'],
            'first_missing_race_id': archive['first_missing_race_id'],
        },
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
    def test_partial_year_full_available_archive_can_pass_all_bet_gate(self):
        report = gate.validate_manifest(manifest(calendar_full=False), require_all_bets=True)
        self.assertTrue(report['eligible_for_roi_interpretation'])
        self.assertFalse(report['calendar_year_complete'])
        self.assertEqual(report['study_scope_label'], 'contiguous_partial_year_prefix')
        self.assertEqual(report['study_last_date'], '20250920')
        self.assertEqual(set(report['settled_bets_present']), set(core.ALL_BETS))

    def test_full_calendar_manifest_reports_complete(self):
        report = gate.validate_manifest(manifest(calendar_full=True), require_all_bets=True)
        self.assertTrue(report['calendar_year_complete'])
        self.assertEqual(report['archive_covered_races'], 100)
        self.assertEqual(report['archive_manifest_races'], 100)

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

    def test_non_contiguous_archive_fails_closed(self):
        m = manifest()
        m['archive_coverage']['scope_label'] = 'non_contiguous_archive'
        m['study_scope']['scope_label'] = 'non_contiguous_archive'
        m['archive_coverage']['contiguous_prefix_is_hole_free'] = False
        m['archive_coverage']['internal_hole_count'] = 1
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'archive scope'):
            gate.validate_manifest(m)

    def test_archive_full_flag_mismatch_fails_closed(self):
        m = manifest(calendar_full=False)
        m['full_calendar_year'] = True
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'disagrees'):
            gate.validate_manifest(m)

    def test_processed_races_cannot_exceed_audited_coverage(self):
        m = manifest()
        m['batch_manifest']['processed_races'] = 81
        with self.assertRaisesRegex(gate.ResearchRunValidationError, 'exceeds'):
            gate.validate_manifest(m)

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
