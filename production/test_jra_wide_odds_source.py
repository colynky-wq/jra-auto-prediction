"""Offline cases matching the observed JRA race and Wide page structure."""

from datetime import datetime, timedelta
from itertools import combinations
import unittest

from jra_wide_odds_source import (
    ACCESS_O, JRAAccessRestricted, JRAWideOddsError, JRAWinOddsError,
    fetch_jra_wide_odds, parse_wide_page,
)
from win_odds_freshness import WinOddsFreshnessError


RACE = "202603030602"
DATE = "20261123"
LABEL = "2026年11月23日（月曜）3回福島6日 2レース"
URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0103202603060220261123/A1"
RECEIPT = datetime.fromisoformat("2026-11-23T10:05:00+09:00")


def header(title, label=LABEL):
    return f'<h1></h1><h1>{title}<span class="opt">{label}</span></h1>'


def action(token, name):
    return f"""<a onclick="return doAction('/JRADB/accessO.html','{token}');">{name}</a>"""


def page(update="9時59分現在オッズ", label=LABEL, pairs=None):
    if pairs is None:
        pairs = [(a, b, "3.2", "4.8") for a, b in combinations([1, 3, 5], 2)]
    tables = []
    for a in sorted({row[0] for row in pairs}):
        rows = "".join(
            f'<tr><th scope="row">{b}</th><td class="odds"><span class="min">{lo}</span>'
            f'<span class="cap">-</span><span class="max">{hi}</span></td></tr>'
            for first, b, lo, hi in pairs if first == a
        )
        tables.append(f'<table class="basic narrow-xy wide"><caption>{a}</caption><tbody>{rows}</tbody></table>')
    return (header("ワイドオッズ（馬番順）", label) +
            '<div class="date_line"><div class="cell time">発走時刻：<strong>10時15分</strong></div></div>'
            f'<div class="refresh_line"><div class="cell time"><strong>{update}</strong></div></div>'
            + "".join(tables))


class WideSourceTests(unittest.TestCase):
    def parse(self, html, receipt=RECEIPT, **kwargs):
        return parse_wide_page(html, RACE, DATE, [1, 3, 5], receipt, **kwargs)

    def test_source_clock_is_separate_from_post_time(self):
        parsed = self.parse(page())
        self.assertEqual(parsed["status"], "verified_interim")
        self.assertEqual(parsed["pairCount"], 3)
        self.assertEqual(parsed["horseNumbers"], [1, 3, 5])
        self.assertEqual(parsed["pairs"][1], {"number1": 1, "number2": 5, "oddsLow": 3.2, "oddsHigh": 4.8})
        evidence = parsed["freshnessEvidence"]
        self.assertEqual(evidence["sourceAgeSecondsAtReceipt"], 360)
        self.assertEqual(evidence["sourceUpdatedAtJST"], "2026-11-23T09:59:00+09:00")

    def test_final_is_diagnostic_only_and_missing_time_is_rejected(self):
        with self.assertRaisesRegex(JRAWideOddsError, "interim update"):
            self.parse(page("最終オッズ"))
        diagnostic = self.parse(page("最終オッズ"), inspect_final=True)
        self.assertEqual(diagnostic["status"], "final_inspection")
        self.assertIsNone(diagnostic["freshnessEvidence"])
        with self.assertRaises(JRAWideOddsError):
            self.parse(page(""))

    def test_freshness_and_capture_windows(self):
        self.assertEqual(self.parse(page("9時35分現在オッズ"))["freshnessEvidence"]["sourceAgeSecondsAtReceipt"], 1800)
        with self.assertRaisesRegex(WinOddsFreshnessError, "stale"):
            self.parse(page("9時35分現在オッズ"), RECEIPT + timedelta(seconds=1))
        with self.assertRaisesRegex(WinOddsFreshnessError, "in_future"):
            self.parse(page("10時06分現在オッズ"))
        for outside in (RECEIPT - timedelta(minutes=2, seconds=1), RECEIPT + timedelta(minutes=2, seconds=1)):
            with self.assertRaisesRegex(JRAWideOddsError, "research window"):
                self.parse(page(), outside)

    def test_wrong_race_or_incomplete_field_is_rejected(self):
        with self.assertRaisesRegex(JRAWinOddsError, "identity_mismatch"):
            self.parse(page(label=LABEL.replace("2レース", "3レース")))
        with self.assertRaisesRegex(JRAWideOddsError, "complete active"):
            self.parse(page(pairs=[(1, 3, "3.0", "3.2"), (3, 5, "7.1", "7.5")]))
        with self.assertRaisesRegex(JRAWideOddsError, "duplicate"):
            self.parse(page(pairs=[(1, 3, "3.0", "3.2"), (1, 3, "3.0", "3.2")]))
        with self.assertRaisesRegex(JRAWideOddsError, "reversed"):
            self.parse(page(pairs=[(1, 3, "4.0", "3.2")]))

    def test_navigation_uses_race_specific_buttons_and_observed_wide_token(self):
        entry = header("出馬表") + action("menu-token/00", "オッズ")
        entry += f'<ul><li class="odds">{action("race-token/11", "オッズ")}</li></ul>'
        entry += '<table>' + "".join(
            f'<tr><td class="num">{n}</td><td class="horse">Horse</td></tr>' for n in [1, 3, 5]
        ) + '</table>'
        win = header("単勝・複勝オッズ（馬番順）") + (
            f'<ul class="nav pills"><li>{action("wide-token/21", "ワイド")}</li></ul>'
        )

        class Response:
            apparent_encoding = "utf-8"

            def __init__(self, text, status=200):
                self.content = text.encode("utf-8")
                self.status_code = status

        class Session:
            def __init__(self, status=200):
                self.headers = {}
                self.calls = []
                self.status = status

            def get(self, url, **kw):
                self.calls.append((url, None, kw["allow_redirects"]))
                return Response(entry, self.status)

            def post(self, url, **kw):
                token = kw["data"]["cname"]
                self.calls.append((url, token, kw["allow_redirects"]))
                return Response(win if token == "race-token/11" else page())

        session = Session()
        out = fetch_jra_wide_odds(RACE, DATE, entry_url=URL, session=session, clock=lambda: RECEIPT)
        self.assertEqual(out["pairCount"], 3)
        self.assertEqual(session.calls, [(URL, None, False), (ACCESS_O, "race-token/11", False),
                                         (ACCESS_O, "wide-token/21", False)])
        denied = Session(429)
        with self.assertRaises(JRAAccessRestricted):
            fetch_jra_wide_odds(RACE, DATE, entry_url=URL, session=denied)
        self.assertEqual(len(denied.calls), 1)


if __name__ == "__main__":
    unittest.main()
