"""Test the research archive without writing a live record."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from jra_wide_odds_source import parse_wide_page
from jra_wide_research_archive import path_for, read_record, record_first, verify_snapshot
from research_longshot_wide_odds_replay import run
from test_jra_wide_odds_source import DATE, RACE, RECEIPT, URL, page


def snapshot(label="9時59分現在オッズ", **options):
    parsed = parse_wide_page(page(label), RACE, DATE, [1, 3, 5], RECEIPT, **options)
    parsed.update(sourceEntryURL=URL, sourceEntryPageSha256="f" * 64)
    return parsed


class ResearchArchiveTests(unittest.TestCase):
    def test_immutable_capture_and_integrity(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            first = record_first(archive, snapshot(), clock=lambda: RECEIPT)
            path = path_for(archive, DATE, RACE)
            raw = path.read_bytes()
            repeat = record_first(archive, snapshot("10時01分現在オッズ"),
                                  clock=lambda: RECEIPT + timedelta(minutes=1))
            self.assertEqual(repeat, first)
            self.assertEqual(path.read_bytes(), raw)
            first["snapshot"]["pairs"][0]["oddsHigh"] = 9.9
            path.write_text(json.dumps(first), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                read_record(path, DATE, RACE)

    def test_freshness_rechecked_at_decision_and_commit(self):
        capture = snapshot("9時35分現在オッズ")
        self.assertEqual(verify_snapshot(capture, DATE, RACE, RECEIPT), 1800)
        with self.assertRaisesRegex(ValueError, "at decision"):
            verify_snapshot(capture, DATE, RACE, RECEIPT + timedelta(microseconds=1))
        with TemporaryDirectory() as tmp:
            times = iter([RECEIPT, RECEIPT + timedelta(microseconds=1)])
            with self.assertRaisesRegex(ValueError, "missed"):
                record_first(tmp, capture, clock=lambda: next(times))
            self.assertFalse(path_for(tmp, DATE, RACE).exists())

    def test_final_and_wrong_roster_are_rejected(self):
        with TemporaryDirectory() as tmp:
            finished = snapshot("最終オッズ", inspect_final=True)
            with self.assertRaisesRegex(ValueError, "interim"):
                record_first(tmp, finished, clock=lambda: RECEIPT)
            incomplete = deepcopy(snapshot())
            incomplete["pairs"].pop()
            with self.assertRaisesRegex(ValueError, "pair roster"):
                record_first(tmp, incomplete, clock=lambda: RECEIPT)
            self.assertFalse(path_for(tmp, DATE, RACE).exists())

    def test_replay_keeps_unmatched_official_record_without_legacy_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "official"
            record_first(archive, snapshot(), clock=lambda: RECEIPT)
            prior = root / "reports" / DATE / "portfolio_live10" / f"{RACE}.json"
            prior.parent.mkdir(parents=True)
            prior.write_text("{}", encoding="utf-8")
            rank = Mock()
            rank.model_to_string.return_value = "offline ranking model"
            evaluated = run(root, root / "evaluation", {}, rank, {},
                            RECEIPT + timedelta(hours=2), jra_archive=archive)
            self.assertEqual(evaluated["snapshotInventory"]["count"], 1)
            self.assertEqual(evaluated["boundReplay"]["states"], {"awaitingPrediction": 1})
            self.assertEqual(evaluated["boundReplay"]["freshSummary"], [])


if __name__ == "__main__":
    unittest.main()
