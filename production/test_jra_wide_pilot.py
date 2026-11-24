"""Dry-run the pilot using immutable historical inputs and a synthetic quote."""

from datetime import datetime, timedelta
import json
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
import unittest

from jra_wide_odds_source import parse_wide_page
from jra_wide_pilot import CAPTURE_NAME, PLAN_KEYS, capture, settle, validate_capture
from live_bundle_publisher import Outbox
from test_jra_wide_odds_source import DATE, RACE, RECEIPT, URL, page
from itertools import combinations


ROOT = Path(__file__).resolve().parent.parent
NOW_SETTLED = datetime.fromisoformat("2026-11-24T12:00:00+09:00")


class JRAWidePilotTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        paths = (
            f"all_bet_v1/win/{RACE}.json", f"longshot_top3_v2/{RACE}.json",
            "ResultData.json", "WideValidation.json",
        )
        self.day = self.root / "reports" / DATE
        for name in paths:
            path = self.day / name
            path.parent.mkdir(parents=True, exist_ok=True)
            copy2(ROOT / "reports" / DATE / name, path)
        self.race = json.loads((self.day / paths[0]).read_text())["prediction"]
        self.snapshot = self.day / CAPTURE_NAME
        self.now = RECEIPT
        self.requests = 0

    def clock(self):
        return self.now

    def quote(self, rid, date, **kwargs):
        self.requests += 1
        self.assertEqual((rid, date, kwargs["entry_url"]), (RACE, DATE, URL))
        self.assertGreater(kwargs["request_timeout"], 0)
        numbers = sorted(h["number"] for h in self.race["horses"])
        # A high synthetic price ensures every rule has enough eligible pairs.
        html = page(pairs=[(a, b, "1000.0", "1100.0") for a, b in combinations(numbers, 2)])
        result = parse_wide_page(html, rid, date, numbers, kwargs["clock"]())
        result.update(sourceEntryURL=URL, sourceEntryPageSha256="a" * 64)
        return result

    def test_full_capture_publisher_and_settlement(self):
        state = capture(self.root, self.race, URL, clock=self.clock, fetch=self.quote)
        self.assertEqual(state, "verifiedAtDecision")
        record = validate_capture(json.loads(self.snapshot.read_text()), "2026-11-23")
        self.assertEqual(set(record["probabilityCards"]), PLAN_KEYS)
        self.assertEqual(set(record["oddsFilteredCards"]), PLAN_KEYS)
        self.assertEqual(record["officialWide"]["pairCount"], 105)
        self.now = NOW_SETTLED
        self.assertEqual(capture(self.root, self.race, URL, clock=self.clock, fetch=self.quote), "alreadyFrozenToday")
        self.assertEqual(self.requests, 1)
        outbox = object.__new__(Outbox)
        outbox.date8, outbox.day = DATE, "2026-11-23"
        raw = self.snapshot.read_bytes()
        self.assertEqual(outbox.bytes_for_remote(f"reports/{DATE}/{CAPTURE_NAME}", raw, None), raw)
        self.assertEqual(outbox.bytes_for_remote(f"reports/{DATE}/{CAPTURE_NAME}", raw+b"\n", raw), raw)
        evaluation = settle(self.root, DATE, self.now)
        self.assertEqual(evaluation["state"], "settled")
        self.assertEqual(evaluation["captureFingerprint"], record["fingerprint"])
        self.assertEqual(len(evaluation["groups"]["oddsFilteredCards"]), 4)
        self.assertTrue(all(value["stakeYen"] == 300 for value in evaluation["groups"]["probabilityCards"].values()))
        self.assertEqual(raw, self.snapshot.read_bytes())

    def test_network_failure_preserves_probability_plans_without_prices(self):
        def fail(*args, **kwargs):
            raise TimeoutError

        self.assertEqual(capture(self.root, self.race, URL, clock=self.clock, fetch=fail), "fetchFailed")
        result = settle(self.root, DATE, NOW_SETTLED)
        self.assertEqual(set(result["groups"]), {"probabilityCards"})

    def test_late_response_cannot_write_a_plan(self):
        def late(*args, **kwargs):
            self.now += timedelta(minutes=3)
            return self.quote_with_original_clock(*args, **kwargs)

        self.assertEqual(capture(self.root, self.race, URL, clock=self.clock, fetch=late), "deadlineMissed")
        self.assertFalse(self.snapshot.exists())
        self.assertIsNone(settle(self.root, DATE, NOW_SETTLED))

    def quote_with_original_clock(self, *args, **kwargs):
        return self.quote(*args, **{**kwargs, "clock": lambda: RECEIPT})

    def test_missing_payout_kept_pending(self):
        capture(self.root, self.race, URL, clock=self.clock, fetch=self.quote)
        (self.day / "WideValidation.json").unlink()
        self.assertEqual(settle(self.root, DATE, NOW_SETTLED), {"state": "awaitingPayout"})


if __name__ == "__main__":
    unittest.main()
