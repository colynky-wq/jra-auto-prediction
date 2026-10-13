import unittest
from datetime import date
from backfill_race_laps import validate_years
class TestCutoff(unittest.TestCase):
    def test_current_year_needs_past_cutoff(self):
        self.assertEqual(validate_years([2026],'20261012',date(2026,10,13)),date(2026,10,12))
        for cutoff in [None,'20261013','20261014']:
            with self.assertRaises(ValueError):validate_years([2026],cutoff,date(2026,10,13))
    def test_previous_years_unchanged(self):
        self.assertIsNone(validate_years([2024,2025],None,date(2026,10,13)))
    def test_future_year_invalid(self):
        with self.assertRaises(ValueError):validate_years([2027],'20261012',date(2026,10,13))
if __name__=='__main__':unittest.main()
