#!/usr/bin/env python3
"""Tests for the deterministic always-on turn preflight."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


_spec = importlib.util.spec_from_file_location(
    "ai_collab_turn",
    Path(__file__).parent / "ai-collab-turn.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_turn"] = _mod
_spec.loader.exec_module(_mod)


class TestTurnPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        collab = self.root / ".ai-collab"
        (collab / "discussions").mkdir(parents=True)
        (collab / "agents.json").write_text(
            json.dumps(
                {
                    "schema": "ai-collab.agents.v2",
                    "project_id": "prj_test",
                    "agents": [
                        {"agent": "codex", "agent_id": "agt_codex", "container": "antigravity"},
                        {"agent": "opencode", "agent_id": "agt_opencode", "container": "terminal"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (collab / "roles.json").write_text(
            json.dumps(
                {
                    "schema": "ai-collab.roles.v2",
                    "assignments": {
                        "senior-director": {"primary": "codex"},
                        "frontend": {"primary": "opencode"},
                        "backend": {"primary": "codex"},
                        "qa": {"primary": "opencode"},
                        "deployment": {"primary": None},
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def packet(self, agent="codex", prompt=""):
        with patch.object(_mod, "register_session", return_value={"session_id": "ses_test"}):
            return _mod.build_packet(self.root, agent, prompt)

    def test_collaboration_is_always_on_without_slash_command(self):
        packet = self.packet(prompt="Corrige el error pequeño en el servidor")

        self.assertTrue(packet["active"])
        self.assertEqual(packet["mode"], "always-on")
        self.assertFalse(packet["user_invocation_required"])
        self.assertEqual(packet["intent"]["action"], "execute-with-shared-state")

    def test_routes_normal_request_to_configured_role_owner(self):
        packet = self.packet(prompt="Construye la interfaz frontend en React")

        self.assertEqual(packet["intent"]["action"], "route-to-role-owner")
        self.assertEqual(packet["intent"]["owners"], ["opencode"])
        self.assertIn("Create a task thread/inbox", packet["required_actions"][-1])

    def test_team_request_automatically_orchestrates(self):
        packet = self.packet(prompt="Que el equipo implemente backend, frontend y pruebas")

        self.assertEqual(packet["intent"]["action"], "orchestrate")
        self.assertEqual(packet["intent"]["owners"], ["codex", "opencode"])

    def test_debate_request_automatically_convenes(self):
        packet = self.packet(prompt="Quiero que los agentes debatan la arquitectura")

        self.assertEqual(packet["intent"]["action"], "convene-discussion")

    def test_vacant_role_requires_assignment_instead_of_silent_execution(self):
        packet = self.packet(prompt="Haz el deployment a producción")

        self.assertEqual(packet["intent"]["action"], "resolve-vacant-role")
        self.assertEqual(packet["intent"]["vacant_roles"], ["deployment"])
        self.assertIn("assign the vacant role", packet["required_actions"][-1])

    def test_short_role_signal_does_not_match_inside_another_word(self):
        packet = self.packet(prompt="Quiero construir el servidor")

        self.assertEqual(packet["intent"]["roles"], ["backend"])

    def test_only_latest_open_thread_message_counts_as_pending_mention(self):
        thread = self.root / ".ai-collab/discussions/discussion-open.md"
        thread.write_text(
            "---\nstatus: open\n---\n"
            "## 2026-01-01T00:00:00Z -- opencode\n\nto: codex\n\n@codex answer\n\n---\n"
            "## 2026-01-01T00:01:00Z -- codex\n\nto: opencode\n\nanswered\n\n---\n",
            encoding="utf-8",
        )

        self.assertEqual(self.packet()["direct_mentions"], [])

        thread.write_text(
            thread.read_text(encoding="utf-8")
            + "## 2026-01-01T00:02:00Z -- opencode\n\nto: codex\n\nnew question\n\n---\n",
            encoding="utf-8",
        )
        self.assertEqual(self.packet()["direct_mentions"], [".ai-collab/discussions/discussion-open.md"])

    def test_closed_thread_never_becomes_pending_mention(self):
        thread = self.root / ".ai-collab/discussions/discussion-closed.md"
        thread.write_text(
            "---\nstatus: closed\n---\n"
            "## 2026-01-01T00:00:00Z -- opencode\n\nto: codex\n\n@codex old request\n\n---\n",
            encoding="utf-8",
        )

        self.assertEqual(self.packet()["direct_mentions"], [])

    def test_stale_open_thread_does_not_interrupt_new_work(self):
        thread = self.root / ".ai-collab/discussions/discussion-stale.md"
        thread.write_text(
            "---\nstatus: open\n---\n"
            "## 2026-01-01T00:00:00Z -- opencode\n\nto: codex\n\n@codex old request\n\n---\n",
            encoding="utf-8",
        )
        stale = time.time() - 90000
        os.utime(thread, (stale, stale))

        self.assertEqual(self.packet()["direct_mentions"], [])
        self.assertEqual(_mod.direct_mentions(self.root, "codex", max_age_seconds=-1), [".ai-collab/discussions/discussion-stale.md"])

    def test_unread_direct_inbox_becomes_required_action(self):
        (self.root / ".ai-collab/inbox-codex.md").write_text(
            "---\nstatus: unread\ntask_id: task-1\nfrom: opencode\nto: codex\n---\nDo it\n",
            encoding="utf-8",
        )

        packet = self.packet()

        self.assertEqual(packet["unread_inboxes"][0]["task_id"], "task-1")
        self.assertEqual(packet["required_actions"][0], "claim-and-execute-unread-inbox-before-unrelated-work")

    def test_missing_project_manifest_requires_setup(self):
        other = Path(tempfile.mkdtemp())
        packet = _mod.build_packet(other, "codex")

        self.assertFalse(packet["active"])
        self.assertEqual(packet["required_action"], "run-collab-setup")


if __name__ == "__main__":
    unittest.main()
