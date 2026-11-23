"""Offline fake response tests for the JRA public interim win odds adapter."""
from __future__ import annotations

from datetime import datetime, timedelta
from copy import deepcopy
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from jra_win_odds_source import (
    ACCESS_O, JRAAccessRestricted, JRAWinOddsError, discover_win_odds_cname,
    fetch_jra_win_odds, parse_jra_win_odds,
)
from win_odds_freshness import WinOddsFreshnessError, validate_win_odds_freshness
from win_odds_retry import retryable_win_odds_failure
from jra_entry_source import JRAEntryAccessRestricted, discover_entry_url

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
        self.assertEqual(fresh["sourceDisplayLabel"], "10時20分現在オッズ")
        self.assertEqual(fresh["sourceRaceID"], RACE)
        self.assertEqual(fresh["sourceRaceDateJST"], DATE)
        self.assertEqual(fresh["sourceTimePrecision"], "minute")
        self.assertRegex(fresh["sourcePageSha256"], r"^[0-9a-f]{64}$")

    def test_jra_freshness_requires_original_display_proof(self):
        _, payload = parse_jra_win_odds(html(), RACE, DATE, RECEIVED)
        valid = validate_win_odds_freshness(payload, RECEIVED)
        self.assertEqual(valid["sourceField"], "jra.html.interim_label")
        for key in ("sourceDisplayLabel", "sourceRaceDateJST",
                    "sourceRaceID", "sourceTimePrecision",
                    "sourcePageSha256", "sourceURL"):
            broken = deepcopy(payload)
            del broken[key]
            with self.subTest(missing=key), self.assertRaises(WinOddsFreshnessError):
                validate_win_odds_freshness(broken, RECEIVED)
        for key, changed in (
            ("sourceDisplayLabel", "10時15分現在オッズ"),
            ("sourceRaceDateJST", "20261122"),
            ("sourceTimePrecision", "unknown"),
            ("sourcePageSha256", "0" * 63),
            ("sourceURL", "https://example.invalid/jra"),
        ):
            broken = deepcopy(payload)
            broken[key] = changed
            with self.subTest(mismatch=key), self.assertRaises(WinOddsFreshnessError):
                validate_win_odds_freshness(broken, RECEIVED)

    def test_jra_evidence_rechecks_at_publication(self):
        _, payload = parse_jra_win_odds(html(), RACE, DATE, RECEIVED)
        evidence = validate_win_odds_freshness(payload, RECEIVED)
        forwarded = {"sourceProvider": evidence["sourceProvider"],
                     "data": {"official_datetime": evidence["sourceUpdatedAtJST"]}}
        for field in ("sourceDisplayLabel", "sourceRaceDateJST", "sourceRaceID",
                      "sourceTimePrecision", "sourcePageSha256", "sourceURL"):
            forwarded[field] = evidence[field]
        later = validate_win_odds_freshness(forwarded, RECEIVED + timedelta(minutes=28))
        self.assertEqual(later["sourceAgeSecondsAtReceipt"], 1800)
        with self.assertRaisesRegex(WinOddsFreshnessError, "stale_over_30m"):
            validate_win_odds_freshness(forwarded, RECEIVED + timedelta(minutes=28, seconds=1))

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

    def test_navigation_discovery_stops_at_first_restricted_page(self):
        class Response:
            content = b"blocked"
            def __init__(self, status):
                self.status_code = status

        for status in (401, 403, 429, 451, 302):
            with self.subTest(status=status):
                class Session:
                    def __init__(self):
                        self.calls = []
                    def get(self, url, **kwargs):
                        self.calls.append((url, kwargs))
                        return Response(status)

                session = Session()
                with self.assertRaises(JRAEntryAccessRestricted) as caught:
                    discover_entry_url(RACE, DATE, session, abort_on_restricted=True)
                self.assertFalse(retryable_win_odds_failure(caught.exception))
                self.assertEqual(len(session.calls), 1)
                self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_cache_prevents_repeat_jra_entry_crawl_for_one_race(self):
        class Response:
            status_code = 200
            apparent_encoding = "shift_jis"
            def __init__(self, content):
                self.content = content.encode("shift_jis")

        entry = '<a onclick="doAction(\x27/JRADB/accessO.html\x27,\x27pw15oli00/6D\x27)">オッズ</a>'
        class Session:
            def __init__(self):
                self.headers = {}
                self.get_calls = 0
                self.post_calls = 0
            def get(self, url, **kwargs):
                self.get_calls += 1
                return Response(entry)
            def post(self, url, **kwargs):
                self.post_calls += 1
                return Response(html())

        session, cache = Session(), {}
        with patch("jra_win_odds_source.discover_entry_url",
                   return_value="https://www.jra.go.jp/JRADB/accessD.html?CNAME=observed") as discover:
            first, _ = fetch_jra_win_odds(
                RACE, DATE, session=session, clock=lambda: RECEIVED,
                navigation_cache=cache)
            second, _ = fetch_jra_win_odds(
                RACE, DATE, session=session, clock=lambda: RECEIVED,
                navigation_cache=cache)
        discover.assert_called_once()
        self.assertEqual(first.to_dict("records"), second.to_dict("records"))
        self.assertEqual(session.get_calls, 1)
        self.assertEqual(session.post_calls, 2)
        self.assertEqual(list(cache), [(DATE, RACE)])

    def test_access_denial_and_redirect_stop_without_retry(self):
        class Response:
            apparent_encoding = "shift_jis"
            def __init__(self, status):
                self.status_code = status
                self.content = b"blocked"

        entry_url = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=observed"
        for stage in ("entry", "odds"):
            for status in (401, 403, 429, 451, 302):
                with self.subTest(stage=stage, status=status):
                    class Session:
                        def __init__(self):
                            self.headers = {}
                            self.calls = []
                        def get(self, url, **kwargs):
                            self.calls.append("get")
                            if stage == "entry":
                                return Response(status)
                            return Response(200)
                        def post(self, url, **kwargs):
                            self.calls.append("post")
                            return Response(status)
                    session = Session()
                    with patch("jra_win_odds_source.discover_entry_url",
                               return_value=entry_url):
                        if stage == "odds":
                            cache = {(DATE, RACE): (entry_url, TOKEN)}
                        else:
                            cache = {}
                        with self.assertRaises(JRAAccessRestricted) as caught:
                            fetch_jra_win_odds(
                                RACE, DATE, session=session,
                                clock=lambda: RECEIVED, navigation_cache=cache)
                    self.assertFalse(retryable_win_odds_failure(caught.exception))
                    self.assertEqual(session.calls, ["get"] if stage == "entry" else ["post"])

    def test_http_200_official_access_block_is_terminal(self):
        class Response:
            status_code = 200
            apparent_encoding = "shift_jis"
            content = "アクセスが集中しております".encode("shift_jis")

        class Session:
            def __init__(self):
                self.headers = {}
            def post(self, *args, **kwargs):
                return Response()

        cache = {(DATE, RACE): (
            "https://www.jra.go.jp/JRADB/accessD.html?CNAME=observed", TOKEN)}
        with self.assertRaises(JRAAccessRestricted) as caught:
            fetch_jra_win_odds(
                RACE, DATE, session=Session(),
                clock=lambda: RECEIVED, navigation_cache=cache)
        self.assertFalse(retryable_win_odds_failure(caught.exception))

    def test_http_200_navigation_access_block_is_terminal(self):
        class Response:
            status_code = 200
            apparent_encoding = "shift_jis"
            content = "ご指定のページに接続できません".encode("shift_jis")

        class Session:
            def __init__(self):
                self.calls = 0
            def get(self, url, **kwargs):
                self.calls += 1
                return Response()

        session = Session()
        with self.assertRaises(JRAEntryAccessRestricted) as caught:
            discover_entry_url(RACE, DATE, session, abort_on_restricted=True)
        self.assertFalse(retryable_win_odds_failure(caught.exception))
        self.assertEqual(session.calls, 1)

    def test_other_upstream_http_error_retries_without_repeat_discovery(self):
        class Response:
            apparent_encoding = "shift_jis"
            status_code = 503
        class Session:
            def __init__(self):
                self.headers = {}
            def post(self, url, **kwargs):
                return Response()

        cache = {(DATE, RACE): (
            "https://www.jra.go.jp/JRADB/accessD.html?CNAME=observed", TOKEN)}
        with self.assertRaises(JRAWinOddsError) as caught:
            fetch_jra_win_odds(RACE, DATE, session=Session(),
                               navigation_cache=cache)
        self.assertTrue(retryable_win_odds_failure(caught.exception))


if __name__ == "__main__":
    unittest.main()
