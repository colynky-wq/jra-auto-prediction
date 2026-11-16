#!/usr/bin/env python3
"""Offline Git-only safety tests for independent aligned audit publication."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "publish_aligned_audit_from_main.sh"
MOCK = '''
import json
from pathlib import Path
mode = Path("audit-mode.txt").read_text().strip()
if Path("reports/local-unpublished.json").exists():
    raise RuntimeError("unpublished local data leaked into audit")
if mode == "success":
    state, current, events, rc = "verified", True, [], 0
elif mode == "blocked":
    state, current, events, rc = "failed", False, [], 1
else:
    state, current, events, rc = "failed", True, [{"event": "fake_success"}], 1
doc = {"projectionState": state, "aligned": {"countsAreCurrent": current},
       "betaDecision": {"eligibleForResearchBeta": False},
       "notificationEvents": events}
Path("AlignedPortfolioReview.json").write_text(
    json.dumps(doc, ensure_ascii=False) + "\\n", encoding="utf-8")
Path("AlignedPortfolioReview.md").write_text(
    "audit state: " + state + "\\n", encoding="utf-8")
raise SystemExit(rc)
'''


def run(args, cwd, check=True):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                          check=check, timeout=30)


class AuditPublicationSafety(unittest.TestCase):
    def fixture(self, root, mode):
        root = Path(root)
        bare, local = root / "remote.git", root / "repo"
        run(["git", "init", "--bare", "--initial-branch=main", str(bare)], root)
        local.mkdir()
        run(["git", "init", "-b", "main"], local)
        run(["git", "config", "user.name", "offline-test"], local)
        run(["git", "config", "user.email", "test@example.invalid"], local)
        (local / "production").mkdir()
        (local / "production" / "aligned_portfolio_review.py").write_text(MOCK)
        (local / "audit-mode.txt").write_text(mode)
        (local / "AlignedPortfolioReview.json").write_text(
            json.dumps({"projectionState": "verified", "aligned": {"countsAreCurrent": True}})
            + "\n")
        (local / "AlignedPortfolioReview.md").write_text("old\n")
        run(["git", "add", "."], local)
        run(["git", "commit", "-m", "initial"], local)
        run(["git", "remote", "add", "origin", str(bare)], local)
        run(["git", "push", "-u", "origin", "main"], local)
        (local / "reports").mkdir()
        (local / "reports" / "local-unpublished.json").write_text('{"fake": true}')
        return bare, local

    def published(self, bare):
        raw = run(["git", "show", "main:AlignedPortfolioReview.json"], bare).stdout
        return json.loads(raw)

    def test_verification_publishes_and_repeated_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, local = self.fixture(tmp, "success")
            first = run(["bash", str(SCRIPT)], local)
            self.assertIn("ALIGNED_AUDIT_PUBLISHED", first.stdout)
            self.assertEqual(self.published(bare)["projectionState"], "verified")
            again = run(["bash", str(SCRIPT)], local)
            self.assertIn("ALIGNED_AUDIT_UNCHANGED", again.stdout)

    def test_fail_closed_projection_is_published_without_success_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, local = self.fixture(tmp, "blocked")
            result = run(["bash", str(SCRIPT)], local)
            self.assertIn("ALIGNED_AUDIT_FAILED", result.stdout)
            state = self.published(bare)
            self.assertFalse(state["aligned"]["countsAreCurrent"])
            self.assertEqual(state["notificationEvents"], [])
            self.assertFalse(state["betaDecision"]["eligibleForResearchBeta"])

    def test_invalid_blocked_state_must_not_be_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, local = self.fixture(tmp, "unsafe")
            result = run(["bash", str(SCRIPT)], local, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("notificationEvents", self.published(bare))


if __name__ == "__main__":
    unittest.main()
