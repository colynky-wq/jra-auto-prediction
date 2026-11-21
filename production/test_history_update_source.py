"""No network, model inference, learning, or forward-record writes."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import history_update_source as source


def month():
    return [{"month": "10", "data": [
        {"date": str(n), "info": [{"race": [{"name": "4回東京"}]}]}
        for n in [3, 18, 25]]}]


def program(date="2026年10月18日", numbers=(1, 2)):
    rows = "".join(f'<tr><th class="num">{n}<span>レース</span></th><td class="name">結果を別途確認</td></tr>' for n in numbers)
    return f'<h1>{date}（日曜） 競馬番組</h1><div id="program_list"><table><caption>4回東京7日</caption><tbody>{rows}</tbody></table></div>'


def response(content, status=200):
    r = requests.Response(); r.status_code = status; r._content = content
    r.url = "https://www.jra.go.jp/test"; return r


class HistorySourceTests(unittest.TestCase):
    def test_schedule_and_default_manual_date_survive_midnight_retry(self):
        now = datetime.fromisoformat("2026-10-19T01:00:00+09:00")
        for event in ["schedule", "workflow_dispatch"]:
            self.assertEqual(source.target_date("", "2026-10-18T12:38:56Z", event, now), "20261018")

    def test_delayed_scheduled_history_uses_last_completed_evening(self):
        now = datetime.fromisoformat("2026-11-21T05:04:38+09:00")
        self.assertEqual(source.target_date("", "2026-11-20T20:04:38Z", "schedule", now),
                         "20261120")
        self.assertEqual(source.target_date("", "2026-11-21T11:59:59Z", "schedule",
                                             datetime.fromisoformat("2026-11-21T21:05:00+09:00")),
                         "20261120")
        self.assertEqual(source.target_date("", "2026-11-21T12:15:00Z", "schedule",
                                             datetime.fromisoformat("2026-11-21T21:30:00+09:00")),
                         "20261121")
        # Manual runs keep explicit-day semantics (do not silently go backwards).
        self.assertEqual(source.target_date("", "2026-11-20T20:04:38Z", "workflow_dispatch", now),
                         "20261121")

    def test_explicit_recovery_date_wins(self):
        self.assertEqual(source.target_date("20261018", "", "workflow_dispatch",
            datetime.fromisoformat("2026-10-19T01:00:00+09:00")), "20261018")

    def test_unknown_or_naive_scheduled_time_fails_closed(self):
        for value in ["", "2026-10-18T12:38:56", "2026-10-20T12:38:56Z"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.target_date("", value, "schedule", datetime.fromisoformat("2026-10-19T01:00:00+09:00"))

    def test_local_default_uses_jst(self):
        self.assertEqual(source.target_date("", "", "", datetime.fromisoformat("2026-10-18T16:00:00Z")), "20261019")

    def test_bad_explicit_date_rejected(self):
        for value in ["2026-10-18", "20260230", "2026101"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.target_date(value, "", "", datetime.now(source.JST))

    def test_month_confirms_meeting_and_omitted_day(self):
        self.assertEqual(source.calendar_meetings(month(), "20261018"), ["4回東京"])
        self.assertEqual(source.calendar_meetings(month(), "20261019"), [])

    def test_calendar_missing_corrupt_partial_or_wrong_month_is_not_no_meeting(self):
        cases = [[], [{"month": "9", "data": month()[0]["data"]}],
                 [{"month": "10", "data": []}],
                 [{"month": "10", "data": month()[0]["data"][:2]}],
                 [{"month": "10", "data": [month()[0]["data"][0]] * 3}]]
        bad = deepcopy(month()); bad[0]["data"][1]["info"][0]["race"] = None; cases.append(bad)
        for value in cases:
            with self.subTest(value=value), self.assertRaises((ValueError, KeyError, TypeError)):
                source.calendar_meetings(value, "20261019")

    def test_race_ids_use_only_published_rows_not_assumed_twelve(self):
        self.assertEqual(source.program_race_ids(program(), "20261018", ["4回東京"]),
                         ["202605040701", "202605040702"])

    def test_wrong_date_missing_venue_duplicate_or_partial_rows_fail(self):
        for html, meetings in [(program("2026年10月17日"), ["4回東京"]),
                               (program(), ["4回東京", "4回京都"]),
                               (program(numbers=(1, 1)), ["4回東京"]),
                               (program(numbers=(1, 3)), ["4回東京"]),
                               (program(numbers=()), ["4回東京"]),
                               ("<h1>2026年10月18日競馬番組</h1>", ["4回東京"])]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                source.program_race_ids(html, "20261018", meetings)

    @patch.object(source.time, "sleep")
    def test_400_then_timeout_then_success_retries_and_records_evidence(self, sleep):
        session = Mock(); session.get.side_effect = [response(b"", 400), requests.Timeout("timed out"), response(b"ok")]
        evidence = []
        self.assertEqual(source.fetch(session, "https://www.jra.go.jp/test", evidence).content, b"ok")
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual([e["httpStatus"] for e in evidence], [400, 200])
        self.assertEqual(evidence[-1]["attempt"], 3)

    @patch.object(source.time, "sleep")
    def test_repeated_failure_is_not_an_empty_race_list(self, sleep):
        session = Mock(); session.get.return_value = response(b"", 400)
        with self.assertRaises(RuntimeError): source.fetch_day(session, "20261018")
        self.assertEqual(session.get.call_count, 3)

    @patch.object(source.time, "sleep")
    def test_http_200_empty_body_is_not_no_meeting(self, sleep):
        session = Mock(); session.get.return_value = response(b"")
        with self.assertRaises(RuntimeError): source.fetch_day(session, "20261018")

    def test_official_two_source_success_and_no_meeting_evidence(self):
        session = Mock(); session.get.side_effect = [response(json.dumps(month()).encode()), response(program().encode("cp932"))]
        ids, evidence = source.fetch_day(session, "20261018")
        self.assertEqual(len(ids), 2); self.assertEqual(len(evidence["requests"]), 2)
        session.get.side_effect = [response(json.dumps(month()).encode())]
        ids, evidence = source.fetch_day(session, "20261019")
        self.assertEqual(ids, []); self.assertEqual(evidence["status"], "no_scheduled_meetings")

    def test_history_backlog_discovers_completed_meetings_in_order(self):
        session = Mock()
        october = month()
        november = [{"month": "11", "data": [
            {"date": str(n), "info": [{"race": [{"name": "4回東京"}]}]}
            for n in [7, 8, 22, 29]]}]
        session.get.side_effect = [
            response(json.dumps(october).encode()),
            response(json.dumps(november).encode()),
        ]
        days = source.completed_meeting_dates(session, "20261018", "20261108")
        self.assertEqual(days, ["20261025", "20261107", "20261108"])
        self.assertEqual(session.get.call_count, 2)
        session.get.assert_any_call(
            "https://www.jra.go.jp/keiba/common/calendar/json/202611.json", timeout=30
        )

    def test_history_backlog_empty_and_excessive_gap_are_safe(self):
        session = Mock()
        self.assertEqual(source.completed_meeting_dates(session, "20261108", "20261108"), [])
        self.assertEqual(source.completed_meeting_dates(session, "20261108", "20261107"), [])
        session.get.assert_not_called()
        with self.assertRaises(ValueError):
            source.completed_meeting_dates(session, "20260101", "20261108")

    def test_push_backfill_never_targets_unfinished_current_day(self):
        early = datetime.fromisoformat("2026-11-22T02:19:00+09:00")
        late = datetime.fromisoformat("2026-11-22T21:30:00+09:00")
        self.assertEqual(source.target_date("", "", "push", early), "20261121")
        self.assertEqual(source.target_date("", "", "push", late), "20261122")
        # A direct manual dispatch retains its previously agreed semantics.
        self.assertEqual(source.target_date("", "2026-11-21T17:19:00Z",
                                            "workflow_dispatch", early), "20261122")

    def test_verified_saved_jra_route_preserves_exact_race_identity(self):
        import update_production_history as updater
        date8 = "20261107"
        race_id = "202603030101"
        correct = ("https://www.jra.go.jp/JRADB/accessS.html?CNAME="
                   "pw01sde0103202603010120261107/A9")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports" / date8 / "ResultData.json"
            path.parent.mkdir(parents=True)
            record = {"raceID": race_id, "raceDate": "2026-11-07",
                      "raceLap": {"sourceURL": correct}}
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(updater, "REPO", root):
                self.assertEqual(updater.verified_result_url_from_report(race_id, date8), correct)
                self.assertEqual(updater.resolve_result_url(race_id, date8), correct)
                self.assertIsNone(updater.verified_result_url_from_report("202603030102", date8))
                for tampered in [
                    correct.replace("www.jra.go.jp", "example.org"),
                    correct.replace("20261107", "20261108"),
                    correct.replace("030101", "030102"),
                    correct.replace("/A9", "/"),
                ]:
                    record["raceLap"]["sourceURL"] = tampered
                    path.write_text(json.dumps([record]), encoding="utf-8")
                    with self.subTest(tampered=tampered), self.assertRaises(ValueError):
                        updater.verified_result_url_from_report(race_id, date8)
                record["raceLap"]["sourceURL"] = correct
                record["raceDate"] = "2026-11-08"
                path.write_text(json.dumps([record]), encoding="utf-8")
                with self.assertRaises(ValueError):
                    updater.verified_result_url_from_report(race_id, date8)

    def test_saved_monitoring_schedule_blocks_false_no_meeting_without_history_write(self):
        import update_production_history as updater
        import pandas as pd
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); history = root / "history.parquet"
            d = pd.DataFrame([{col: 1 for col in updater.BASE_COLS}])
            d["日付"] = pd.to_datetime(["2026-10-17"]); d["レースID"] = "202605040601"
            d.to_parquet(history); before = history.read_bytes()
            schedule = root / "reports/20261018/PaceRecordingSchedule.json"
            schedule.parent.mkdir(parents=True); schedule.write_text(json.dumps({"races": [{"raceID": "202605040701"}]}))
            with patch.multiple(updater, HISTORY_PATH=history, REPO=root, TARGET_DATE8="20261018",
                                NOW_JST=datetime.fromisoformat("2026-10-19T01:00:00+09:00")), \
                 patch.object(updater, "fetch_day_race_ids", return_value=[]), \
                 self.assertRaises(SystemExit):
                updater.main()
            self.assertEqual(history.read_bytes(), before)


if __name__ == "__main__": unittest.main()
