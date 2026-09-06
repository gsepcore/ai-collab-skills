#!/usr/bin/env python3
"""
Tests for ai-collab-update.py.
Run with: python3 install/test_update.py
"""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_update",
    Path(__file__).parent / "ai-collab-update.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovers_existing_collab_projects(self):
        project = self.home / "work" / "app"
        (project / ".ai-collab").mkdir(parents=True)
        ignored = self.home / "work" / "app" / "node_modules" / "dep"
        (ignored / ".ai-collab").mkdir(parents=True)

        projects = _mod.discover_projects(self.home, max_depth=4)

        self.assertIn(project, projects)
        self.assertNotIn(ignored, projects)

    def test_project_args_from_manifest_preserves_agents_models_and_container(self):
        project = self.home / "app"
        collab = project / ".ai-collab"
        collab.mkdir(parents=True)
        (collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "opencode", "container": "antigravity", "model": "minimax/m2.5"},
                        {"agent": "codex", "container": "antigravity", "model": "openai/gpt-5.5"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        agents, container, models = _mod.project_args_from_manifest(project)

        self.assertEqual(agents, ["claude-code", "opencode", "codex"])
        self.assertEqual(container, "antigravity")
        self.assertEqual(models["opencode"], "minimax/m2.5")
        self.assertEqual(models["codex"], "openai/gpt-5.5")

    def test_project_args_omits_unknown_model_to_keep_generated_snippets_stable(self):
        project = self.home / "app"
        collab = project / ".ai-collab"
        collab.mkdir(parents=True)
        (collab / "agents.json").write_text(
            json.dumps({"agents": [{"agent": "codex", "container": "unknown", "model": "unknown"}]}),
            encoding="utf-8",
        )

        agents, container, models = _mod.project_args_from_manifest(project)

        self.assertEqual(agents, ["claude-code", "codex"])
        self.assertEqual(container, "unknown")
        self.assertEqual(models, {})

    def test_global_update_includes_codex_bridge_and_recovery(self):
        rels = [rel for rel, _dest, _executable in _mod.GLOBAL_FILES]
        destinations = [str(dest) for _rel, dest, _executable in _mod.GLOBAL_FILES]

        self.assertIn("install/ai-collab-codex-bridge.py", rels)
        self.assertIn("install/ai-collab-recover.py", rels)
        self.assertIn("install/ai-collab-team.py", rels)
        self.assertIn("install/ai-collab-session.py", rels)
        self.assertIn("install/ai-collab-turn.py", rels)
        self.assertIn("install/ai-collab-setup.py", rels)
        self.assertIn("install/ai-collab-debate.py", rels)
        self.assertTrue(any(".codex/skills/collab/SKILL.md" in path for path in destinations))

    def test_fetch_can_use_pinned_local_development_source(self):
        source = self.home / "source"
        (source / "install").mkdir(parents=True)
        (source / "install" / "sample.py").write_bytes(b"local-version")
        previous = _mod.LOCAL_SOURCE
        try:
            _mod.LOCAL_SOURCE = source.resolve()
            self.assertEqual(_mod.fetch("install/sample.py", 1), b"local-version")
            with self.assertRaises(OSError):
                _mod.fetch("../outside", 1)
        finally:
            _mod.LOCAL_SOURCE = previous

    def test_disable_legacy_daemon_removes_leftover_plist_by_default(self):
        plist = self.home / "LaunchAgents" / "com.gsepcore.ai-collab.plist"
        plist.parent.mkdir(parents=True)
        plist.write_text("<plist/>", encoding="utf-8")
        marker = self.home / ".ai-collab-daemon-enabled"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        result = _mod.disable_legacy_daemon(
            dry_run=False,
            plist_path=plist,
            marker_path=marker,
            has_crontab=lambda: False,
            runner=fake_run,
        )

        self.assertEqual(result["status"], "removed")
        self.assertFalse(plist.exists())
        self.assertTrue(any(cmd[:2] == ["launchctl", "unload"] for cmd in calls))

    def test_disable_legacy_daemon_keeps_explicit_opt_in(self):
        plist = self.home / "LaunchAgents" / "com.gsepcore.ai-collab.plist"
        plist.parent.mkdir(parents=True)
        plist.write_text("<plist/>", encoding="utf-8")
        marker = self.home / ".ai-collab-daemon-enabled"
        marker.write_text("", encoding="utf-8")

        def fail_run(cmd, **kwargs):
            self.fail(f"should not touch launchd/cron when opted in: {cmd}")

        result = _mod.disable_legacy_daemon(
            dry_run=False,
            plist_path=plist,
            marker_path=marker,
            has_crontab=lambda: True,
            runner=fail_run,
        )

        self.assertEqual(result["status"], "kept")
        self.assertTrue(plist.exists())

    def test_disable_legacy_daemon_is_noop_when_nothing_installed(self):
        plist = self.home / "LaunchAgents" / "com.gsepcore.ai-collab.plist"
        marker = self.home / ".ai-collab-daemon-enabled"

        result = _mod.disable_legacy_daemon(
            dry_run=False,
            plist_path=plist,
            marker_path=marker,
            has_crontab=lambda: False,
            runner=lambda *a, **k: self.fail("should not run any command"),
        )

        self.assertEqual(result["status"], "noop")

    def test_disable_legacy_daemon_dry_run_does_not_delete(self):
        plist = self.home / "LaunchAgents" / "com.gsepcore.ai-collab.plist"
        plist.parent.mkdir(parents=True)
        plist.write_text("<plist/>", encoding="utf-8")
        marker = self.home / ".ai-collab-daemon-enabled"

        result = _mod.disable_legacy_daemon(
            dry_run=True,
            plist_path=plist,
            marker_path=marker,
            has_crontab=lambda: False,
            runner=lambda *a, **k: self.fail("dry-run should not execute commands"),
        )

        self.assertEqual(result["status"], "dry-run")
        self.assertTrue(plist.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
