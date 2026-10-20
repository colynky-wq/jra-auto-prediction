import unittest

import payout_parse_rescue_v2 as rescue


class JointPayoutRescueTests(unittest.TestCase):
    def test_real_202506010105_compact_place_and_wide(self):
        html = '''
        <table>
          <tr><th>着順</th><th>枠</th><th>馬番</th><th>馬名</th></tr>
          <tr><td>1</td><td>3</td><td>6</td><td>A</td></tr>
          <tr><td>2</td><td>6</td><td>11</td><td>B</td></tr>
          <tr><td>3</td><td>3</td><td>5</td><td>C</td></tr>
        </table>
        <table>
          <tr><th>単勝</th><td>6</td><td>720円</td><td>3番人気</td><th>馬単</th><td>6-11</td><td>4,180円</td><td>16番人気</td></tr>
          <tr><th>複勝</th><td>6115</td><td>320円180円270円</td><td>7番人気2番人気4番人気</td><th>ワイド</th><td>6-115-65-11</td><td>950円1,640円990円</td><td>11番人気24番人気12番人気</td></tr>
          <tr><th>枠連</th><td>3-6</td><td>1,350円</td><td>7番人気</td><th>3連複</th><td>5-6-11</td><td>9,070円</td><td>31番人気</td></tr>
          <tr><th>馬連</th><td>6-11</td><td>2,430円</td><td>9番人気</td><th>3連単</th><td>6-11-5</td><td>44,520円</td><td>157番人気</td></tr>
        </table>
        '''
        rows, audit = rescue.parse_payout_html(
            html, '202506010105', '202506010105.html.gz', strict=True
        )
        got = {(r['券種'], r['組合せ']): r['払戻円'] for r in rows}
        self.assertEqual(got[('複勝', '6')], 320.0)
        self.assertEqual(got[('複勝', '11')], 180.0)
        self.assertEqual(got[('複勝', '5')], 270.0)
        self.assertEqual(got[('ワイド', '6-11')], 950.0)
        self.assertEqual(got[('ワイド', '5-6')], 1640.0)
        self.assertEqual(got[('ワイド', '5-11')], 990.0)
        self.assertEqual(audit['warning_count'], 0)
        self.assertEqual(audit['covered_bet_count'], 8)
        self.assertEqual(audit['rescued_place_segments'], 1)
        self.assertEqual(audit['rescued_wide_segments'], 1)

    def test_compact_wide_without_independent_top3_evidence_fails_closed(self):
        html = '''<table>
          <tr><th>ワイド</th><td>6-115-65-11</td><td>950円1,640円990円</td></tr>
        </table>'''
        with self.assertRaisesRegex(ValueError, 'could not pair'):
            rescue.parse_payout_html(html, '202506010105', 'x.html.gz', strict=True)

    def test_pair_solver_finds_triangle_interpretation_among_ambiguities(self):
        solutions = rescue._wide_pair_solutions('6-115-65-11', 3)
        self.assertIn(((6, 11), (5, 6), (5, 11)), solutions)
        self.assertGreaterEqual(len(solutions), 2)


if __name__ == '__main__':
    unittest.main()
