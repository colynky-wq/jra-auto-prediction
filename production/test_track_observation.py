import unittest
from datetime import datetime,timedelta,timezone
from track_observation_runtime import parse_track_observation
JST=timezone(timedelta(hours=9));T=datetime(2026,10,12,9,40,tzinfo=JST)
# Synthetic structural fixture. Numeric moisture tables must not determine going.
HTML='''<h2>第4回東京競馬第5日（2026年10月12日（月曜））</h2>
<h3>馬場状態（10月12日（月曜）9時現在）</h3><h4>芝</h4><p>稍重</p><h4>ダート</h4><p>良</p>
<h3>含水率</h3><p>芝 良 重 不良</p>'''
URL='https://www.jra.go.jp/keiba/baba/index.html'
class TestTrack(unittest.TestCase):
    def parse(self,html=HTML,t=T):return parse_track_observation(html,URL,'05','20261012',t)
    def test_timestamped_explicit_state(self):
        x=self.parse();self.assertEqual(x['going'],{'芝':'稍重','ダート':'良'});self.assertTrue(x['eligibleForRaceInput'])
    def test_stale_friday_is_not_current(self):
        x=self.parse(HTML.replace('10月12日（月曜）9時現在','10月9日（金曜）正午現在'))
        self.assertFalse(x['eligibleForRaceInput']);self.assertGreater(x['ageHours'],6)
    def test_future_timestamp(self):
        with self.assertRaises(ValueError):self.parse(HTML.replace('9時現在','10時現在'))
    def test_wrong_venue_or_date(self):
        for text in [HTML.replace('東京','京都'),HTML.replace('2026年10月12日','2026年10月11日')]:
            with self.assertRaises(ValueError):self.parse(text)
    def test_no_inference_from_moisture_or_explanatory_table(self):
        with self.assertRaises(ValueError):self.parse(HTML.replace('<p>稍重</p>','<p>14.2%</p>'))
    def test_track_fetched_after_post_cannot_attach(self):
        from test_prerace_snapshot import HTML as entry,ENTRIES,URL as entryurl,RID,DAY
        from prerace_snapshot_runtime import persist_snapshot
        import tempfile
        x=self.parse();x['capturedAtJST']=T.replace(hour=10).isoformat()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):persist_snapshot(entry,RID,DAY,entryurl,T,ENTRIES,folder,x)
if __name__=='__main__':unittest.main()
