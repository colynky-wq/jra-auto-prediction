from copy import deepcopy
import unittest

from pace_baseline_runtime import build_baselines, classify_actual
from race_lap_pdf_runtime import parse_report_half, restore_numeric_glyph, validate_report_url
from race_lap_runtime import segment_distances
from historical_jra_laps import month_token, result_urls

URL = "https://www.jra.go.jp/datafile/seiseki/report/2024/2024-1nakayama1.pdf"
EXPECTED = {"raceID": "202406010104", "date8": "20240106", "distanceM": 1800,
            "surface": "ダ", "winnerTimeSeconds": 117.8}
# Minimal factual timing fields only; no full copyrighted report is bundled.
TEXT = """00004 1月6日 晴 良 (2024年1中山)第1日 第4競走 (cid:4)(cid:5)1,800(cid:2)
発走11時35分 (ダート・右)
コースレコード1:48.5重
中央レコード1:47.6重
勝馬 1:57.8
ハロンタイム 12.9―11.8―13.9―13.6―13.0―12.7―12.7―13.4―13.8
上り4F52.6―3F39.9
"""


def race(index, year=2024, value=35.0, going="良", detail="芝・右"):
    return {"raceID": f"{year}060101{index:04d}", "raceDate": f"{year}-01-06",
            "venue": "中山", "surface": "芝", "distanceM": 1800,
            "courseDetail": detail, "going": going,
            "earlyRecordedDistanceM": 600, "earlyRecordedTimeSeconds": value,
            "validation": {"headingMatches": True}}


class PdfTests(unittest.TestCase):
    def test_pdf_matches_independent_actual_html_timings(self):
        record = parse_report_half(TEXT, URL, EXPECTED, 2, 2)
        self.assertEqual(record["winnerTimeSeconds"], 117.8)
        self.assertEqual(record["first600mSeconds"], 38.6)
        self.assertEqual(record["raceLast600mSeconds"], 39.9)
        self.assertTrue(all(record["validation"].values()))

    def test_heading_serial_is_not_joined_to_month(self):
        record = parse_report_half(TEXT, URL, EXPECTED, 2, 2)
        self.assertEqual(record["raceDate"], "2024-01-06")

    def test_required_numeric_glyph_from_unknown_font_is_rejected(self):
        self.assertEqual(restore_numeric_glyph("(cid:9874)", "ABCDEF+RyuminPr6N-Light"), "2")
        with self.assertRaises(ValueError): restore_numeric_glyph("(cid:9874)", "UnknownFont")

    def test_nonofficial_url_or_wrong_venue_rejected(self):
        for url in [URL.replace("www.jra.go.jp", "example.com"), URL.replace("nakayama", "tokyo")]:
            with self.assertRaises(ValueError): validate_report_url(url, EXPECTED["raceID"], EXPECTED["date8"])

    def test_history_mismatch_and_lap_tail_mismatch_rejected(self):
        expected = dict(EXPECTED, winnerTimeSeconds=118.8)
        with self.assertRaises(ValueError): parse_report_half(TEXT, URL, expected, 2, 2)
        with self.assertRaises(ValueError): parse_report_half(TEXT.replace("3F39.9", "3F34.0"), URL, EXPECTED, 2, 2)

    def test_fukushima_1150m_starts_at_150m(self):
        lengths = segment_distances(1150, 6)
        self.assertEqual(lengths, [150,200,200,200,200,200])
        self.assertEqual(sum(lengths[:4]), 750)
        with self.assertRaises(ValueError): segment_distances(1150, 5)

    def test_month_selector_uses_observed_checksum(self):
        html = 'pw01skl10 objParam["2401"]="B3";'
        self.assertEqual(month_token(html, "20240106"), "pw01skl10202401/B3")
        with self.assertRaises(ValueError): month_token(html, "20240201")
        routes = result_urls('pw01sde1006202401010420240106/40')
        self.assertIn('202406010104:20240106', routes)


class BaselineTests(unittest.TestCase):
    def test_2025_and_2026_do_not_enter_thresholds(self):
        train = [race(i, value=33+i/10) for i in range(40)]
        base = build_baselines(train)
        self.assertEqual(base, build_baselines(train+[race(1,2025,20),race(2,2026,50)]))

    def test_fewer_than_thirty_same_condition_races_is_held(self):
        self.assertEqual(build_baselines([race(i) for i in range(29)]), [])

    def test_going_and_outer_course_are_not_mixed(self):
        records = [race(i) for i in range(30)]
        base = build_baselines(records)
        self.assertIsNone(classify_actual(race(80,2025,going="重"),base))
        self.assertIsNone(classify_actual(race(81,2025,detail="芝・右・外"),base))

    def test_700m_offset_is_not_mixed_with_600m(self):
        base = build_baselines([race(i) for i in range(30)])
        other = race(90,2025);other["earlyRecordedDistanceM"] = 700
        self.assertIsNone(classify_actual(other,base))

    def test_bad_validation_and_nan_are_excluded(self):
        records = [race(i) for i in range(29)]
        bad = race(90);bad["validation"]["headingMatches"] = False
        self.assertEqual(build_baselines(records+[bad,race(91,value=float("nan"))]),[])

    def test_threshold_boundaries_and_duplicate_identity(self):
        train = [race(i,value=35) for i in range(30)]
        base = build_baselines(train)
        self.assertEqual(classify_actual(race(80,2025,34),base),"fast")
        self.assertEqual(classify_actual(race(81,2025,35),base),"middle")
        self.assertEqual(classify_actual(race(82,2025,36),base),"slow")
        with self.assertRaises(ValueError): build_baselines(train+[deepcopy(train[0])])


if __name__ == "__main__": unittest.main()
