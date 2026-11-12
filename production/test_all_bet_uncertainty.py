import csv
import tempfile
import unittest
from pathlib import Path

import research_all_bet_uncertainty as unc


def sample(race, bet, ev, hit=False, payout=0):
    return {
        'race_id': race, 'bet_type': bet, 'combo': '1', 'model_prob': 0.2,
        'odds': 6.0, 'model_ev': ev, 'settled': True, 'hit': hit,
        'payout_yen_per_100': payout, 'oos_scope_verified': True,
    }


class DayBootstrapTests(unittest.TestCase):
    def test_load_race_dates_accepts_compact_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'manifest.csv'
            p.write_text('レースID,日付\nR1,20250105\nR2,2025/01/06\n', encoding='utf-8-sig')
            self.assertEqual(unc.load_race_dates(p), {'R1': '2025-01-05', 'R2': '2025-01-06'})

    def test_whole_day_bootstrap_preserves_day_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / 'samples.csv'
            rows = [
                sample('R1', '単勝', 1.2, True, 300),
                sample('R2', '単勝', 1.1, False, 0),
                sample('R3', '単勝', 1.3, False, 0),
            ]
            with samples.open('w', encoding='utf-8-sig', newline='') as h:
                w = csv.DictWriter(h, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            manifest = root / 'manifest.csv'
            manifest.write_text(
                'レースID,日付\nR1,20250105\nR2,20250105\nR3,20250106\n',
                encoding='utf-8-sig',
            )
            dates = unc.load_race_dates(manifest)
            state = unc.aggregate_days(samples, dates, thresholds=(1.0,), top_ks=(1,), stake_yen=100)
            by_day = state[(1.0, 1, '単勝')]
            self.assertEqual(set(by_day), {'2025-01-05', '2025-01-06'})
            self.assertEqual(by_day['2025-01-05']['stake'], 200)
            self.assertEqual(by_day['2025-01-05']['return'], 300)
            output = unc.bootstrap_rows(state, reps=200, seed=7)
            win = next(r for r in output if r['bet_type'] == '単勝')
            self.assertEqual(win['day_count'], 2)
            self.assertEqual(win['selected_bets'], 3)
            self.assertAlmostEqual(win['roi_pct'], 100.0)
            self.assertEqual(win['uncertainty_status'], 'few_days')
            self.assertIsNotNone(win['roi_ci_low_pct'])
            self.assertIsNotNone(win['roi_ci_high_pct'])

    def test_top_k_is_applied_per_race_before_day_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / 'samples.csv'
            rows = [
                {'race_id': 'R1', 'bet_type': 'ワイド', 'combo': '1-2', 'model_prob': .2,
                 'odds': 8, 'model_ev': 1.5, 'settled': True, 'hit': True, 'payout_yen_per_100': 180},
                {'race_id': 'R1', 'bet_type': 'ワイド', 'combo': '1-3', 'model_prob': .2,
                 'odds': 7, 'model_ev': 1.4, 'settled': True, 'hit': False, 'payout_yen_per_100': 0},
            ]
            with samples.open('w', encoding='utf-8-sig', newline='') as h:
                w = csv.DictWriter(h, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
            state = unc.aggregate_days(samples, {'R1': '2025-01-05'}, thresholds=(1.0,), top_ks=(1, 2), stake_yen=100)
            self.assertEqual(state[(1.0, 1, 'ワイド')]['2025-01-05']['stake'], 100)
            self.assertEqual(state[(1.0, 1, 'ワイド')]['2025-01-05']['return'], 180)
            self.assertEqual(state[(1.0, 2, 'ワイド')]['2025-01-05']['stake'], 200)
            self.assertEqual(state[(1.0, 2, 'ワイド')]['2025-01-05']['return'], 180)


if __name__ == '__main__':
    unittest.main()
