from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import jra_entry_source as entries
import run_forward_aggregate as results
import run_forward_aggregate_v2 as enhanced


def url(kind, mode, day="20261012"):
    token = f"pw01{kind}{mode}082026040510{day}/E3"
    path = "accessD" if kind == "dde" else "accessS"
    return f"https://www.jra.go.jp/JRADB/{path}.html?" + urlencode({"CNAME": token})


class RouteModeTests(unittest.TestCase):
    def test_entry_identity_is_the_same_in_both_modes(self):
        for mode in ["01", "10"]:
            self.assertEqual(entries._race_id_from_url(url("dde", mode), "20261012"), "202608040510")
            self.assertIsNone(entries._race_id_from_url(url("dde", mode), "20261011"))

    def test_result_identity_is_the_same_in_both_modes(self):
        for mode in ["01", "10"]:
            self.assertEqual(results._race_id_from_result_url(url("sde", mode), "20261012"), "202608040510")
            self.assertIsNone(results._race_id_from_result_url(url("sde", mode), "20261011"))

    def test_navigation_keeps_both_mode_links(self):
        for kind, extractor in [("dde", entries._extract_accessd_urls), ("sde", results._extract_result_urls)]:
            links = [url(kind, mode) for mode in ["01", "10"]]
            html = "".join(f'<a href="{link}">結果</a>' for link in links)
            found = extractor("https://www.jra.go.jp/JRADB/accessD.html", html)
            self.assertTrue(set(links) <= set(found))

    def test_result_tokens_in_scripts_are_recovered_in_both_modes(self):
        links = [url("sde", mode) for mode in ["01", "10"]]
        from urllib.parse import parse_qs, urlparse
        tokens = [parse_qs(urlparse(link).query)["CNAME"][0] for link in links]
        html = "<script>" + ";".join(f"var x='{token}'" for token in tokens) + "</script>"
        found = enhanced.enhanced_extract_result_urls(links[0], html)
        self.assertTrue(set(links) <= set(found))

    def test_unknown_date_can_start_from_current_homepage_calendar(self):
        target = url("dde", "10")
        class Response:
            status_code = 200
            apparent_encoding = "utf-8"
            def __init__(self, text):
                self.content = text.encode()
        class Session:
            def get(self, page_url, timeout):
                if page_url == entries.JRA_HOST + "/":
                    return Response("<form id='commForm01' method='POST'><input id='cname' name='cname'></form>"
                                    "<a onclick=\"doAction('/JRADB/accessD.html','pw01dli00/F3')\">出馬表</a>")
                return Response("<h1>パラメータエラー</h1>")
            def post(self, page_url, data, headers, timeout):
                if page_url != entries.ACCESS_D or data != {'cname':'pw01dli00/F3'}:
                    raise AssertionError('Public calendar requires its exact form field')
                return Response(f'<a href="{target}">出馬表</a>')
        with patch.dict(entries.KNOWN_DATE_SEEDS, {}, clear=True):
            self.assertEqual(entries.discover_entry_url("202608040510", "20261012", Session()), target)


if __name__ == "__main__":
    unittest.main()
