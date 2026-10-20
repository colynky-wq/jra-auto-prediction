import unittest

import payout_parse_rescue as rescue


class PayoutParseRescueTests(unittest.TestCase):
    def test_6115_is_resolved_by_trio_set_6_11_5(self):
        html = '''<table>
        <tr><th>複勝</th><td>6115</td><td>320円180円270円</td><td>7番人気2番人気4番人気</td></tr>
        <tr><th>3連複</th><td>5-6-11</td><td>2,450円</td><td>8番人気</td></tr>
        <tr><th>3連単</th><td>6-11-5</td><td>18,420円</td><td>55番人気</td></tr>
        </table>'''
        rows, audit = rescue.parse_payout_html(
            html, '202506010105', '202506010105.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('6', 320.0), ('11', 180.0), ('5', 270.0)])
        self.assertEqual(audit['warning_count'], 0)
        self.assertEqual(audit['rescued_place_segments'], 1)

    def test_6115_is_resolved_by_trio_set_6_1_15(self):
        html = '''<table>
        <tr><th>複勝</th><td>6115</td><td>320円180円270円</td></tr>
        <tr><th>3連複</th><td>1-6-15</td><td>2,450円</td></tr>
        </table>'''
        rows, _ = rescue.parse_payout_html(
            html, '202506010105', '202506010105.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('6', 320.0), ('1', 180.0), ('15', 270.0)])

    def test_6115_is_resolved_by_result_table_when_other_pools_are_unhelpful(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬\n番</th><th>馬名</th></tr>
          <tr><td>1</td><td>4</td><td>6</td><td>A</td></tr>
          <tr><td>2</td><td>7</td><td>11</td><td>B</td></tr>
          <tr><td>3</td><td>3</td><td>5</td><td>C</td></tr>
          <tr><td>4</td><td>1</td><td>1</td><td>D</td></tr>
        </table>
        <table>
          <tr><th>複勝</th><td>6115</td><td>320円180円270円</td><td>7番人気2番人気4番人気</td></tr>
        </table>
        '''
        rows, audit = rescue.parse_payout_html(
            html, '202506010105', '202506010105.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('6', 320.0), ('11', 180.0), ('5', 270.0)])
        self.assertEqual(audit['warning_count'], 0)
        self.assertEqual(audit['rescued_place_segments'], 1)

    def test_result_table_can_choose_6_1_15_interpretation(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th></tr>
          <tr><td>1</td><td>4</td><td>6</td><td>A</td></tr>
          <tr><td>2</td><td>1</td><td>1</td><td>B</td></tr>
          <tr><td>3</td><td>8</td><td>15</td><td>C</td></tr>
          <tr><td>4</td><td>3</td><td>5</td><td>D</td></tr>
        </table>
        <table>
          <tr><th>複勝</th><td>6115</td><td>320円180円270円</td></tr>
        </table>
        '''
        rows, _ = rescue.parse_payout_html(
            html, '202506010105', '202506010105.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('6', 320.0), ('1', 180.0), ('15', 270.0)])

    def test_ambiguous_without_independent_hint_still_fails_closed(self):
        html = '''<table>
        <tr><th>複勝</th><td>6115</td><td>320円180円270円</td></tr>
        </table>'''
        with self.assertRaisesRegex(ValueError, 'could not pair'):
            rescue.parse_payout_html(
                html, '202506010105', '202506010105.html.gz', strict=True
            )

    def test_existing_unambiguous_compact_case_still_uses_base_parser(self):
        html = '''<table>
        <tr><th>複勝</th><td>11313</td><td>120円280円1,980円</td></tr>
        </table>'''
        rows, audit = rescue.parse_payout_html(
            html, '202506010101', '202506010101.html.gz', strict=True
        )
        place = [(r['組合せ'], r['払戻円']) for r in rows if r['券種'] == '複勝']
        self.assertEqual(place, [('11', 120.0), ('3', 280.0), ('13', 1980.0)])
        self.assertEqual(audit['warning_count'], 0)


if __name__ == '__main__':
    unittest.main()
