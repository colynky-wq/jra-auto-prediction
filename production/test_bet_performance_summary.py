import json
from pathlib import Path
import tempfile
import unittest

import build_bet_performance_summary as summary


def source(stamp, payout=250):
    return {
        'generatedAtJST': stamp,
        'races': [{
            'id': 'win:2026-10-25:race1',
            'betType': 'win',
            'raceDate': '2026-10-25',
            'raceID': 'race1',
            'status': 'matched',
            'candidates': [
                {'id': '01', 'unitYen': 100, 'hit': True, 'payoutYen': payout},
                {'id': '02', 'unitYen': 100, 'hit': False, 'payoutYen': 0},
            ],
        }],
    }


class TestBetPerformanceSummary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.report = self.root / 'reports' / '20261025' / 'BetResearchData.json'
        self.report.parent.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, value):
        self.report.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

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
