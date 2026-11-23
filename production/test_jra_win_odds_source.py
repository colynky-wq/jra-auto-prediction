"""Offline fake response tests for the JRA public interim win odds adapter."""
from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from jra_win_odds_source import (
    ACCESS_O, JRAWinOddsError, discover_win_odds_cname,
    fetch_jra_win_odds, parse_jra_win_odds,
)
from win_odds_freshness import WinOddsFreshnessError, validate_win_odds_freshness

RACE = "202605050602"
DATE = "20261123"
RECEIVED = datetime(2026, 11, 23, 10, 22, tzinfo=ZoneInfo("Asia/Tokyo"))
TOKEN = "pw15oli00/6D"


def html(*, clock="10時20分現在オッズ", label="2026年11月23日（月曜）5回東京6日 2レース",
         rows=None):
    rows = rows or [(1, "3.5"), (2, "17.2"), (3, "42.8")]
    body = "".join(
        f'<tr><td class="num">{n}</td><td class="odds_tan">{odd}</td></tr>'
        for n, odd in rows
    )
    return (f'<h1>単勝・複勝オッズ（馬番順）<span class="opt">{label}</span></h1>'
            f'<div class="cell time"><strong>{clock}</strong></div>'
            f'<table class="basic narrow-xy tanpuku">{body}</table>')


class JRAWinOddsSourceTests(unittest.TestCase):
    def test_interim_correct_race_and_origination_minute(self):
        odds, source = parse_jra_win_odds(html(), RACE, DATE, RECEIVED)
        self.assertEqual(odds["馬番"].tolist(), [1, 2, 3])
        self.assertEqual(odds["単勝オッズ"].tolist(), [3.5, 17.2, 42.8])
        self.assertEqual(source["sourceProvider"], "jra_official_web")
        self.assertEqual(source["sourceDisplayLabel"], "10時20分現在オッズ")
        self.assertEqual(source["data"]["official_datetime"], "2026-11-23T10:20:00+09:00")
        fresh = validate_win_odds_freshness(source, RECEIVED)
        self.assertEqual(fresh["sourceField"], "jra.html.interim_label")
        self.assertEqual(fresh["sourceAgeSecondsAtReceipt"], 120)

    def test_no_last_odds_pass_as_interim(self):
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(html(clock="最終オッズ"), RACE, DATE, RECEIVED)

    def test_no_unknown_clock(self):
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(html(clock="オッズ更新"), RACE, DATE, RECEIVED)

    def test_no_wrong_race_or_date(self):
        with self.assertRaisesRegex(JRAWinOddsError, "identity_mismatch"):
            parse_jra_win_odds(html(label="2026年11月23日（月曜）5回東京6日 3レース"),
                               RACE, DATE, RECEIVED)
        with self.assertRaisesRegex(JRAWinOddsError, "identity_mismatch"):
            parse_jra_win_odds(html(), RACE, "20261124", RECEIVED)

    def test_no_stale_timestamp_even_when_http_new(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "stale_over_30m"):
            parse_jra_win_odds(html(clock="09時51分現在オッズ"), RACE, DATE, RECEIVED)

    def test_no_future_or_ambiguous_timestamp(self):
        with self.assertRaisesRegex(WinOddsFreshnessError, "in_future"):
            parse_jra_win_odds(html(clock="10時23分現在オッズ"), RACE, DATE, RECEIVED)
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(
                html().replace('<div class="cell time">', '<div class="cell time"><strong>10時19分現在オッズ</strong>'),
                RACE, DATE, RECEIVED)

    def test_no_duplicate_or_incomplete_row(self):
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(html(rows=[(1, "2.5"), (1, "2.5")]), RACE, DATE, RECEIVED)
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(html(rows=[(1, "2.5"), (2, "取消")]), RACE, DATE, RECEIVED)
        with self.assertRaises(JRAWinOddsError):
            parse_jra_win_odds(html(rows=[(1, "2.5"), (2, "--")]), RACE, DATE, RECEIVED)

    def test_observed_navigation_token_only(self):
        entry = '<a onclick="doAction(\x27/JRADB/accessO.html\x27,\x27pw15oli00/6D\x27)">オッズ</a>'
        self.assertEqual(discover_win_odds_cname(entry), TOKEN)
        with self.assertRaises(JRAWinOddsError):
            discover_win_odds_cname("<a>オッズ</a>")
        with self.assertRaises(JRAWinOddsError):
            discover_win_odds_cname(entry + entry.replace(TOKEN, "pw15oli00/6E"))

    def test_no_source_fabricated_from_receipt(self):
        for source in ("09時52分現在オッズ", "09時50分現在オッズ"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(WinOddsFreshnessError, "stale_over_30m"):
                    parse_jra_win_odds(html(clock=source), RACE, DATE,
                                       RECEIVED + timedelta(minutes=1))

    def test_fake_http_single_fetch_and_provenance(self):
        class Response:
            status_code = 200
            apparent_encoding = "shift_jis"
            def __init__(self, content):
                self.content = content.encode("shift_jis")

        entry = '<a onclick="doAction(\x27/JRADB/accessO.html\x27,\x27pw15oli00/6D\x27)">オッズ</a>'
        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = []
            def get(self, url, **kwargs):
                self.calls.append(("GET", url))
                return Response(entry)
            def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs.get("data")))
                return Response(html())

        session = Session()
        with patch("jra_win_odds_source.discover_entry_url",
                   return_value="https://www.jra.go.jp/JRADB/accessD.html?CNAME=observed"):
            odds, source = fetch_jra_win_odds(RACE, DATE, session=session,
                                              clock=lambda: RECEIVED)
        self.assertEqual(len(odds), 3)
        self.assertEqual(session.calls[1][0:2], ("POST", ACCESS_O))
        self.assertEqual(session.calls[1][2], {"cname": TOKEN})
        self.assertEqual(source["preflightFreshnessEvidence"]["freshnessStatus"],
                         "verified_within_30m")


if __name__ == "__main__":
    unittest.main()
