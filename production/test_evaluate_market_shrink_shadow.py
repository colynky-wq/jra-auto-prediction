"""No-network prospective verification of frozen market-shrink samples."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from evaluate_market_shrink_shadow import evaluate
from market_shrink_shadow import JST, record_best_effort, shadow_path
from test_market_shrink_shadow import fixture


def audit_for(race):
    return {
        "projectionState": "verified",
        "generatedAtJST": "2026-11-23T20:00:00+09:00",
        "aligned": {"countsAreCurrent": True, "racesObserved": 1},
        "quality": {"issues": [], "resultExclusions": []},
        "races": [{
            "raceDate": race["raceDate"], "raceID": race["raceID"],
            "resultState": "matched", "inputFingerprint": "frozen-bundle",
            "actualFinish": {"1": 2, "2": 1, "3": 3},
        }],
    }


class MarketShrinkForwardEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.race = fixture()
        self.original = {"fingerprint": "frozen-bundle", "prediction": self.race}
        self.now = lambda: datetime(2026, 11, 22, 11, 50, 10, tzinfo=JST)

    def write(self, temp):
        rec = record_best_effort(temp, self.race, first_formal_capture=True,
                                 clock=self.now)
        self.assertEqual(rec["status"], "prospective_frozen")

    def test_paired_future_prediction_scores_exact_winner_not_reversed(self):
        with TemporaryDirectory() as temp:
            self.write(temp)
            out = evaluate(Path(temp), audit_for(self.race),
                           load_bundle=lambda *_: self.original)
            self.assertEqual(out["counts"]["pairedAuditedRaces"], 1)
            self.assertEqual(out["byEvent"]["win"]["pairedRaces"], 1)
            self.assertEqual(out["status"], "insufficient_prospective_evidence")
            win = out["byEvent"]["win"]["brierByModel"]
            # Race winner is horse #2. Market: [0.4, .35, .25].
            expected = ((0.4 - 0)**2 + (0.35 - 1)**2 +
                        (0.25 - 0)**2) / 3
            self.assertAlmostEqual(win["marketOnly"], expected, places=7)
            self.assertEqual(out["counts"]["raceEventsUnavailable"], 0)
            self.assertFalse(out["autoBetEnabled"])

    def test_no_shadow_is_never_backfilled_as_prospective(self):
        with TemporaryDirectory() as temp:
            out = evaluate(Path(temp), audit_for(self.race),
                           load_bundle=lambda *_: self.original)
            self.assertEqual(out["counts"]["frozenSnapshots"], 0)
            self.assertEqual(out["counts"]["pairedAuditedRaces"], 0)
            self.assertIsNone(out["byEvent"]["win"]["brierByModel"]["marketOnly"])
            self.assertFalse(shadow_path(temp, self.race).exists())

    def test_wrong_source_and_tamper_fail_closed(self):
        with TemporaryDirectory() as temp:
            self.write(temp)
            bad = deepcopy(self.original)
            bad["prediction"] = deepcopy(self.race)
            bad["prediction"]["horses"][0]["aiProbability"] += 1
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                evaluate(Path(temp), audit_for(self.race), load_bundle=lambda *_: bad)
            path = shadow_path(temp, self.race)
            obj = json.loads(path.read_text())
            obj["horses"][0]["probabilitiesPct"]["win"]["marketBlend25"] = 1
            path.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                evaluate(Path(temp), audit_for(self.race),
                         load_bundle=lambda *_: self.original)

    def test_nonsettled_not_a_false_loss_and_failed_audit_not_used(self):
        with TemporaryDirectory() as temp:
            self.write(temp)
            audit = audit_for(self.race)
            audit["races"][0]["resultState"] = "awaiting_result"
            out = evaluate(Path(temp), audit, load_bundle=lambda *_: self.original)
            self.assertEqual(out["counts"]["notAuditedOrNotSettled"], 1)
            self.assertEqual(out["counts"]["pairedAuditedRaces"], 0)
            audit["projectionState"] = "failed"
            with self.assertRaisesRegex(ValueError, "audit not verified"):
                evaluate(Path(temp), audit, load_bundle=lambda *_: self.original)

    def test_unknown_third_only_omits_top3(self):
        with TemporaryDirectory() as temp:
            self.write(temp)
            audit = audit_for(self.race)
            audit["races"][0]["actualFinish"]["3"] = None
            out = evaluate(Path(temp), audit, load_bundle=lambda *_: self.original)
            self.assertEqual(out["byEvent"]["win"]["pairedRaces"], 1)
            self.assertEqual(out["byEvent"]["top2"]["pairedRaces"], 1)
            self.assertEqual(out["byEvent"]["top3"]["pairedRaces"], 0)


if __name__ == "__main__":
    unittest.main()
