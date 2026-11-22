"""Nightly research scheduling must retain a trusted, fail-closed recovery path."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / ".github/workflows/race-simulator-learning.yml"
RESEARCH = ROOT / ".github/workflows/keibaai-settled-research-publish.yml"


class NightlyResearchRecovery(unittest.TestCase):
    def test_both_night_publications_have_schedule_and_trusted_fallback(self):
        for path in (SIM, RESEARCH):
            with self.subTest(path=str(path)):
                source = path.read_text()
                self.assertIn("  workflow_run:", source)
                self.assertIn("KeibaAI Bet Performance Summary", source)
                self.assertIn("KeibaAI Live Result Sync", source)
                self.assertIn("types: [completed]", source)
                self.assertIn("branches: [main]", source)
                self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
                self.assertIn("github.event.workflow_run.event == 'schedule'", source)
                self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", source)
                self.assertIn("github.event.workflow_run.head_branch == 'main'", source)
                self.assertIn("  workflow_dispatch:", source)
                self.assertIn("  schedule:", source)

    def test_production_catchup_requires_complete_official_results(self):
        for path in (SIM, RESEARCH):
            with self.subTest(path=str(path)):
                source = path.read_text()
                self.assertIn("python", source)
                self.assertIn("production/after_race_result_gate.py", source)
                self.assertIn('if [ "$rc" -eq 10 ]; then', source)
                self.assertIn('git fetch', source)
                self.assertIn('git reset --hard origin/main', source)
        source = SIM.read_text()
        self.assertNotIn("push event checks only", source)
        self.assertIn("SIMULATOR_LEARNING_SOURCE_CONFIRMED", source)

    def test_archive_and_betting_controls_unchanged(self):
        source = RESEARCH.read_text()
        self.assertIn("python production/validate_wide_axis_snapshot.py", source)
        self.assertIn("python -u production/wide_axis_shadow.py", source)
        self.assertIn("research-only", source)


if __name__ == "__main__":
    unittest.main()
