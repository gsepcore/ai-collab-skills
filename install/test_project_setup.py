#!/usr/bin/env python3
"""
Tests for ai-collab-project-setup.py
Run with: python3 install/test_project_setup.py
"""
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_project_setup",
    Path(__file__).parent / "ai-collab-project-setup.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestProjectSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 5, 13, 14, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, agents=("opencode", "codex"), models=None):
        return _mod.setup_project(
            self.root,
            list(agents),
            "antigravity",
            models or {"opencode": "minimax/m2.5", "codex": "openai/gpt-5.5"},
            now=self.now,
        )

    def test_onboards_agents_with_container_and_models(self):
        result = self.setup()

        self.assertEqual(result["gitignore"], "updated")
        self.assertTrue((self.root / ".ai-collab" / "PROTOCOL.md").exists())
        self.assertTrue((self.root / ".ai-collab" / "TEAM.md").exists())
        self.assertTrue((self.root / ".ai-collab" / "agents.json").exists())
        self.assertTrue((self.root / ".ai-collab" / "inbox-all.md").exists())

        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- claude-code (director)", team)
        self.assertIn("- opencode", team)
        self.assertIn("- codex", team)
        self.assertIn("| opencode | worker | antigravity | minimax/m2.5 |", team)
        self.assertIn("| codex | worker | antigravity | openai/gpt-5.5 |", team)

    def test_shared_agents_md_can_hold_multiple_agent_snippets(self):
        self.setup()

        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", agents_md)
        self.assertIn("AI-COLLAB-START agent=codex", agents_md)
        self.assertIn("agent_slug: `opencode`", agents_md)
        self.assertIn("agent_slug: `codex`", agents_md)

    def test_opencode_gets_agent_specific_rules_file(self):
        self.setup()

        rules = (self.root / ".opencode" / "rules" / "ai-collab.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", rules)
        self.assertIn("inbox-opencode.md", rules)

    def test_idempotent_rerun_does_not_duplicate_snippets(self):
        self.setup()
        self.setup()

        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents_md.count("AI-COLLAB-START agent=opencode"), 1)
        self.assertEqual(agents_md.count("AI-COLLAB-START agent=codex"), 1)
        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertEqual(team.count("- opencode"), 1)
        self.assertEqual(team.count("- codex"), 1)

    def test_legacy_aliases_normalize_to_agent_runtime_names(self):
        self.setup(agents=("cursor", "windsurf", "copilot", "antigravity"), models={})

        self.assertTrue((self.root / ".cursorrules").exists())
        self.assertTrue((self.root / ".windsurfrules").exists())
        self.assertTrue((self.root / ".github" / "copilot-instructions.md").exists())
        team = (self.root / ".ai-collab" / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- cursor-native", team)
        self.assertIn("- windsurf-native", team)
        self.assertIn("- copilot-chat", team)
        self.assertIn("- codex", team)


if __name__ == "__main__":
    unittest.main(verbosity=2)
