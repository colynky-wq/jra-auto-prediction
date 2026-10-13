import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import pandas as pd
from prerace_snapshot_runtime import parse_snapshot,persist_snapshot

JST=timezone(timedelta(hours=9));RID='202605040501';DAY='20261012'
URL='https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0105202604050120261012%2FAC'
# Synthetic minimal fixture, not a historical pre-race observation.
HTML='''<h1>出馬表2026年10月12日（月曜）4回東京5日 1レース</h1>
<div class="race_header">発走時刻：9時50分 2歳未勝利 コース：1,400メートル（ダート・左）</div>
<table><tr><td>1</td><td>1</td><td>テスト馬A</td><td>牡2</td><td>前走 芝 良</td></tr>
<tr><td>2</td><td>2</td><td>テスト馬B 出走取消</td><td>牝2</td><td>前走 ダート 重</td></tr></table>'''
ENTRIES=pd.DataFrame({'馬番':[1,2],'馬名':['テスト馬A','テスト馬B']})
T=datetime(2026,10,12,9,40,tzinfo=JST)
class TestSnapshot(unittest.TestCase):
    def parse(self,html=HTML,url=URL,t=T,entries=ENTRIES):return parse_snapshot(html,RID,DAY,url,t,entries)
    def test_past_going_is_not_current(self):
        x=self.parse();self.assertIsNone(x['going']);self.assertEqual(x['activeEntrantsAtCapture'],1)
        self.assertTrue(x['prePostConfirmed']);self.assertTrue(x['runners'][1]['withdrawn'])
    def test_current_going_only(self):
        x=self.parse(HTML.replace('2歳未勝利','ダート：稍重 2歳未勝利'))
        self.assertEqual(x['going'],'稍重')
    def test_no_after_post_or_wrong_day(self):
        for t in [T.replace(hour=9,minute=50),T.replace(day=13),T.replace(tzinfo=None)]:
            with self.assertRaises(ValueError):self.parse(t=t)
    def test_source_identity(self):
        for url in [URL.replace('accessD','accessS'),URL.replace('www.jra.go.jp','example.com'),URL.replace('20261012','20261011')]:
            with self.assertRaises(ValueError):self.parse(url=url)
        with self.assertRaises(ValueError):self.parse(HTML.replace('東京','京都'))
    def test_missing_post_no_save(self):
        with self.assertRaises(ValueError):self.parse(HTML.replace('発走時刻：9時50分',''))
    def test_duplicate_and_incomplete(self):
        with self.assertRaises(ValueError):self.parse(entries=ENTRIES.iloc[:1])
        with self.assertRaises(ValueError):self.parse(HTML.replace('<td>2</td><td>2</td>','<td>2</td><td>1</td>'))
    def test_immutable_and_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            a=persist_snapshot(HTML,RID,DAY,URL,T,ENTRIES,folder);content=a.read_bytes()
            self.assertIsNone(persist_snapshot(HTML,RID,DAY,URL,T+timedelta(minutes=1),ENTRIES,folder))
            b=persist_snapshot(HTML.replace('出走取消',''),RID,DAY,URL,T+timedelta(minutes=2),ENTRIES,folder)
            self.assertNotEqual(a,b);self.assertEqual(a.read_bytes(),content)
    def test_no_forecast_fields_enabled(self):
        x=self.parse()
        for k in ['paceForecastEnabled','formalSignalsChanged','stakeSizingEnabled']:self.assertFalse(x[k])
        self.assertNotIn('odds',x);self.assertNotIn('actualSeconds',x)
if __name__=='__main__':unittest.main()
