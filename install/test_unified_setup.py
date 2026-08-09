#!/usr/bin/env python3
"""Tests for the unified AI Collab setup entrypoint."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_spec = importlib.util.spec_from_file_location(
    "ai_collab_unified_setup",
    Path(__file__).parent / "ai-collab-setup.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestUnifiedSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.collab = self.root / ".ai-collab"
        self.collab.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_defaults_preserve_custom_agents_models_and_container(self):
        (self.collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "codex", "container": "antigravity", "model": "openai/gpt-5.6"},
                        {"agent": "custom-design", "container": "antigravity", "model": "vendor/design"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        agents, container, models = _mod.manifest_defaults(self.root)

        self.assertEqual(agents, ["codex", "custom-design"])
        self.assertEqual(container, "antigravity")
        self.assertEqual(models["custom-design"], "vendor/design")

    def test_manifest_defaults_do_not_rewrite_unknown_model_placeholder(self):
        (self.collab / "agents.json").write_text(
            json.dumps({"agents": [{"agent": "codex", "container": "unknown", "model": "unknown"}]}),
            encoding="utf-8",
        )

        agents, container, models = _mod.manifest_defaults(self.root)

        self.assertEqual(agents, ["codex"])
        self.assertEqual(container, "unknown")
        self.assertEqual(models, {})

    def test_project_command_always_refreshes_protocol(self):
        command = _mod.build_project_command(
            Path("/tmp/helper.py"),
            self.root,
            ["codex", "opencode"],
            "antigravity",
            {"codex": "openai/gpt-5.6"},
            True,
        )

        self.assertIn("--refresh-protocol", command)
        self.assertIn("--non-interactive", command)
        self.assertEqual(command[command.index("--agents") + 1], "codex,opencode")

    def test_protected_snapshot_detects_conversation_or_role_mutation(self):
        discussions = self.collab / "discussions"
        discussions.mkdir()
        thread = discussions / "discussion-test.md"
        thread.write_text("original", encoding="utf-8")
        roles = self.collab / "roles.json"
        roles.write_text("{}", encoding="utf-8")
        before = _mod.protected_snapshot(self.root)

        thread.write_text("changed", encoding="utf-8")
        after = _mod.protected_snapshot(self.root)

        self.assertEqual(_mod.protected_changes(before, after), [".ai-collab/discussions/discussion-test.md"])

    def test_protected_snapshot_allows_append_only_agent_progress(self):
        discussions = self.collab / "discussions"
        discussions.mkdir()
        thread = discussions / "discussion-test.md"
        thread.write_text("original\n", encoding="utf-8")
        before = _mod.protected_snapshot(self.root)

        thread.write_text("original\nnew agent reply\n", encoding="utf-8")
        after = _mod.protected_snapshot(self.root)

        self.assertEqual(_mod.protected_changes(before, after), [])
        self.assertEqual(_mod.protected_appends(before, after), [".ai-collab/discussions/discussion-test.md"])

    def test_verify_project_requires_capabilities_for_every_expected_agent(self):
        for relative in ["PROTOCOL.md", "TEAM.md", "inbox-all.md"]:
            (self.collab / relative).write_text("ok", encoding="utf-8")
        (self.collab / "agents.json").write_text(
            json.dumps({"agents": [{"agent": "codex"}, {"agent": "opencode"}]}), encoding="utf-8"
        )
        (self.collab / "capabilities.json").write_text(
            json.dumps({"agents": [{"agent": "codex"}]}), encoding="utf-8"
        )

        result = _mod.verify_project(self.root, ["codex", "opencode"])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["missing_agent_capabilities"], ["opencode"])

    def test_global_reinstall_suppresses_recursive_project_setup(self):
        installer = self.root / "install.sh"
        installer.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        observed = {}

        def fake_run(command, *, cwd, env=None):
            observed["command"] = command
            observed["env"] = env
            return 0

        with mock.patch.object(_mod, "run_visible", side_effect=fake_run):
            result = _mod.reinstall_global(self.root, str(installer), 1)

        self.assertEqual(result["status"], "updated")
        self.assertEqual(observed["env"]["AI_COLLAB_SKIP_PROJECT_SETUP"], "1")
        self.assertEqual(observed["env"]["AI_COLLAB_YES"], "1")

    def test_existing_project_full_local_migration_preserves_history_and_installs_both_skills(self):
        home = Path(self.tmp.name) / "home"
        (home / ".claude").mkdir(parents=True)
        (self.collab / "discussions").mkdir(exist_ok=True)
        (self.collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "codex", "container": "antigravity", "model": "openai/gpt-5.6"},
                        {"agent": "opencode", "container": "antigravity", "model": "minimax/m2.7"},
                        {"agent": "design-bot", "container": "antigravity", "model": "vendor/design"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        protected = {
            self.collab / "roles.json": '{"assignments":{"ui-ux-design":{"primary":"design-bot"}}}\n',
            self.collab / "inbox-codex.md": "existing inbox content\n",
            self.collab / "discussions" / "discussion-existing.md": "existing discussion content\n",
        }
        for path, content in protected.items():
            path.write_text(content, encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "AI_COLLAB_CLAUDE_DIR": str(home / ".claude"),
                "CODEX_HOME": str(home / ".codex"),
                "AI_COLLAB_NO_DAEMON": "1",
                "AI_COLLAB_NO_CODEX_BRIDGE": "1",
                "AI_COLLAB_NO_IDE_BRIDGE": "1",
                "AI_COLLAB_INSTALL_OCR": "0",
            }
        )
        repo = Path(__file__).resolve().parent.parent

        completed = subprocess.run(
            [
                sys.executable,
                str(repo / "install" / "ai-collab-setup.py"),
                "--root",
                str(self.root),
                "--installer-source",
                str(repo),
                "--non-interactive",
                "--skip-doctor",
            ],
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads((self.collab / "setup-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["mode"], "migration")
        self.assertEqual(report["preservation"]["status"], "ok")
        self.assertIn("design-bot", report["project_verification"]["registered_agents"])
        self.assertTrue((home / ".claude" / "skills" / "collab" / "SKILL.md").is_file())
        self.assertTrue((home / ".codex" / "skills" / "collab" / "SKILL.md").is_file())
        self.assertTrue((home / ".claude" / "ai-collab-setup.py").is_file())
        for path, content in protected.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
