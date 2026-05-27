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

    def tearDown(self):
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
