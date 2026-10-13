from __future__ import annotations

import unittest

from race_lap_runtime import LapParseError, parse_official_laps, segment_distances

RACE_ID = "202608040510"
DATE8 = "20261012"
URL = "https://www.jra.go.jp/JRADB/accessS.html?CNAME=pw01sde0108202604051020261012%2FE3"
KYOTO_LAPS = [12.3, 10.5, 10.8, 11.2, 11.6, 11.8, 12.3, 11.4, 11.5]


def page(distance=1800, laps=None, winner="1:43.4", last=35.2):
    sequence = " - ".join(f"{time:.1f}" for time in (KYOTO_LAPS if laps is None else laps))
    # Minimal factual result-table fixture, not a copy of the full JRA page.
    return f"""<h1>レース結果 2026年10月12日（月曜）4回京都5日 10レース</h1>
        <p>3歳以上2勝クラス コース：{distance:,}メートル（芝・右 外） 芝良</p>
        <table><tr><td class='place'>1</td><td class='time'>{winner}</td><td class='f_time'>34.7</td></tr></table>
        <table><tr><th>ハロンタイム</th><td>{sequence}</td></tr>
        <tr><th>上り</th><td>4F 47.0 - 3F {last:.1f}</td></tr></table>"""


class RaceLapTests(unittest.TestCase):
    def test_real_official_sequence_and_individual_last3f_are_distinct(self):
        record = parse_official_laps(page(), RACE_ID, DATE8, URL)
        self.assertEqual(record["first600mSeconds"], 33.6)
        self.assertEqual(record["first1000mSeconds"], 56.4)
        self.assertEqual(record["raceLast600mSeconds"], 35.2)
        self.assertNotEqual(record["raceLast600mSeconds"], 34.7)
        self.assertEqual(record["lapTotalSeconds"], record["winnerTimeSeconds"])

    def test_odd_100m_distance_is_not_interpolated_to_600_or_1000(self):
        laps = [7.5] + [12.0] * 10
        record = parse_official_laps(page(2100, laps, "2:07.5", 36.0), RACE_ID, DATE8, URL)
        self.assertEqual(record["lapDistancesM"], [100] + [200] * 10)
        self.assertIsNone(record["first600mSeconds"])
        self.assertIsNone(record["first1000mSeconds"])
        self.assertEqual(record["earlyRecordedDistanceM"], 700)
        self.assertEqual(record["earlyRecordedTimeSeconds"], 43.5)

    def test_missing_race_laps_cannot_use_individual_time_instead(self):
        html = page().replace("ハロンタイム", "missing")
        with self.assertRaises(LapParseError):
            parse_official_laps(html, RACE_ID, DATE8, URL)

    def test_wrong_html_identity_is_rejected(self):
        for old, new in [("10月12日", "10月11日"), ("京都5日", "東京5日"), ("10レース", "11レース")]:
            with self.assertRaises(LapParseError):
                parse_official_laps(page().replace(old, new), RACE_ID, DATE8, URL)

    def test_wrong_route_or_non_official_source_is_rejected(self):
        for url in [URL.replace("040510", "040511"), URL.replace("www.jra.go.jp", "example.com")]:
            with self.assertRaises(LapParseError):
                parse_official_laps(page(), RACE_ID, DATE8, url)

    def test_both_observed_jra_route_modes_are_supported(self):
        newer = URL.replace("pw01sde01", "pw01sde10")
        self.assertEqual(parse_official_laps(page(), RACE_ID, DATE8, URL)["lapTimesSeconds"],
                         parse_official_laps(page(), RACE_ID, DATE8, newer)["lapTimesSeconds"])

    def test_distance_and_lap_count_must_agree(self):
        for distance, count in [(1800, 8), (2100, 10), (1551, 8)]:
            with self.assertRaises(LapParseError):
                segment_distances(distance, count)

    def test_total_time_disagreement_is_rejected(self):
        with self.assertRaises(LapParseError):
            parse_official_laps(page(winner="1:44.9"), RACE_ID, DATE8, URL)

    def test_race_last600_disagreement_is_rejected(self):
        with self.assertRaises(LapParseError):
            parse_official_laps(page(last=34.7), RACE_ID, DATE8, URL)

    def test_unfinished_or_invalid_sequence_is_rejected(self):
        for html in [page(winner=""), page().replace("12.3 - 10.5", "NaN - 10.5"), page().replace("12.3 - 10.5", "0.0 - 10.5")]:
            with self.assertRaises(LapParseError):
                parse_official_laps(html, RACE_ID, DATE8, URL)


if __name__ == "__main__":
    unittest.main()
