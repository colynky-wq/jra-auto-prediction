from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

import win5_research as w
from win5_payouts import payout_document,yen,saved_payout,PayoutNotReady
from test_win5_research import NOW,DAY,plan,leg,hashes
from test_triple_baseline import result


def html(ids=None,amount='3万5100円',tickets='12,468票',winners=None):
    ids=ids or [r['race_id'] for r in plan()['schedule']]
    winners=winners or [(1,'horse1')]*5
    return (f'<title>WIN5対象レース | 2026年10月17日 レース情報(JRA)</title>'
            '<table><tr><th>払戻金</th><td>'+amount+'</td></tr><tr><th>的中票数</th><td>'+tickets+'</td></tr></table>'
            '<table class="win5raceresult2"><tr><td>レース</td>'+
            ''.join(f'<td><a href="result.html?race_id={rid}">race</a></td>' for rid in ids)+
            '</tr><tr><td>勝ち馬</td>'+''.join(f'<td><span class="RaceWaku Waku1">{n}</span>'
             f'<a href="https://db.netkeiba.com/horse/9999999999">{name}</a></td>' for n,name in winners)+
            '</tr></table>').encode()


class PayoutTests(unittest.TestCase):
    def test_japanese_yen_units_and_pool_source(self):
        for text,expected in [('35,100円',35100),('3万5100円',35100),('1億3584万8000円',135848000),('1億円',100000000)]:
            self.assertEqual(yen(text),expected)
        for text in ('-円','万円','1.2円','0万円ほか'):
            with self.assertRaises(PayoutNotReady):yen(text)
        value=payout_document(html(),DAY,[r['race_id'] for r in plan()['schedule']])
        self.assertEqual(value['payoutPer100Yen'],35100);self.assertEqual(value['winningTicketCount'],12468)

    def test_wrong_date_order_pending_ties_and_no_winning_tickets(self):
        ids=[r['race_id'] for r in plan()['schedule']]
        with self.assertRaises(ValueError):payout_document(html(),'2026-10-18',ids)
        with self.assertRaises(ValueError):payout_document(html(),DAY,list(reversed(ids)))
        with self.assertRaises(PayoutNotReady):payout_document(html(amount='-円'),DAY,ids)
        with self.assertRaises(PayoutNotReady):payout_document(html(tickets='-票'),DAY,ids)
        tied=html().replace(b'<span class="RaceWaku Waku1">1</span>',b'<span class="RaceWaku">1</span><span class="RaceWaku">2</span>',1)
        with self.assertRaises(PayoutNotReady):payout_document(tied,DAY,ids)
        value=payout_document(html(amount='的中なし',tickets='0票'),DAY,ids)
        self.assertEqual(value['payoutPer100Yen'],0)
        self.assertEqual(value['payoutStatus'],'carryover_no_winning_tickets')

    def test_full_validation_cache_preserves_original_date_and_checks_independent_winners(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=w.capture(root,plan(),leg,clock=lambda:NOW,hashes=hashes())
            record=json.loads(p.read_text());actual=[]
            for prediction in record['predictions']:
                row=result(prediction['prediction']);row['resultUpdatedAtJST']='2026-10-17 16:00:00';actual.append(row)
            (root/'reports/20261017/ResultData.json').write_text(json.dumps(actual))
            later=NOW+timedelta(days=1)
            report=w.build_validation(root,clock=lambda:later,fetch=lambda _:html())
            self.assertEqual(report['counts']['matched'],1);self.assertEqual(report['counts']['probability_matched'],1)
            self.assertEqual(report['races'][0]['payout']['payoutPer100Yen'],35100)
            self.assertEqual(report['races'][0]['raceDate'],DAY)
            report=w.build_validation(root,clock=lambda:later+timedelta(days=1),fetch=lambda _:self.fail('cache refetched'))
            self.assertEqual(report['counts']['matched'],1);self.assertIsNone(report['returnIndex'])
            cache=root/'reports/20261017/win5_payouts_v1/session.json'
            cache.unlink()
            report=w.build_validation(root,clock=lambda:later,fetch=lambda _:html(winners=[(2,'horse2')]*5))
            self.assertEqual(report['counts']['invalid'],1);self.assertFalse(cache.exists())
            report=w.build_validation(root,clock=lambda:later,fetch=lambda _:(_ for _ in ()).throw(OSError('offline')))
            self.assertEqual(report['counts']['payouts_pending'],1)


if __name__=='__main__':unittest.main()
