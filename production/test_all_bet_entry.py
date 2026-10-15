import unittest
from copy import deepcopy

from all_bet_entry import parse_entry
from test_prerace_snapshot import HTML,URL,T,ENTRIES,RID,DAY


class EntryTests(unittest.TestCase):
    def test_exact_official_response_keeps_withdrawal_and_frame_assignments(self):
        value=parse_entry(HTML,RID,DAY,URL,T,ENTRIES)
        self.assertEqual([r['frame'] for r in value['runners']],[1,2])
        self.assertTrue(value['runners'][1]['withdrawn'])
        self.assertEqual(value['raceID'],RID)

    def test_image_frame_alt_and_unresolved_frame_are_not_guessed(self):
        html=HTML.replace('<td>1</td><td>1</td>','<td><img alt="1枠"></td><td>1</td>')
        self.assertEqual(parse_entry(html,RID,DAY,URL,T,ENTRIES)['runners'][0]['frame'],1)
        html=HTML.replace('<td>1</td><td>1</td>','<td>?</td><td>1</td>')
        self.assertIsNone(parse_entry(html,RID,DAY,URL,T,ENTRIES)['runners'][0]['frame'])

    def test_official_heading_or_post_mismatch_rejected(self):
        with self.assertRaises(ValueError):parse_entry(HTML.replace('東京','京都'),RID,DAY,URL,T,ENTRIES)
        with self.assertRaises(ValueError):parse_entry(HTML,RID,DAY,URL,T.replace(minute=50),ENTRIES)


if __name__=='__main__':unittest.main()
