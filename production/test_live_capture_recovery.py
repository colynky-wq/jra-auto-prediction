from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from live_capture_policy import (
    FORMAL_TIMING_MARKER,
    RECOVERY_TIMING_MARKER,
    classify_capture_timing,
)
from live_recording_resume import published_race_ids


class LiveCapturePolicyTests(unittest.TestCase):
    def classify(self, remaining: float):
        return classify_capture_timing(
            remaining,
            formal_early=12.0,
            formal_late=8.0,
            recovery_late=5.0,
        )

    def test_capture_window_boundaries_do_not_mislabel_late_recovery(self):
        self.assertIsNone(self.classify(12.01))
        self.assertEqual(self.classify(12.0), FORMAL_TIMING_MARKER)
        self.assertEqual(self.classify(10.0), FORMAL_TIMING_MARKER)
        self.assertEqual(self.classify(8.0), FORMAL_TIMING_MARKER)
        self.assertEqual(self.classify(7.99), RECOVERY_TIMING_MARKER)
        self.assertEqual(self.classify(5.0), RECOVERY_TIMING_MARKER)
        self.assertIsNone(self.classify(4.99))

    @staticmethod
    def race(timing: str) -> dict:
        return {
            'raceID': '202608040710',
            'raceDate': '2026-10-18',
            'startTime': '14:55',
            'predictionTiming': timing,
            'horses': [
                {
                    'number': n,
                    'name': str(n),
                    'aiProbability': 20.0,
                    'marketProbability': 20.0,
                    'aiTop2Probability': 40.0,
                    'marketTop2Probability': 40.0,
                    'aiTop3Probability': 60.0,
                    'marketTop3Probability': 60.0,
                }
                for n in (1, 2)
            ],
        }

    def test_quarantined_late_recovery_resumes_without_formal_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = self.race(RECOVERY_TIMING_MARKER)
            recovery = root / 'reports' / '20261018' / 'late_recovery'
            recovery.mkdir(parents=True)
            (recovery / (row['raceID'] + '.json')).write_text(json.dumps(row), encoding='utf-8')
            schedule = [{'race_id': row['raceID'], 'start_time': row['startTime']}]
            self.assertEqual(
                published_race_ids(root, '20261018', schedule, require_research=True),
                {row['raceID']},
            )

    def test_recovery_row_inside_racedata_is_not_formal_resume_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = self.race(RECOVERY_TIMING_MARKER)
            (root / 'RaceData.json').write_text(json.dumps([row]), encoding='utf-8')
            schedule = [{'race_id': row['raceID'], 'start_time': row['startTime']}]
            self.assertEqual(
                published_race_ids(root, '20261018', schedule, require_research=False),
                set(),
            )

    def test_formal_capture_still_requires_formal_research_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = self.race(FORMAL_TIMING_MARKER)
            (root / 'RaceData.json').write_text(json.dumps([row]), encoding='utf-8')
            schedule = [{'race_id': row['raceID'], 'start_time': row['startTime']}]
            self.assertEqual(
                published_race_ids(root, '20261018', schedule, require_research=True),
                set(),
            )
            self.assertEqual(
                published_race_ids(root, '20261018', schedule, require_research=False),
                {row['raceID']},
            )


if __name__ == '__main__':
    unittest.main()
