from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
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



class ArchivedResultRouteTests(unittest.TestCase):
    rid = "202608040510"
    date = "20261012"

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / self.date / "prerace" / self.rid
        self.folder.mkdir(parents=True)
        self.fingerprint = "a" * 64
        self.filename = "20261012T150000000000-" + self.fingerprint[:12] + ".json"
        self.snapshot = {
            "schemaVersion": 1, "methodVersion": "official-entry-observation-v2",
            "raceID": self.rid, "raceDate": "2026-10-12",
            "capturedAtJST": "2026-10-12T15:00:00+09:00",
            "scheduledPostJST": "2026-10-12T15:10:00+09:00",
            "sourceURL": url("dde", "10"), "sourceSHA256": "b" * 64,
            "prePostConfirmed": True,
        }
        self.index = {"raceID": self.rid, "snapshots": [
            {"file": self.filename, "fingerprint": self.fingerprint,
             "capturedAtJST": self.snapshot["capturedAtJST"]}]}
        self.write()

    def write(self):
        (self.folder / self.filename).write_text(json.dumps(self.snapshot))
        (self.folder / "index.json").write_text(json.dumps(self.index))

    def test_archived_route_matches_exact_race(self):
        self.assertEqual(results.archived_entry_url(self.root, self.rid, self.date),
                         self.snapshot["sourceURL"])

    def test_wrong_race_date_origin_or_post_time_rejected(self):
        original = dict(self.snapshot)
        for field, value in (
                ("raceID", "202608040511"), ("raceDate", "2026-10-11"),
                ("sourceURL", url("dde", "10", "20261011")),
                ("sourceURL", url("dde", "10").replace("www.jra.go.jp", "example.com")),
                ("sourceURL", url("sde", "10")),
                ("sourceURL", url("dde", "10") + "&CNAME=bad"),
                ("prePostConfirmed", False),
                ("scheduledPostJST", "2026-10-12T14:59:00+09:00"),
                ("sourceSHA256", "bad")):
            with self.subTest(field=field, value=value):
                self.snapshot = {**original, field: value}
                self.write()
                self.assertIsNone(results.archived_entry_url(self.root, self.rid, self.date))

    def test_archive_reuses_route_without_homepage_crawl(self):
        correct = url("sde", "10")
        wrong_origin = correct.replace("www.jra.go.jp", "example.com")
        class Response:
            status_code = 200
            encoding = apparent_encoding = "utf-8"
            content = f'<a href="{wrong_origin}">結果</a><a href="{correct}">結果</a>'.encode()
        class Session:
            def __init__(self): self.calls = []
            def get(self, page_url, **kwargs):
                self.calls.append((page_url, kwargs))
                return Response()
        session = Session()
        with patch.object(results.agg, "REPORTS_DIR", self.root), \
             patch.object(results, "SESSION", session), \
             patch.dict(results._RESULT_URL_CACHE, {}, clear=True), \
             patch.object(entries, "discover_entry_url", side_effect=AssertionError("unexpected crawl")):
            self.assertEqual(results._discover_result_url_for_race(self.rid, self.date), correct)
            self.assertEqual(results._discover_result_url_for_race(self.rid, self.date), correct)
        self.assertEqual(session.calls, [(self.snapshot["sourceURL"],
                                         {"timeout": 30, "allow_redirects": False})])

    def test_archived_denial_not_retried_by_discovery(self):
        class Response:
            status_code = 403
            content = b"denied"
        with patch.object(results.agg, "REPORTS_DIR", self.root), \
             patch.dict(results._RESULT_URL_CACHE, {}, clear=True), \
             patch.object(results.SESSION, "get", return_value=Response()) as get, \
             patch.object(entries, "discover_entry_url", side_effect=AssertionError("unexpected crawl")):
            with self.assertRaisesRegex(RuntimeError, "403"):
                results._discover_result_url_for_race(self.rid, self.date)
            get.assert_called_once()

    def test_real_saved_nov23_routes(self):
        root = Path(__file__).resolve().parent.parent / "reports"
        folder = root / "20261123" / "prerace"
        ids = sorted(p.name for p in folder.iterdir() if p.is_dir())
        self.assertEqual(len(ids), 23)
        # Fukushima 1R has only monitor event files, not an entry snapshot.
        # Never convert the existence of a directory into source evidence.
        accepted = {rid for rid in ids
                    if results.archived_entry_url(root, rid, "20261123") is not None}
        self.assertEqual(len(accepted), 22)
        self.assertEqual(set(ids) - accepted, {"202603030601"})


if __name__ == "__main__":
    unittest.main()
