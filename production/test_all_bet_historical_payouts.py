import unittest

import normalize_historical_payouts as p


class HistoricalPayoutNormalizationTests(unittest.TestCase):
    def test_standard_all_bet_table_is_normalized(self):
        html = '''
        <table>
          <tr><th>単勝</th><td>8</td><td>4,450円</td><td>9番人気</td></tr>
          <tr><th>複勝</th><td>8<br>1<br>4</td><td>820円<br>340円<br>200円</td><td>10番人気<br>6番人気<br>3番人気</td></tr>
          <tr><th>枠連</th><td>1－6</td><td>2,210円</td><td>9番人気</td></tr>
          <tr><th>ワイド</th><td>1-8<br>4-8<br>1-4</td><td>5,210円<br>2,320円<br>620円</td><td>42番人気<br>29番人気<br>6番人気</td></tr>
          <tr><th>馬連</th><td>1-8</td><td>30,940円</td><td>44番人気</td></tr>
          <tr><th>馬単</th><td>8→1</td><td>73,450円</td><td>94番人気</td></tr>
          <tr><th>3連複</th><td>1-4-8</td><td>29,440円</td><td>76番人気</td></tr>
          <tr><th>3連単</th><td>8→1→4</td><td>383,690円</td><td>602番人気</td></tr>
        </table>
        '''
        rows, audit = p.parse_payout_html(html, '202506040609', 'sample.html.gz')
        got = {(r['券種'], r['組合せ']): r['払戻円'] for r in rows}
        self.assertEqual(got[('単勝', '8')], 4450.0)
        self.assertEqual(got[('複勝', '1')], 340.0)
        self.assertEqual(got[('枠連', '1-6')], 2210.0)
        self.assertEqual(got[('ワイド', '1-8')], 5210.0)
        self.assertEqual(got[('馬連', '1-8')], 30940.0)
        self.assertEqual(got[('馬単', '8-1')], 73450.0)
        self.assertEqual(got[('3連複', '1-4-8')], 29440.0)
        self.assertEqual(got[('3連単', '8-1-4')], 383690.0)
        self.assertEqual(audit['covered_bet_count'], 8)
        self.assertEqual(audit['warning_count'], 0)

    def test_unordered_combos_are_canonical_but_ordered_are_preserved(self):
        html = '''<table>
        <tr><th>馬連</th><td>16-7</td><td>680円</td></tr>
        <tr><th>馬単</th><td>16→7</td><td>1,510円</td></tr>
        <tr><th>3連複</th><td>12-3-8</td><td>4,420円</td></tr>
        <tr><th>3連単</th><td>12→3→8</td><td>57,817円</td></tr>
        </table>'''
        rows, _ = p.parse_payout_html(html, '202507020403')
        got = {(r['券種'], r['組合せ']) for r in rows}
        self.assertIn(('馬連', '7-16'), got)
        self.assertIn(('馬単', '16-7'), got)
        self.assertIn(('3連複', '3-8-12'), got)
        self.assertIn(('3連単', '12-3-8'), got)

    def test_rowspan_style_followup_row_keeps_current_bet(self):
        html = '''<table>
        <tr><th rowspan="3">複勝</th><td>7</td><td>130円</td></tr>
        <tr><td>3</td><td>180円</td></tr>
        <tr><td>11</td><td>240円</td></tr>
        </table>'''
        rows, audit = p.parse_payout_html(html, '202507020403')
        self.assertEqual([(r['組合せ'], r['払戻円']) for r in rows],
                         [('7', 130.0), ('3', 180.0), ('11', 240.0)])
        self.assertEqual(audit['warning_count'], 0)

    def test_dead_heat_multiple_winners_are_kept(self):
        html = '''<table>
        <tr><th>馬連</th><td>1-2<br>1-3</td><td>1,200円<br>1,700円</td></tr>
        <tr><th>馬単</th><td>1→2<br>1→3</td><td>2,200円<br>3,100円</td></tr>
        </table>'''
        rows, _ = p.parse_payout_html(html, '202507020403')
        self.assertEqual(len(rows), 4)
        got = {(r['券種'], r['組合せ']): r['払戻円'] for r in rows}
        self.assertEqual(got[('馬連', '1-3')], 1700.0)
        self.assertEqual(got[('馬単', '1-3')], 3100.0)

    def test_mismatched_row_fails_closed_by_default(self):
        html = '''<table><tr><th>ワイド</th><td>1-2</td><td>300円<br>400円</td></tr></table>'''
        with self.assertRaisesRegex(ValueError, 'could not pair'):
            p.parse_payout_html(html, '202507020403')

    def test_warning_mode_skips_unpairable_row_and_audits_it(self):
        html = '''<table><tr><th>ワイド</th><td>1-2</td><td>300円<br>400円</td></tr></table>'''
        rows, audit = p.parse_payout_html(html, '202507020403', strict=False)
        self.assertEqual(rows, [])
        self.assertEqual(audit['warning_count'], 1)

    def test_filename_race_id(self):
        self.assertEqual(
            p.race_id_from_path('/tmp/202506040609.html.gz'),
            '202506040609',
        )
        with self.assertRaises(ValueError):
            p.race_id_from_path('/tmp/not-a-race.html.gz')


if __name__ == '__main__':
    unittest.main()
