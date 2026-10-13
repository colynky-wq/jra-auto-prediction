"""Synthetic, temporary-file-only tests of the real entry-to-ledger call chain."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import jra_entry_source as source
import pace_prospective_runtime as prospective
import pace_recording_events as events
import prerace_snapshot_runtime as snapshots
from build_pace_recording_status import build
from evaluate_prospective_pace import run
from test_pace_prospective import fixture, NOW, JST

RID = "202605040601"
DAY = "20261017"
URL = "https://www.jra.go.jp/JRADB/accessD.html?CNAME=pw01dde0105202604060120261017%2FAC"


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz is not None else cls.current.replace(tzinfo=None)


def entry_html(age=4, going="良"):
    # Not obtained from a race: distinct test names and a deliberately minimal DOM.
    rows = []
    for n in [1, 2, 3]:
        rows.append(f'<tr><td>{n}</td><td>{n}</td>'
                    f'<td><a>テスト{n}</a> 2.0 ({n}番人気) 480kg (+0) <a>調教師</a> (美浦)</td>'
                    f'<td>牡{age} 57.0kg <a>騎手</a></td></tr>')
    return ('<h1>出馬表2026年10月17日（土曜）4回東京6日 1レース</h1>'
            f'<div class="race_header">発走時刻：9時50分 芝：{going} 1勝クラス '
            'コース：1,200メートル（芝・左）</div><table>' + ''.join(rows) + '</table>')


class Response:
    status_code = 200
    apparent_encoding = "utf-8"

    def __init__(self, html):
        self.content = html.encode()


class Session:
    def __init__(self):
        self.headers = {}
        self.html = entry_html()

    def get(self, url, timeout):
        if url != URL:
            raise AssertionError("unexpected external request in isolated test")
        return Response(self.html)


class RecordingChainTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        Clock.current = NOW
        history, laps, self.observation, model, self.actual = fixture()
        (self.root / "production").mkdir()
        history.to_parquet(self.root / "production/production_history.parquet", index=False)
        self.models = self.root / "research/pace"
        self.models.mkdir(parents=True)
        self.model_path = self.models / "unit.json"
        self.model_path.write_text(json.dumps(model))
        for lap in laps:
            self.write_lap(lap)
        self.folder = self.root / "reports" / DAY / "prerace" / RID
        self.session = Session()
        first_prediction = prospective.record_first_prediction
        self.stack.enter_context(patch.object(source.requests, "Session", return_value=self.session))
        self.stack.enter_context(patch.object(source, "discover_entry_url", return_value=URL))
        self.stack.enter_context(patch.dict("os.environ", {"KEIBAAI_CAPTURE_PRERACE": "true"}))
        for module in [source, prospective, snapshots, events]:
            self.stack.enter_context(patch.object(module, "datetime", Clock))
        for module in [prospective, snapshots, events]:
            self.stack.enter_context(patch.object(module, "ROOT", self.root))
        self.stack.enter_context(patch.object(prospective, "_CACHE", {}))
        self.stack.enter_context(patch.object(prospective, "record_first_prediction", side_effect=lambda path: first_prediction(path, model_path=self.model_path)))
        self.track = self.stack.enter_context(patch("track_observation_runtime.fetch_track_observation", return_value=None))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        events.save_schedule(DAY, [{"race_id": RID, "venue": "東京", "race_no": 1, "start_time": "09:50"}])

    def write_lap(self, lap):
        folder = self.root / "research/laps" / lap["raceDate"].replace("-", "")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "RaceLapData.json").write_text(json.dumps({"races": [lap]}))

    def fetch(self):
        return source.fetch_entry_table(RID, DAY, JST)

    def prediction(self):
        return json.loads((self.folder / "PacePrediction.json").read_text())

    def board(self, final=None):
        if final is None:
            final = pd.DataFrame(columns=["レースID", "日付", "馬番", "障害"])
        return build(self.root / "reports", self.root / "research/laps", final, self.models, Clock.current)

    def test_real_entry_parser_to_first_prediction_to_result_match(self):
        entries, fetched_at, url = self.fetch()
        self.assertEqual((entries["年齢"].tolist(), url), ([4, 4, 4], URL))
        self.assertEqual(fetched_at.to_pydatetime(), NOW)
        prediction = self.prediction()
        snapshot = json.loads((self.folder / prediction["snapshotFile"]).read_text())
        self.assertEqual([r["horseKey"] for r in snapshot["runners"]], [f"テスト{n}_2022" for n in [1, 2, 3]])
        self.assertEqual(prediction["status"], "research_forecast")
        pending = self.board()
        self.assertEqual(pending["counts"]["saved"], 1)
        self.assertEqual(pending["counts"]["awaitingResult"], 1)
        self.assertEqual(pending["counts"]["invalid"], 0)
        frozen = (self.folder / "PacePrediction.json").read_bytes()
        Clock.current = NOW + timedelta(hours=12)
        self.write_lap(self.actual)
        final = pd.DataFrame({"レースID": [RID] * 3, "日付": [self.actual["raceDate"]] * 3, "馬番": [1, 2, 3], "障害": [False] * 3})
        self.assertEqual(self.board(final)["counts"]["matched"], 1)
        evaluated = run(self.root / "reports", self.root / "research/laps", final, self.models)
        self.assertEqual(evaluated["counts"]["evaluatedRaces"], 1)
        self.assertEqual(evaluated["counts"]["invalidRecords"], 0)
        self.assertEqual((self.folder / "PacePrediction.json").read_bytes(), frozen)

    def test_first_debut_stays_held_despite_older_namesake_history(self):
        self.session.html = entry_html(age=2)
        history = pd.read_parquet(self.root / "production/production_history.parquet")
        history["馬キー"] = history["馬名"] + "_2013"
        history.to_parquet(self.root / "production/production_history.parquet", index=False)
        self.fetch()
        prediction = self.prediction()
        self.assertEqual(prediction["status"], "held")
        self.assertEqual(prediction["holdReason"], "insufficient_style_history")
        self.assertEqual(prediction["styleCoverage"], 0)
        self.assertEqual(prediction["inputFeatures"]["fieldSize"], 3)
        self.assertIsNone(prediction["latestSourceDay"])
        self.assertEqual(self.board()["counts"]["held"], 1)
        self.assertEqual(self.board()["counts"]["invalid"], 0)

    def test_changed_observation_does_not_replace_first_prediction(self):
        self.fetch()
        frozen = (self.folder / "PacePrediction.json").read_bytes()
        first = self.prediction()["snapshotFile"]
        Clock.current = NOW + timedelta(minutes=1)
        self.session.html = entry_html(going="稍重")
        self.fetch()
        index = json.loads((self.folder / "index.json").read_text())
        self.assertEqual(len(index["snapshots"]), 2)
        self.assertEqual((self.folder / "PacePrediction.json").read_bytes(), frozen)
        self.assertEqual(self.prediction()["snapshotFile"], first)
        self.assertEqual(self.board()["counts"]["saved"], 1)
        self.assertEqual(self.board()["counts"]["invalid"], 0)

    def test_after_post_capture_is_diagnosed_without_creating_prediction(self):
        Clock.current = NOW + timedelta(minutes=10)
        entries, _, _ = self.fetch()
        self.assertEqual(len(entries), 3)
        self.assertFalse((self.folder / "PacePrediction.json").exists())
        self.assertFalse((self.folder / "index.json").exists())
        diagnostic = [json.loads(p.read_text()) for p in (self.folder / "events").glob("*.json")]
        self.assertEqual([d["stage"] for d in diagnostic], ["capture_failed"])
        self.assertEqual(self.board()["counts"]["failed"], 1)

    def test_optional_track_failure_keeps_entry_and_valid_first_prediction(self):
        self.track.side_effect = OSError("synthetic track timeout")
        entries, _, _ = self.fetch()
        self.assertEqual(len(entries), 3)
        self.assertEqual(self.prediction()["status"], "research_forecast")
        diagnostic = [json.loads(p.read_text()) for p in (self.folder / "events").glob("*.json")]
        self.assertEqual([d["stage"] for d in diagnostic], ["track_unavailable"])
        self.assertEqual(self.board()["counts"]["saved"], 1)


if __name__ == "__main__":
    unittest.main()
