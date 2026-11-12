import unittest

import payout_parse_rescue_v4 as p


class OrderedPoolPlaceRescueTests(unittest.TestCase):
    def test_trifecta_order_resolves_7111_without_result_table(self):
        html = '''
        <table>
          <tr><th>単勝</th><td>7</td><td>430円</td><td>3番人気</td><th>馬単</th><td>7-11</td><td>2,100円</td><td>8番人気</td></tr>
          <tr><th>複勝</th><td>7111</td><td>150円110円210円</td><td>3番人気1番人気5番人気</td><th>ワイド</th><td>7-111-71-11</td><td>200円850円340円</td><td>2番人気11番人気4番人気</td></tr>
          <tr><th>馬連</th><td>7-11</td><td>1,000円</td><td>4番人気</td><th>3連複</th><td>1-7-11</td><td>2,500円</td><td>10番人気</td></tr>
          <tr><th>3連単</th><td>7-11-1</td><td>12,000円</td><td>35番人気</td></tr>
        </table>
        '''
        rows, audit = p.parse_payout_html(html, '202505020510', 'sample.html.gz', strict=True)
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        wide = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == 'ワイド']
        self.assertEqual(place, [('7', 150.0), ('11', 110.0), ('1', 210.0)])
        self.assertEqual(wide, [('7-11', 200.0), ('1-7', 850.0), ('1-11', 340.0)])
        self.assertEqual(audit['warning_count'], 0)
        self.assertEqual(audit['rescued_place_segments'], 1)
        self.assertEqual(audit['rescued_wide_segments'], 1)

    def test_trifecta_order_distinguishes_8111(self):
        html = '''
        <table>
          <tr><th>単勝</th><td>8</td><td>300円</td><th>馬単</th><td>8-11</td><td>1,500円</td></tr>
          <tr><th>複勝</th><td>8111</td><td>150円220円480円</td><td>1番人気4番人気8番人気</td></tr>
          <tr><th>3連複</th><td>1-8-11</td><td>3,000円</td><th>3連単</th><td>8-11-1</td><td>18,000円</td></tr>
        </table>
        '''
        rows, audit = p.parse_payout_html(html, '202506020808', 'sample2.html.gz', strict=True)
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('8', 150.0), ('11', 220.0), ('1', 480.0)])
        self.assertEqual(audit['warning_count'], 0)


if __name__ == '__main__':
    unittest.main()
