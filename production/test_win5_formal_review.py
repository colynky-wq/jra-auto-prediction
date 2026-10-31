import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import win5_formal_review as review


READY_RECORD = {
    "schemaVersion": 1,
    "version": review.SOURCE_VERSION,
    "status": "ready",
    "recordFingerprint": "frozen-record-1",
    "recordedAtJST": "2026-10-18T12:15:00+09:00",
    "plan": {
        "raceDate": "2026-10-18",
        "deadlineJST": "2026-10-18T14:40:00+09:00",
    },
}

MATCHED_SCORE = {
    "status": "probability_matched",
    "winningOutcome": [1, 2, 3, 4, 5],
    "aiActualOutcomeProbability": 0.000001,
    "marketActualOutcomeProbability": 0.0000008,
    "aiNegativeLogProbability": 13.8155105579,
    "marketNegativeLogProbability": 14.0386541093,
    "aiTopOneAllFiveHit": False,
    "marketTopOneAllFiveHit": False,
    "aiTopOneLegHits": 3,
    "marketTopOneLegHits": 2,
}


class TestWin5FormalReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_journal(self, date8="20261018", *, has_target=True, status="ready"):
        folder = self.root / "reports" / date8
        folder.mkdir(parents=True, exist_ok=True)
        day = f"{date8[:4]}-{date8[4:6]}-{date8[6:]}"
        value = {
            "version": review.SOURCE_VERSION,
            "raceDate": day,
            "hasTarget": has_target,
            "status": status,
        }
        (folder / "win5_status.json").write_text(json.dumps(value), encoding="utf-8")
        return folder

    def write_session(self, folder, value=None):
        path = folder / "win5_v1" / "session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value or READY_RECORD), encoding="utf-8")
        return path

    def write_results(self, folder):
        path = folder / "ResultData.json"
        path.write_text("[]", encoding="utf-8")
        return path

    @patch.object(review.win5, "validate", return_value=READY_RECORD)
    @patch.object(review.win5, "load_rows", return_value=[])
    @patch.object(review.win5, "index_unique", return_value={})
    @patch.object(review.win5, "probability_score", return_value=MATCHED_SCORE)
    def test_one_five_leg_session_counts_as_one_observation(self, _score, _index, _rows, _validate):
        folder = self.write_journal()
        self.write_session(folder)
        self.write_results(folder)

        built = review.build(self.root, now=datetime.fromisoformat("2026-10-18T18:00:00+09:00"))

        self.assertEqual(built["observationUnit"], "one_frozen_five_race_win5_session")
        self.assertEqual(built["sessionsObserved"], 1)
        self.assertEqual(built["sessionsEligible"], 1)
        self.assertEqual(built["sessionsResultMatched"], 1)
        self.assertEqual(built["probabilityEvaluation"]["sessionsEvaluated"], 1)
        self.assertEqual(built["probabilityEvaluation"]["totalLegsEvaluated"], 5)
        self.assertEqual(built["probabilityEvaluation"]["aiTopOneLegHits"], 3)
        self.assertEqual(built["probabilityEvaluation"]["marketTopOneLegHits"], 2)

    def test_prestart_session_is_not_in_formal_cohort(self):
        folder = self.write_journal("20261017")
        self.write_session(folder)

        built = review.build(self.root)

        self.assertEqual(built["prospectiveStartDate"], "2026-10-18")
        self.assertEqual(built["sessionsObserved"], 0)
        self.assertEqual(built["sessionsEligible"], 0)

    def test_missing_frozen_record_is_explicitly_excluded(self):
        self.write_journal()

        built = review.build(self.root)

        self.assertEqual(built["sessionsObserved"], 1)
        self.assertEqual(built["sessionsEligible"], 0)
        self.assertEqual(built["sessionsExcluded"], 1)
        self.assertEqual(built["sessions"][0]["status"], "missing_frozen_session")

    @patch.object(review.win5, "validate", return_value={**READY_RECORD, "status": "held", "reason": "source_unavailable"})
    def test_held_record_never_qualifies(self, _validate):
        folder = self.write_journal()
        self.write_session(folder)

        built = review.build(self.root)

        self.assertEqual(built["sessionsEligible"], 0)
        self.assertEqual(built["sessionsExcluded"], 1)
        self.assertEqual(built["sessions"][0]["status"], "held_frozen_session")

    @patch.object(review.win5, "validate", return_value=READY_RECORD)
    def test_missing_results_are_pending_not_failure(self, _validate):
        folder = self.write_journal()
        self.write_session(folder)

        built = review.build(self.root)

        self.assertEqual(built["sessionsEligible"], 1)
        self.assertEqual(built["sessionsResultMatched"], 0)
        self.assertEqual(built["sessionsPending"], 1)
        self.assertEqual(built["sessions"][0]["status"], "results_pending")

    @patch.object(review.win5, "validate", return_value=READY_RECORD)
    @patch.object(review.win5, "load_rows", return_value=[])
    @patch.object(review.win5, "index_unique", return_value={})
    @patch.object(review.win5, "probability_score", return_value=MATCHED_SCORE)
    def test_missing_payout_is_pending_never_zero_return(self, _score, _index, _rows, _validate):
        folder = self.write_journal()
        self.write_session(folder)
        self.write_results(folder)

        built = review.build(self.root)
        row = built["sessions"][0]

        self.assertEqual(row["status"], "payout_pending")
        self.assertFalse(row["payoutVerified"])
        self.assertNotIn("returnYen", row)
        self.assertNotIn("profitYen", row)
        self.assertNotIn("returnRatePct", row)

    @patch.object(review.win5, "validate", return_value=READY_RECORD)
    @patch.object(review.win5, "load_rows", return_value=[])
    @patch.object(review.win5, "index_unique", return_value={})
    @patch.object(review.win5, "probability_score", return_value=MATCHED_SCORE)
    def test_payout_tied_to_wrong_record_fingerprint_is_rejected(self, _score, _index, _rows, _validate):
        folder = self.write_journal()
        self.write_session(folder)
        self.write_results(folder)
        payout_dir = folder / "win5_payouts_v1"
        payout_dir.mkdir(parents=True)
        unsigned = {
            "recordFingerprint": "different-record",
            "sourceURL": "https://example.invalid/win5",
            "sourceFingerprint": "x" * 64,
            "retrievedAtJST": "2026-10-18T16:00:00+09:00",
            "payout": {
                "raceDate": "2026-10-18",
                "raceIDs": ["1", "2", "3", "4", "5"],
                "winners": [],
                "winningTicketCount": 1,
                "payoutPer100Yen": 100,
                "payoutStatus": "paid",
            },
        }
        wrapper = {**unsigned, "fingerprint": review.payouts.digest(unsigned)}
        (payout_dir / "session.json").write_text(json.dumps(wrapper), encoding="utf-8")

        built = review.build(self.root)
        row = built["sessions"][0]

        self.assertEqual(row["status"], "payout_validation_failed")
        self.assertFalse(row["payoutVerified"])
        self.assertEqual(row["payoutError"], "ValueError")

    def test_betting_roi_is_not_backfilled_from_probability_sessions(self):
        built = review.build(self.root)
        betting = built["bettingEvaluation"]

        self.assertEqual(betting["status"], "not_started_no_frozen_selection_rule")
        self.assertFalse(betting["formalROIAvailable"])
        self.assertTrue(betting["historicalReferenceBacktestExcluded"])
        self.assertFalse(betting["selectionRuleFrozenForTheseSessions"])
        self.assertNotIn("returnRatePct", betting)
        self.assertNotIn("profitYen", betting)

    def test_timestamp_only_change_does_not_rewrite(self):
        changed, first = review.write_if_material_changed(self.root)
        self.assertTrue(changed)
        output = self.root / "Win5FormalReview.json"
        before = output.read_bytes()

        changed, second = review.write_if_material_changed(self.root)

        self.assertFalse(changed)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(review.material(first), review.material(second))


if __name__ == "__main__":
    unittest.main()
