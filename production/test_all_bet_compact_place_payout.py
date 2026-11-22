import unittest

import normalize_historical_payouts as p


class CompactPlacePayoutRegressionTests(unittest.TestCase):
    def test_concatenated_place_numbers_discard_duplicate_split(self):
        html = '''<table>
        <tr><th>複勝</th><td>11313</td><td>120円<br>280円<br>1,980円</td><td>1番人気<br>6番人気<br>11番人気</td></tr>
        </table>'''
        rows, audit = p.parse_payout_html(
            html,
            '202506010101',
            '202506010101.html.gz',
            strict=True,
        )
        self.assertEqual(
            [(r['組合せ'], r['払戻円']) for r in rows],
            [('11', 120.0), ('3', 280.0), ('13', 1980.0)],
        )
        self.assertEqual(audit['warning_count'], 0)


if __name__ == '__main__':
    unittest.main()
