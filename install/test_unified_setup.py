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

    def test_noninteractive_setup_cannot_skip_role_onboarding(self):
        (self.collab / "agents.json").write_text(
            json.dumps({
                "schema": "ai-collab.agents.v2", "project_id": "prj_test",
                "agents": [{"agent": "codex", "agent_id": "agt_codex"}],
            }),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            assign=[], non_interactive=True,
            installer_source=str(Path(__file__).resolve().parent.parent),
        )

        result = _mod.run_role_onboarding(self.root, args)

        self.assertEqual(result["status"], "required")
        pending = json.loads((self.collab / "role-onboarding.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["status"], "required")
        self.assertEqual(pending["agents"][0]["agent_id"], "agt_codex")

    def test_capability_ack_requires_current_registered_identity_session_and_all_features(self):
        digest = "cap_test"
        (self.collab / "agents.json").write_text(json.dumps({
            "project_id": "prj_test",
            "agents": [{"agent": "codex", "agent_id": "agt_codex"}],
        }), encoding="utf-8")
        (self.collab / "capabilities.json").write_text(json.dumps({
            "capability_catalog": {"digest": digest, "features": [{"id": "visual-eyes"}, {"id": "shared-conversations"}]},
            "capability_onboarding": {"thread": ".ai-collab/discussions/discussion-capability-onboarding-cap_test.md"},
        }), encoding="utf-8")
        sessions = self.collab / "live" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "current-codex.json").write_text(json.dumps({
            "agent": "codex", "agent_id": "agt_codex", "session_id": "ses_current", "status": "active",
        }), encoding="utf-8")
        (sessions / "ses_current.json").write_text(json.dumps({
            "project_id": "prj_test", "agent": "codex", "agent_id": "agt_codex",
            "session_id": "ses_current", "status": "active",
        }), encoding="utf-8")
        discussions = self.collab / "discussions"
        discussions.mkdir()
        thread = discussions / "discussion-capability-onboarding-cap_test.md"
        thread.write_text(
            "## 2026-08-12T12:00:00Z -- codex\n\n"
            "capability_ack: cap_test\nagent_id: agt_codex\nsession_id: ses_old\n"
            "understood_features: visual-eyes, shared-conversations\nautomatic_use: enabled\n\n---\n",
            encoding="utf-8",
        )
        self.assertEqual(_mod.capability_ack_status(self.root, ["codex"])["missing"], ["codex"])

        thread.write_text(
            "## 2026-08-12T12:01:00Z -- codex\n\n"
            "capability_ack: cap_test\nagent_id: agt_codex\nsession_id: ses_current\n"
            "understood_features: visual-eyes, shared-conversations\nautomatic_use: enabled\n\n---\n",
            encoding="utf-8",
        )
        self.assertEqual(_mod.capability_ack_status(self.root, ["codex"])["status"], "confirmed")

        (sessions / "ses_current.json").write_text(json.dumps({
            "project_id": "prj_other", "agent": "codex", "agent_id": "agt_codex",
            "session_id": "ses_current", "status": "active",
        }), encoding="utf-8")
        self.assertEqual(_mod.capability_ack_status(self.root, ["codex"])["missing"], ["codex"])

    def test_capability_onboarding_does_not_redispatch_same_missing_agents_without_explicit_retry(self):
        digest = "cap_test"
        (self.collab / "agents.json").write_text(json.dumps({
            "project_id": "prj_test",
            "agents": [{"agent": "codex", "agent_id": "agt_codex"}, {"agent": "opencode", "agent_id": "agt_opencode"}],
        }), encoding="utf-8")
        thread_rel = ".ai-collab/discussions/discussion-capability-onboarding-cap_test.md"
        (self.collab / "capabilities.json").write_text(json.dumps({
            "capability_catalog": {"digest": digest, "features": [{"id": "shared-conversations"}]},
            "capability_onboarding": {"thread": thread_rel},
        }), encoding="utf-8")
        thread = self.root / thread_rel
        thread.parent.mkdir(parents=True)
        thread.write_text(
            "## 2026-08-17T12:00:00Z -- ai-collab-setup\n\n"
            "type: question\nto: opencode\n\ncapability_ack: cap_test\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(actor="codex", installer_source=None, retry_capability_onboarding=False)

        with mock.patch.object(_mod, "run_visible") as run_visible:
            result = _mod.run_capability_onboarding(self.root, ["codex", "opencode"], args)

        self.assertEqual(result["dispatch"], "already-queued")
        self.assertEqual(result["queued_agents"], ["opencode"])
        run_visible.assert_not_called()

    def test_capability_onboarding_defaults_to_queue_only_even_with_real_wake_targets(self):
        # Confirmed live: without this default, running /collab setup on a
        # project with a visible-chat-only agent (codex under
        # container=antigravity) launched that IDE from scratch as a pure
        # side effect of onboarding -- nobody asked for or was watching for
        # that window. Queue-only must be the default whenever there is a
        # real wake target, not only when there is nothing to wake (the
        # previous logic forced it in exactly the case that didn't matter).
        digest = "cap_test"
        (self.collab / "agents.json").write_text(json.dumps({
            "project_id": "prj_test",
            "agents": [{"agent": "codex", "agent_id": "agt_codex"}, {"agent": "opencode", "agent_id": "agt_opencode"}],
        }), encoding="utf-8")
        (self.collab / "capabilities.json").write_text(json.dumps({
            "capability_catalog": {"digest": digest, "features": [{"id": "shared-conversations"}]},
            "capability_onboarding": {"thread": ".ai-collab/discussions/discussion-capability-onboarding-cap_test.md"},
        }), encoding="utf-8")
        args = argparse.Namespace(actor="claude-code", installer_source=None, retry_capability_onboarding=False)

        with mock.patch.object(_mod, "run_visible", return_value=0) as run_visible:
            result = _mod.run_capability_onboarding(self.root, ["codex", "opencode"], args)

        run_visible.assert_called_once()
        command = run_visible.call_args[0][0]
        self.assertIn("--queue-only", command)
        self.assertEqual(result["dispatch"], "queued-for-daemon")

    def test_capability_onboarding_immediate_wake_is_an_explicit_opt_in(self):
        digest = "cap_test"
        (self.collab / "agents.json").write_text(json.dumps({
            "project_id": "prj_test",
            "agents": [{"agent": "codex", "agent_id": "agt_codex"}, {"agent": "opencode", "agent_id": "agt_opencode"}],
        }), encoding="utf-8")
        (self.collab / "capabilities.json").write_text(json.dumps({
            "capability_catalog": {"digest": digest, "features": [{"id": "shared-conversations"}]},
            "capability_onboarding": {"thread": ".ai-collab/discussions/discussion-capability-onboarding-cap_test.md"},
        }), encoding="utf-8")
        args = argparse.Namespace(actor="claude-code", installer_source=None, retry_capability_onboarding=False)

        with mock.patch.dict(os.environ, {"AI_COLLAB_SETUP_ONBOARDING_IMMEDIATE_WAKE": "1"}):
            with mock.patch.object(_mod, "run_visible", return_value=0) as run_visible:
                result = _mod.run_capability_onboarding(self.root, ["codex", "opencode"], args)

        command = run_visible.call_args[0][0]
        self.assertNotIn("--queue-only", command)
        self.assertEqual(result["dispatch"], "started")

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
                "AI_COLLAB_SETUP_ONBOARDING_QUEUE_ONLY": "1",
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

        self.assertEqual(completed.returncode, 3, completed.stdout + completed.stderr)
        report = json.loads((self.collab / "setup-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "awaiting-agent-acknowledgements")
        self.assertEqual(report["capability_onboarding"]["status"], "awaiting-agent-acknowledgements")
        self.assertTrue(report["capability_onboarding"]["missing"])
        self.assertEqual(report["mode"], "migration")
        self.assertEqual(report["preservation"]["status"], "ok")
        self.assertIn("design-bot", report["project_verification"]["registered_agents"])
        self.assertTrue((home / ".claude" / "skills" / "collab" / "SKILL.md").is_file())
        self.assertTrue((home / ".codex" / "skills" / "collab" / "SKILL.md").is_file())
        self.assertTrue((home / ".claude" / "ai-collab-setup.py").is_file())
        for path, content in protected.items():
            if path.name != "roles.json":
                self.assertEqual(path.read_text(encoding="utf-8"), content)
        migrated_roles = json.loads((self.collab / "roles.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated_roles["schema"], "ai-collab.roles.v2")
        self.assertEqual(migrated_roles["assignments"]["ui-ux-design"]["primary"], "design-bot")
        self.assertTrue(migrated_roles["assignments"]["ui-ux-design"]["primary_agent_id"].startswith("agt_"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
