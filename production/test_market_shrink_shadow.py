"""Offline tests for first-capture market-shrink research shadow."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from market_shrink_shadow import JST, build, record_best_effort, shadow_path


def fixture():
    return {
        "raceDate": "2026-11-22", "raceID": "202605050101",
        "startTime": "12:00", "predictionTiming": "live_10min",
        "captureQuality": "formal_10min",
        "capturedAtJST": "2026-11-22T11:50:00+09:00",
        "horses": [
            {"number": n, "aiProbability": a, "marketProbability": m,
             "aiTop2Probability": t2, "marketTop2Probability": u2,
             "aiTop3Probability": 100, "marketTop3Probability": 100}
            for n, a, m, t2, u2 in [
                (1, 60, 40, 90, 80),
                (2, 30, 35, 70, 75),
                (3, 10, 25, 40, 45),
            ]
        ],
    }


class MarketShrinkShadowTests(unittest.TestCase):
    def setUp(self):
        self.race = fixture()
        self.before = lambda: datetime(2026, 11, 22, 11, 50, 10, tzinfo=JST)

    def test_fixed_weights_and_original_untouched(self):
        original = deepcopy(self.race)
        out = build(self.race, clock=self.before)
        self.assertEqual(self.race, original)
        self.assertEqual(out["status"], "prospective_shadow_only")
        self.assertFalse(out["autoBetEnabled"])
        self.assertFalse(out["formalRecommendationsEnabled"])
        self.assertEqual(out["capturedAtJST"], original["capturedAtJST"])
        win = out["horses"][0]["probabilitiesPct"]["win"]
        self.assertEqual(win, {"marketOnly": 40.0, "marketBlend25": 45.0,
                               "marketBlend50": 50.0, "existingAI": 60.0})
        top2 = out["horses"][0]["probabilitiesPct"]["top2"]
        self.assertEqual(top2["marketBlend25"], 82.5)
        self.assertEqual(out["horses"][0]["probabilitiesPct"]["top3"]["marketBlend25"], 100.0)

    def test_first_capture_is_immutable_and_resume_never_backfills(self):
        with TemporaryDirectory() as temp:
            first = record_best_effort(temp, self.race, first_formal_capture=True,
                                       clock=self.before)
            self.assertEqual(first["status"], "prospective_frozen")
            path = shadow_path(temp, self.race)
            original = path.read_bytes()
            second = record_best_effort(temp, self.race, first_formal_capture=False,
                                        clock=lambda: datetime(2026, 11, 22, 13, 0, tzinfo=JST))
            self.assertEqual(second["status"], "already_frozen")
            self.assertEqual(path.read_bytes(), original)
            with TemporaryDirectory() as empty:
                missed = record_best_effort(empty, self.race, first_formal_capture=False,
                                            clock=self.before)
                self.assertEqual(missed["status"], "missing_first_capture_no_backfill")
                self.assertFalse(shadow_path(empty, self.race).exists())

    def test_rejects_late_or_recovery_or_invalid_inputs(self):
        with TemporaryDirectory() as temp:
            cases = []
            late = deepcopy(self.race)
            late["capturedAtJST"] = "2026-11-22T11:55:00+09:00"
            cases.append(late)
            recovery = deepcopy(self.race)
            recovery["predictionTiming"] = "late_recovery"
            cases.append(recovery)
            missing = deepcopy(self.race)
            missing["horses"][0]["marketProbability"] = None
            cases.append(missing)
            duplicate = deepcopy(self.race)
            duplicate["horses"][1]["number"] = 1
            cases.append(duplicate)
            for race in cases:
                with self.subTest(case=race):
                    result = record_best_effort(temp, race, first_formal_capture=True,
                                                clock=self.before)
                    self.assertEqual(result["status"], "error_no_bet_impact")
            self.assertFalse(shadow_path(temp, self.race).exists())
            result = record_best_effort(
                temp, self.race, first_formal_capture=True,
                clock=lambda: datetime(2026, 11, 22, 11, 52, 1, tzinfo=JST))
            self.assertEqual(result["status"], "error_no_bet_impact")

    def test_immutable_snapshot_detects_tamper_and_input_change(self):
        with TemporaryDirectory() as temp:
            record_best_effort(temp, self.race, first_formal_capture=True, clock=self.before)
            changed = deepcopy(self.race)
            changed["horses"][0]["aiProbability"] = 55
            out = record_best_effort(temp, changed, first_formal_capture=False,
                                     clock=self.before)
            self.assertEqual(out["status"], "error_no_bet_impact")
            path = shadow_path(temp, self.race)
            value = json.loads(path.read_text())
            value["horses"][0]["probabilitiesPct"]["win"]["marketBlend25"] = 99
            path.write_text(json.dumps(value))
            out = record_best_effort(temp, self.race, first_formal_capture=False,
                                     clock=self.before)
            self.assertEqual(out["status"], "error_no_bet_impact")

    def test_incomplete_time_zone_never_creates_shadow(self):
        with TemporaryDirectory() as temp:
            race = deepcopy(self.race)
            race["capturedAtJST"] = "2026-11-22T11:50:00"
            out = record_best_effort(temp, race, first_formal_capture=True, clock=self.before)
            self.assertEqual(out["status"], "error_no_bet_impact")
            self.assertFalse(shadow_path(temp, race).exists())


if __name__ == "__main__":
    unittest.main()
