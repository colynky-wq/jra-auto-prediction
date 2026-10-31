import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_bet_performance_summary as summary


def source(stamp, payout=250):
    return {
        'generatedAtJST': stamp,
        'races': [{
            'id': 'win:2026-10-25:race1',
            'betType': 'win',
            'raceDate': '2026-10-25',
            'raceID': 'race1',
            'startTime': '10:00',
            'status': 'matched',
            'candidates': [
                {'id': '01', 'unitYen': 100, 'hit': True, 'payoutYen': payout},
                {'id': '02', 'unitYen': 100, 'hit': False, 'payoutYen': 0},
            ],
        }],
    }


def formal_sidecar(captured='2026-10-25T09:50:00+09:00', timing='live_10min'):
    return {
        'schemaVersion': 1,
        'betType': 'win',
        'prediction': {
            'raceID': 'race1',
            'raceDate': '2026-10-25',
            'startTime': '10:00',
            'predictionTiming': timing,
            'capturedAtJST': captured,
        },
    }


def direct_row(day, rid, minute=0):
    return {
        'id': f'win:{day}:{rid}',
        'betType': 'win',
        'raceDate': day,
        'raceID': rid,
        'startTime': f'10:{minute:02d}',
        'status': 'matched',
        'candidates': [{'id': '01', 'unitYen': 100, 'hit': False, 'payoutYen': 0}],
    }


def portfolio_row(row):
    return {
        'raceDate': row['raceDate'],
        'raceID': row['raceID'],
        'startTime': row.get('startTime', '10:00'),
        'scope': 'prospective',
        'ruleVersion': 'portfolio-shadow-v1-20261019',
        'planType': 'skip',
        'planLabel': '見送り',
        'reason': 'test',
        'legs': [],
        'totalStakeYen': 0,
        'settled': False,
    }


def portfolio_payload(rows):
    return {
        'schemaVersion': 1,
        'version': 'portfolio-shadow-v1-20261019',
        'status': 'research_display_only',
        'formalRecommendationsEnabled': False,
        'autoBetEnabled': False,
        'stakeSizingEnabled': False,
        'prospectiveStartDate': '2026-10-24',
        'races': [portfolio_row(row) for row in rows],
    }


class TestBetPerformanceSummary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.report = self.root / 'reports' / '20261025' / 'BetResearchData.json'
        self.report.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_bet_source(self, value):
        self.report.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def write_portfolio(self, rows):
        (self.root / 'PortfolioResearchData.json').write_text(
            json.dumps(portfolio_payload(rows), ensure_ascii=False), encoding='utf-8'
        )

    def write_source(self, value):
        self.write_bet_source(value)
        prospective = [
            row for row in value.get('races', [])
            if isinstance(row, dict) and str(row.get('raceDate') or '') >= '2026-10-24'
        ]
        self.write_portfolio(prospective)

    def write_sidecar(self, value):
        path = self.root / 'reports' / '20261025' / 'all_bet_v1' / 'win' / 'race1.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_recorded_unit_amounts_drive_stake_and_return(self):
        self.write_source(source('2026-10-25T17:00:00+09:00', payout=250))
        built = summary.build(self.root)
        win = next(row for row in built['betTypes'] if row['id'] == 'win')
        self.assertEqual(win['settledRaces'], 1)
        self.assertEqual(win['hitRaces'], 1)
        self.assertEqual(win['candidateTickets'], 2)
        self.assertEqual(win['hitTickets'], 1)
        self.assertEqual(win['stakeYen'], 200)
        self.assertEqual(win['returnYen'], 250)
        self.assertEqual(win['profitYen'], 50)
        self.assertEqual(win['returnRatePct'], 125.0)

    def test_formal_t10_cohort_requires_strict_immutable_sidecar(self):
        self.write_source(source('2026-10-25T17:00:00+09:00', payout=250))

        without = summary.build(self.root)
        self.assertEqual(without['formalT10']['eligibleRaces'], 0)
        formal_win = next(row for row in without['formalT10']['betTypes'] if row['id'] == 'win')
        self.assertEqual(formal_win['settledRaces'], 0)
        self.assertEqual(without['formalT10']['evidenceReasons']['formal_win_sidecar_missing'], 1)

        self.write_sidecar(formal_sidecar())
        with_formal = summary.build(self.root)
        self.assertEqual(with_formal['formalT10']['prospectiveStartDate'], '2026-10-24')
        self.assertEqual(with_formal['formalT10']['targetRaces'], 100)
        self.assertEqual(with_formal['formalT10']['eligibleRaces'], 1)
        self.assertEqual(with_formal['formalT10']['excludedRaces'], 0)
        formal_win = next(row for row in with_formal['formalT10']['betTypes'] if row['id'] == 'win')
        self.assertEqual(formal_win['recordedRaces'], 1)
        self.assertEqual(formal_win['recordingCoveragePct'], 100.0)
        self.assertEqual(formal_win['settledRaces'], 1)
        self.assertEqual(formal_win['settledCoveragePct'], 100.0)
        self.assertEqual(formal_win['stakeYen'], 200)
        self.assertEqual(formal_win['returnYen'], 250)
        self.assertEqual(formal_win['returnRatePct'], 125.0)
        self.assertTrue(formal_win['formalCohortApplicable'])
        self.assertEqual(formal_win['formalEvaluationStatus'], 'active')

    def test_non_formal_timing_stays_excluded(self):
        self.write_source(source('2026-10-25T17:00:00+09:00'))
        self.write_sidecar(formal_sidecar(timing='morning'))
        built = summary.build(self.root)
        self.assertEqual(built['formalT10']['eligibleRaces'], 0)
        self.assertEqual(built['formalT10']['evidenceReasons']['prediction_not_formal_10min'], 1)

    def test_out_of_window_capture_stays_excluded(self):
        self.write_source(source('2026-10-25T17:00:00+09:00'))
        self.write_sidecar(formal_sidecar(captured='2026-10-25T09:45:00+09:00'))
        built = summary.build(self.root)
        self.assertEqual(built['formalT10']['eligibleRaces'], 0)
        self.assertEqual(built['formalT10']['evidenceReasons']['captured_outside_formal_window'], 1)

    @patch.object(summary, 'inspect_formal_10min_evidence', return_value={'eligible': True, 'reason': 'formal_10min_verified'})
    def test_formal_cohort_ignores_prestart_and_locks_first_100(self, inspect_mock):
        rows = [direct_row('2026-10-23', 'prestart', 0)]
        prospective = [direct_row('2026-10-24', f'race{i:03d}', i % 60) for i in range(101)]
        rows += prospective
        self.write_portfolio(prospective)

        filtered, meta = summary.formal_t10_rows(self.root, rows)

        self.assertEqual(inspect_mock.call_count, 101)
        self.assertEqual(meta['prospectiveRacesObserved'], 101)
        self.assertEqual(meta['verifiedFormalRacesObserved'], 101)
        self.assertEqual(meta['eligibleRaces'], 100)
        self.assertEqual(meta['remainingTo100'], 0)
        self.assertEqual(meta['laterFormalRacesExcludedAfterFirst100'], 1)
        self.assertTrue(meta['first100CohortLockedByRule'])
        self.assertEqual(len(filtered), 100)
        self.assertNotIn('prestart', {row['raceID'] for row in filtered})

    @patch.object(summary, 'inspect_formal_10min_evidence', return_value={'eligible': True, 'reason': 'formal_10min_verified'})
    def test_missing_bet_record_is_coverage_gap_not_smaller_formal_denominator(self, _inspect_mock):
        race1 = direct_row('2026-10-25', 'race1', 0)
        race2 = direct_row('2026-10-25', 'race2', 5)
        self.write_portfolio([race1, race2])
        self.write_bet_source({'generatedAtJST': '2026-10-25T17:00:00+09:00', 'races': [race1]})

        built = summary.build(self.root)

        self.assertEqual(built['formalT10']['eligibleRaces'], 2)
        win = next(row for row in built['formalT10']['betTypes'] if row['id'] == 'win')
        self.assertEqual(win['recordedRaces'], 1)
        self.assertEqual(win['recordingCoveragePct'], 50.0)
        self.assertEqual(win['missingRecordRaces'], 1)
        self.assertEqual(win['settledRaces'], 1)
        self.assertEqual(win['settledCoveragePct'], 50.0)

    @patch.object(summary, 'inspect_formal_10min_evidence', return_value={'eligible': True, 'reason': 'formal_10min_verified'})
    def test_win5_is_not_scored_against_single_race_formal_denominator(self, _inspect_mock):
        race1 = direct_row('2026-10-25', 'race1', 0)
        race2 = direct_row('2026-10-25', 'race2', 5)
        self.write_portfolio([race1, race2])
        self.write_bet_source({'generatedAtJST': '2026-10-25T17:00:00+09:00', 'races': [race1]})

        built = summary.build(self.root)

        self.assertIn('win5', built['formalT10']['nonPerRaceBetTypes'])
        win5 = next(row for row in built['formalT10']['betTypes'] if row['id'] == 'win5')
        self.assertFalse(win5['formalCohortApplicable'])
        self.assertEqual(win5['formalEvaluationStatus'], 'dedicated_multi_race_protocol_required')
        self.assertIsNone(win5['recordingCoveragePct'])
        self.assertIsNone(win5['settledCoveragePct'])
        self.assertIsNone(win5['missingRecordRaces'])
        self.assertIsNone(win5['unsettledOrUnmatchedRaces'])
        self.assertIsNone(win5['returnRatePct'])

    def test_source_timestamp_only_does_not_rewrite(self):
        self.write_source(source('2026-10-25T17:00:00+09:00'))
        changed, _ = summary.write_if_material_changed(self.root)
        self.assertTrue(changed)
        output = self.root / 'BetPerformanceSummary.json'
        before = output.read_bytes()

        self.write_source(source('2026-10-25T17:10:00+09:00'))
        changed, current = summary.write_if_material_changed(self.root)

        self.assertFalse(changed)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(current['sourceGeneratedAtJST'], '2026-10-25T17:00:00+09:00')

    def test_material_return_change_rewrites(self):
        self.write_source(source('2026-10-25T17:00:00+09:00', payout=250))
        summary.write_if_material_changed(self.root)

        self.write_source(source('2026-10-25T17:10:00+09:00', payout=300))
        changed, current = summary.write_if_material_changed(self.root)

        self.assertTrue(changed)
        win = next(row for row in current['betTypes'] if row['id'] == 'win')
        self.assertEqual(win['returnYen'], 300)
        self.assertEqual(win['returnRatePct'], 150.0)


if __name__ == '__main__':
    unittest.main()
