import unittest

import payout_parse_rescue_v2 as rescue


class PlaceOrderRescueTests(unittest.TestCase):
    def test_7111_uses_result_order_7_11_1(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th></tr>
          <tr><td>1</td><td>4</td><td>7</td><td>A</td></tr>
          <tr><td>2</td><td>6</td><td>11</td><td>B</td></tr>
          <tr><td>3</td><td>1</td><td>1</td><td>C</td></tr>
          <tr><td>4</td><td>2</td><td>2</td><td>D</td></tr>
        </table>
        <table>
          <tr><th>複勝</th><td>7111</td><td>470円110円230円</td><td>7番人気1番人気4番人気</td></tr>
        </table>
        '''
        rows, audit = rescue.parse_payout_html(
            html, '202506010207', '202506010207.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('7', 470.0), ('11', 110.0), ('1', 230.0)])
        self.assertEqual(audit['warning_count'], 0)

    def test_7111_can_also_resolve_7_1_11_when_result_table_says_so(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th></tr>
          <tr><td>1</td><td>4</td><td>7</td><td>A</td></tr>
          <tr><td>2</td><td>1</td><td>1</td><td>B</td></tr>
          <tr><td>3</td><td>6</td><td>11</td><td>C</td></tr>
        </table>
        <table>
          <tr><th>複勝</th><td>7111</td><td>470円110円230円</td></tr>
        </table>
        '''
        rows, audit = rescue.parse_payout_html(
            html, '202506010207', '202506010207.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('7', 470.0), ('1', 110.0), ('11', 230.0)])
        self.assertEqual(audit['warning_count'], 0)


if __name__ == '__main__':
    unittest.main()
