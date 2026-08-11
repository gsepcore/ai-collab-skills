#!/usr/bin/env python3
"""
Tests for ai-collab-orchestrate.py
Run with: python3 install/test_orchestrate.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_orchestrate",
    Path(__file__).parent / "ai-collab-orchestrate.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_orchestrate"] = _mod
_spec.loader.exec_module(_mod)


class TestOrchestrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        collab = self.root / ".ai-collab"
        collab.mkdir()
        (collab / "TEAM.md").write_text(
            """\
## Roster

- codex
- claude-code
- opencode
""",
            encoding="utf-8",
        )
        (collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "codex"},
                        {"agent": "claude-code"},
                        {"agent": "opencode"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (collab / "roles.json").write_text(
            json.dumps(
                {
                    "assignments": {
                        "senior-director": {"primary": "codex"},
                        "frontend": {"primary": "claude-code"},
                        "qa": {"primary": "opencode"},
                        "ui-ux-design": {"primary": None},
                    }
                }
            ),
            encoding="utf-8",
        )
        self._visible_wake = _mod.visible_wake
        self._wait_for_inbox_response = _mod.wait_for_inbox_response
        _mod.wait_for_inbox_response = lambda path, timeout: ""
        _mod.visible_wake = lambda root, path: {
            "ok": True,
            "result": {
                "action": "thread-mentions",
                "results": [{"target_slug": "opencode", "action": "dispatched"}],
            },
            "reason": "",
        }

    def tearDown(self):
        _mod.visible_wake = self._visible_wake
        _mod.wait_for_inbox_response = self._wait_for_inbox_response
        self.tmp.cleanup()

    def run_cli(self, *args):
        return _mod.main(["--root", str(self.root), *args])

    def test_init_creates_director_locked_run(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-1",
            "--goal",
            "Implement a safe feature",
            "--director",
            "codex",
            "--agents",
            "claude-code,opencode",
        )

        director = json.loads((self.root / ".ai-collab/runs/run-1/director.json").read_text(encoding="utf-8"))
        self.assertEqual(director["director"], "codex")
        self.assertEqual(director["director_lock"], "active")
        self.assertTrue((self.root / ".ai-collab/runs/run-1/PLAN.md").exists())
        self.assertTrue((self.root / ".ai-collab/runs/run-1/tasks.json").exists())

    def test_init_infers_director_and_participants_from_roles(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-role-defaults",
            "--goal",
            "Implement a feature with the configured team",
        )

        director = json.loads(
            (self.root / ".ai-collab/runs/run-role-defaults/director.json").read_text(encoding="utf-8")
        )
        self.assertEqual(director["director"], "codex")
        self.assertEqual(director["agents"], ["claude-code", "opencode"])

    def test_add_task_routes_owner_from_role(self):
        self.run_cli("init", "--run-id", "run-role-task", "--goal", "Build frontend")
        self.run_cli(
            "add-task",
            "--run-id",
            "run-role-task",
            "--actor",
            "codex",
            "--task-id",
            "task-frontend",
            "--title",
            "Build UI",
            "--role",
            "frontend",
            "--description",
            "Implement the interface.",
        )

        tasks = json.loads((self.root / ".ai-collab/runs/run-role-task/tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(tasks["tasks"][0]["owner"], "claude-code")
        self.assertEqual(tasks["tasks"][0]["role"], "frontend")

    def test_add_task_rejects_vacant_role(self):
        self.run_cli("init", "--run-id", "run-vacant-role", "--goal", "Design product")
        with self.assertRaises(SystemExit):
            self.run_cli(
                "add-task",
                "--run-id",
                "run-vacant-role",
                "--actor",
                "codex",
                "--title",
                "Design UI",
                "--role",
                "ui-ux-design",
                "--description",
                "Create product design.",
            )

    def test_explicit_owner_overrides_vacant_role(self):
        self.run_cli("init", "--run-id", "run-design-override", "--goal", "Prototype design")
        self.run_cli(
            "add-task",
            "--run-id",
            "run-design-override",
            "--actor",
            "codex",
            "--title",
            "Prototype UI",
            "--role",
            "ui-ux-design",
            "--owner",
            "claude-code",
            "--description",
            "Create a temporary UI prototype.",
        )

        tasks = json.loads((self.root / ".ai-collab/runs/run-design-override/tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(tasks["tasks"][0]["owner"], "claude-code")

    def test_add_task_and_assign_writes_inbox_and_thread(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-2",
            "--goal",
            "Coordinate work",
            "--director",
            "codex",
            "--agents",
            "opencode",
        )
        self.run_cli(
            "add-task",
            "--run-id",
            "run-2",
            "--actor",
            "codex",
            "--task-id",
            "task-docs",
            "--title",
            "Review docs",
            "--owner",
            "opencode",
            "--description",
            "Review README for orchestration docs.",
            "--allowed-files",
            "README.md,SKILL.md",
            "--validation",
            "docs reviewed",
        )
        self.run_cli("assign", "--run-id", "run-2", "--actor", "codex", "--task-id", "task-docs")

        inbox = (self.root / ".ai-collab/inbox-opencode.md").read_text(encoding="utf-8")
        self.assertIn("status: unread", inbox)
        self.assertIn("run_id: run-2", inbox)
        self.assertIn("Allowed files: README.md, SKILL.md", inbox)
        thread = (self.root / ".ai-collab/thread-task-docs.md").read_text(encoding="utf-8")
        self.assertIn("Assigned to @opencode", thread)
        tasks = json.loads((self.root / ".ai-collab/runs/run-2/tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(tasks["tasks"][0]["status"], "assigned")
        self.assertEqual(tasks["tasks"][0]["dispatch"]["status"], "submitted-visibly")

    def test_assign_to_codex_skips_internal_wait_and_writes_visible_chat(self):
        waits = []
        _mod.wait_for_inbox_response = lambda path, timeout: waits.append(timeout) or ""
        self.run_cli(
            "init", "--run-id", "run-codex-visible", "--goal", "Ask Codex",
            "--director", "claude-code", "--agents", "codex",
        )
        self.run_cli(
            "add-task", "--run-id", "run-codex-visible", "--actor", "claude-code",
            "--task-id", "task-codex", "--title", "Review", "--owner", "codex",
            "--description", "Review the implementation.",
        )

        result = self.run_cli(
            "assign", "--run-id", "run-codex-visible", "--actor", "claude-code",
            "--task-id", "task-codex", "--internal-wait-seconds", "30",
        )

        self.assertEqual(result, 0)
        self.assertEqual(waits, [0])

    def test_assign_skips_visible_escalation_after_internal_claim(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-internal-response",
            "--goal",
            "Coordinate work",
            "--director",
            "codex",
            "--agents",
            "opencode",
        )
        self.run_cli(
            "add-task",
            "--run-id",
            "run-internal-response",
            "--actor",
            "codex",
            "--task-id",
            "task-internal",
            "--title",
            "Review implementation",
            "--owner",
            "opencode",
            "--description",
            "Review the implementation.",
        )
        visible_calls = []
        _mod.wait_for_inbox_response = lambda path, timeout: "claimed"
        _mod.visible_wake = lambda root, path: visible_calls.append((root, path))

        result = self.run_cli(
            "assign",
            "--run-id",
            "run-internal-response",
            "--actor",
            "codex",
            "--task-id",
            "task-internal",
        )

        self.assertEqual(result, 0)
        self.assertEqual(visible_calls, [])
        tasks = json.loads(
            (self.root / ".ai-collab/runs/run-internal-response/tasks.json").read_text(encoding="utf-8")
        )
        dispatch = tasks["tasks"][0]["dispatch"]
        self.assertEqual(dispatch["status"], "internal-response")
        self.assertEqual(dispatch["evidence"]["inbox_status"], "claimed")

    def test_assign_fails_closed_when_visible_agent_cannot_be_activated(self):
        self.run_cli("init", "--run-id", "run-visible-fail", "--goal", "Coordinate", "--director", "codex", "--agents", "opencode")
        self.run_cli(
            "add-task", "--run-id", "run-visible-fail", "--actor", "codex", "--task-id", "task-fail",
            "--title", "Review", "--owner", "opencode", "--description", "Review the implementation.",
        )
        _mod.visible_wake = lambda root, path: {"ok": False, "reason": "no visible OpenCode TUI"}
        result = self.run_cli("assign", "--run-id", "run-visible-fail", "--actor", "codex", "--task-id", "task-fail")
        self.assertEqual(result, 2)
        tasks = json.loads((self.root / ".ai-collab/runs/run-visible-fail/tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(tasks["tasks"][0]["dispatch"]["status"], "failed")
        status = (self.root / ".ai-collab/runs/run-visible-fail/status.md").read_text(encoding="utf-8")
        self.assertIn("status: dispatch-failed", status)

    def test_non_director_cannot_assign(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-3",
            "--goal",
            "Coordinate work",
            "--director",
            "codex",
            "--agents",
            "opencode",
        )
        self.run_cli(
            "add-task",
            "--run-id",
            "run-3",
            "--actor",
            "codex",
            "--task-id",
            "task-1",
            "--title",
            "Do work",
            "--owner",
            "opencode",
            "--description",
            "Do bounded work.",
        )
        with self.assertRaises(SystemExit):
            self.run_cli("assign", "--run-id", "run-3", "--actor", "opencode", "--task-id", "task-1")

    def test_assign_refuses_to_overwrite_active_inbox(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-4",
            "--goal",
            "Coordinate work",
            "--director",
            "codex",
            "--agents",
            "opencode",
        )
        (self.root / ".ai-collab/inbox-opencode.md").write_text(
            """\
---
to: opencode
status: running
---
active work
""",
            encoding="utf-8",
        )
        self.run_cli(
            "add-task",
            "--run-id",
            "run-4",
            "--actor",
            "codex",
            "--task-id",
            "task-2",
            "--title",
            "Do work",
            "--owner",
            "opencode",
            "--description",
            "Do bounded work.",
        )
        with self.assertRaises(SystemExit):
            self.run_cli("assign", "--run-id", "run-4", "--actor", "codex", "--task-id", "task-2")

    def test_finalize_requires_terminal_tasks_and_validation(self):
        self.run_cli(
            "init",
            "--run-id",
            "run-5",
            "--goal",
            "Coordinate work",
            "--director",
            "codex",
            "--agents",
            "opencode",
        )
        self.run_cli(
            "add-task",
            "--run-id",
            "run-5",
            "--actor",
            "codex",
            "--task-id",
            "task-3",
            "--title",
            "Do work",
            "--owner",
            "opencode",
            "--description",
            "Do bounded work.",
        )
        with self.assertRaises(SystemExit):
            self.run_cli(
                "finalize",
                "--run-id",
                "run-5",
                "--actor",
                "codex",
                "--summary",
                "Done",
                "--validation",
                "tests passed",
            )
        self.run_cli(
            "set-task",
            "--run-id",
            "run-5",
            "--actor",
            "codex",
            "--task-id",
            "task-3",
            "--status",
            "done",
            "--summary",
            "Completed by worker.",
        )
        with self.assertRaises(SystemExit):
            self.run_cli("finalize", "--run-id", "run-5", "--actor", "codex", "--summary", "Done")
        self.run_cli(
            "finalize",
            "--run-id",
            "run-5",
            "--actor",
            "codex",
            "--summary",
            "Done",
            "--validation",
            "tests passed",
        )
        director = json.loads((self.root / ".ai-collab/runs/run-5/director.json").read_text(encoding="utf-8"))
        self.assertEqual(director["director_lock"], "released")
        self.assertTrue((self.root / ".ai-collab/runs/run-5/final-summary.md").exists())


if __name__ == "__main__":
    unittest.main()
