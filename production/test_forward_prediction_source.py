"""Offline historical-source safety tests. No live racing network."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from forward_prediction_source import load_published_day, SnapshotUnavailable


def records(day):
    return [{"raceID": "202605050501", "raceDate": day,
             "horses": [{"number": 1, "name": "TEST"}]}]


class ForwardSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for args in (["init", "-q"], ["config", "user.name", "test"],
                     ["config", "user.email", "test@example.invalid"]):
            subprocess.run(["git", *args], cwd=self.root, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, day, when):
        (self.root/"RaceData.json").write_text(json.dumps(records(day)), encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "add", "RaceData.json"], cwd=self.root, env=env, check=True)
        subprocess.run(["git", "commit", "-qm", day], cwd=self.root, env=env, check=True)

    def test_same_day_reads_current_published_display(self):
        self.commit("2026-11-22", "2026-11-22T14:00:00+09:00")
        rows, meta = load_published_day(self.root, "20261122")
        self.assertEqual(rows[0]["raceDate"], "2026-11-22")
        self.assertEqual(meta["kind"], "current_committed_display")
        self.assertFalse(meta["formalT10EvidenceVerified"])

    def test_delayed_cron_reads_only_actual_preexisting_git_blob(self):
        self.commit("2026-11-21", "2026-11-21T16:50:00+09:00")
        self.commit("2026-11-22", "2026-11-22T12:00:00+09:00")
        rows, meta = load_published_day(self.root, "20261121")
        self.assertEqual(rows[0]["raceDate"], "2026-11-21")
        self.assertEqual(meta["kind"], "historical_committed_display")
        self.assertRegex(meta["sourceCommitSHA"], r"^[a-f0-9]{40}$")
        self.assertFalse(meta["formalT10EvidenceVerified"])

    def test_preceding_day_must_be_exact_date(self):
        self.commit("2026-11-20", "2026-11-20T16:50:00+09:00")
        self.commit("2026-11-22", "2026-11-22T12:00:00+09:00")
        with self.assertRaises(SnapshotUnavailable):
            load_published_day(self.root, "20261121")

    def test_commits_after_midnight_do_not_create_fake_previous_day(self):
        self.commit("2026-11-21", "2026-11-22T00:01:00+09:00")
        with self.assertRaises(SnapshotUnavailable):
            load_published_day(self.root, "20261121")

    def test_mixed_date_and_duplicate_races_fail_closed(self):
        self.commit("2026-11-21", "2026-11-21T16:50:00+09:00")
        p = self.root/"RaceData.json"
        p.write_text(json.dumps(records("2026-11-21")+records("2026-11-22")))
        with self.assertRaises(ValueError):
            load_published_day(self.root, "20261121")
        p.write_text(json.dumps(records("2026-11-21")+records("2026-11-21")))
        with self.assertRaises(ValueError):
            load_published_day(self.root, "20261121")

    def test_invalid_day_rejected(self):
        for day in ("20261199", "2026112", "2026-11-21"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                load_published_day(self.root, day)


if __name__ == "__main__":
    unittest.main()
