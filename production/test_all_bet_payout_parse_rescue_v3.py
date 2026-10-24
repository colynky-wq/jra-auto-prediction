import unittest

import payout_parse_rescue_v3 as parser


class PayoutParseRescueV3Tests(unittest.TestCase):
    def test_invalid_same_horse_wide_candidate_becomes_warning_not_raw_exception(self):
        html = '''<table>
        <tr><th>ワイド</th><td>11-11</td><td>300円</td></tr>
        </table>'''
        rows, audit = parser.parse_payout_html(
            html, '202501020511', 'sample.html.gz', strict=False
        )
        self.assertEqual(rows, [])
        self.assertEqual(audit['warning_count'], 1)
        self.assertIn('ワイド could not pair', audit['warnings'])

    def test_7111_uses_result_order(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th></tr>
          <tr><td>1</td><td>4</td><td>7</td><td>A</td></tr>
          <tr><td>2</td><td>6</td><td>11</td><td>B</td></tr>
          <tr><td>3</td><td>1</td><td>1</td><td>C</td></tr>
          <tr><td>4</td><td>2</td><td>3</td><td>D</td></tr>
        </table>
        <table>
          <tr><th>単勝</th><td>7</td><td>420円</td><td>2番人気</td></tr>
          <tr><th>複勝</th><td>7111</td><td>150円110円210円</td></tr>
          <tr><th>3連複</th><td>1-7-11</td><td>2,300円</td></tr>
          <tr><th>3連単</th><td>7-11-1</td><td>11,000円</td></tr>
        </table>
        '''
        rows, audit = parser.parse_payout_html(
            html, '202505020510', 'sample.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('7', 150.0), ('11', 110.0), ('1', 210.0)])
        self.assertEqual(audit['warning_count'], 0)


if __name__ == '__main__':
    unittest.main()
