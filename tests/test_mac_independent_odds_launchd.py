"""Cross-platform tests for opt-in macOS LaunchAgent generator."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ops"))
from mac_independent_odds_launchd import (
    LABEL, PLIST_NAME, render_agent, write_agent,
)


class MacLaunchAgentSetupTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        base=Path(self.t.name)
        self.root=base/"repo"
        (self.root/"production").mkdir(parents=True)
        (self.root/"production"/"independent_odds_bridge.py").write_text(
            'print("synthetic")\n',encoding="utf-8")
        self.home=base/"fake-home"

    def test_agent_has_explicit_bridge_and_no_external_provider(self):
        data=plistlib.loads(render_agent(self.root,sys.executable,self.home))
        self.assertEqual(data["Label"],LABEL)
        self.assertIn("independent_odds_bridge.py",data["ProgramArguments"][1])
        self.assertIn("--watch",data["ProgramArguments"])
        self.assertIn("30",data["ProgramArguments"])
        self.assertTrue(data["RunAtLoad"])
        self.assertFalse(data["KeepAlive"])
        self.assertNotIn("Network",repr(data))

    def test_render_is_side_effect_free(self):
        render_agent(self.root,sys.executable,self.home)
        self.assertFalse(self.home.exists())

    def test_write_opt_in_creates_plist_but_not_enables_agent(self):
        path=write_agent(self.root,sys.executable,self.home)
        self.assertEqual(path.name,PLIST_NAME)
        data=plistlib.loads(path.read_bytes())
        self.assertEqual(data["Label"],LABEL)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_refuse_overwrite_unless_explicit(self):
        path=write_agent(self.root,sys.executable,self.home)
        with self.assertRaises(FileExistsError):
            write_agent(self.root,sys.executable,self.home)
        data=plistlib.loads(
            write_agent(self.root,sys.executable,self.home,overwrite=True).read_bytes())
        self.assertEqual(data["Label"],LABEL)

    def test_bad_repo_and_invalid_poll_interval_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"bridge script not found"):
            render_agent(self.home,sys.executable,self.home)
        with self.assertRaisesRegex(ValueError,"poll_seconds"):
            render_agent(self.root,sys.executable,self.home,poll_seconds=0)


if __name__=="__main__":
    unittest.main()
