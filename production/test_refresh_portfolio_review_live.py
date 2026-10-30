import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import refresh_portfolio_review_live as live


class TestRefreshPortfolioReviewLive(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "PortfolioResearchData.json").write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_review(self, value):
        (self.root / "PortfolioReview100.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @patch.object(live, "attach_formal_evidence", side_effect=lambda root, data: data)
    @patch.object(live, "build")
    def test_timestamp_only_change_does_not_rewrite(self, build_mock, _attach_mock):
        prior = {
            "generatedAtJST": "2026-10-31T08:30:00+09:00",
            "racesObserved": 68,
            "remainingTo100": 32,
            "qualityChecks": {"formal10minVerifiedRaces": 68},
        }
        candidate = {**prior, "generatedAtJST": "2026-10-31T09:00:00+09:00"}
        self.write_review(prior)
        before = (self.root / "PortfolioReview100.json").read_bytes()
        build_mock.return_value = candidate

        changed, returned = live.refresh(self.root)

        self.assertFalse(changed)
        self.assertEqual(returned, prior)
        self.assertEqual((self.root / "PortfolioReview100.json").read_bytes(), before)

    @patch.object(live, "attach_formal_evidence", side_effect=lambda root, data: data)
    @patch.object(live, "build")
    def test_material_count_change_rewrites(self, build_mock, _attach_mock):
        prior = {
            "generatedAtJST": "2026-10-31T08:30:00+09:00",
            "racesObserved": 68,
            "remainingTo100": 32,
            "qualityChecks": {"formal10minVerifiedRaces": 68},
        }
        candidate = {
            "generatedAtJST": "2026-10-31T09:40:00+09:00",
            "racesObserved": 69,
            "remainingTo100": 31,
            "qualityChecks": {"formal10minVerifiedRaces": 69},
        }
        self.write_review(prior)
        build_mock.return_value = candidate

        changed, returned = live.refresh(self.root)

        self.assertTrue(changed)
        self.assertEqual(returned, candidate)
        saved = json.loads((self.root / "PortfolioReview100.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["racesObserved"], 69)
        self.assertEqual(saved["qualityChecks"]["formal10minVerifiedRaces"], 69)

    @patch.object(live, "attach_formal_evidence", side_effect=lambda root, data: data)
    @patch.object(live, "build")
    def test_missing_review_is_created(self, build_mock, _attach_mock):
        candidate = {
            "generatedAtJST": "2026-10-31T09:40:00+09:00",
            "racesObserved": 1,
            "remainingTo100": 99,
            "qualityChecks": {"formal10minVerifiedRaces": 1},
        }
        build_mock.return_value = candidate

        changed, returned = live.refresh(self.root)

        self.assertTrue(changed)
        self.assertEqual(returned, candidate)
        self.assertTrue((self.root / "PortfolioReview100.json").exists())


if __name__ == "__main__":
    unittest.main()
