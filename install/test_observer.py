#!/usr/bin/env python3
"""
Tests for ai-collab-observer.py
Run with: python3 install/test_observer.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_observer",
    Path(__file__).parent / "ai-collab-observer.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_observer"] = _mod
_spec.loader.exec_module(_mod)


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestObserver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collab = self.root / ".ai-collab"
        self.collab.mkdir()
        self.now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self._env = {
            key: os.environ.get(key)
            for key in (
                "AI_COLLAB_OBSERVER",
                "AI_COLLAB_OBSERVER_SCREENSHOTS",
                "AI_COLLAB_OBSERVER_SCREENSHOT_MODE",
                "AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL",
                "AI_COLLAB_OBSERVER_SCREENSHOT_ACTIVE_ONLY",
            )
        }
        for key in self._env:
            os.environ.pop(key, None)
        (self.collab / "agents.json").write_text(
            json.dumps(
                {
                    "schema": "ai-collab.agents.v1",
                    "project": "demo",
                    "agents": [{"agent": "opencode"}, {"agent": "codex"}],
                }
            ),
            encoding="utf-8",
        )
        (self.collab / "inbox-opencode.md").write_text(
            """---
from: Claude Code
to: opencode
task_id: task-123
status: claimed
claimed_at: 2026-06-15T11:59:00Z
updated: 2026-06-15T11:59:00Z
---

## Task
Fix the observer.
""",
            encoding="utf-8",
        )
        (self.collab / "opencode-20260615-115900.md").write_text(
            """---
ai: OpenCode
agent: opencode
updated: 2026-06-15T11:59:00Z
---

## Working On
Implementing live observation.

## Files Modified This Session
- `install/ai-collab-observer.py` - observer implementation

## Do Not Touch (Avoid Conflicts)
- `README.md` - being edited by opencode
""",
            encoding="utf-8",
        )

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def fake_runner(self, command, **kwargs):
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return Completed(stdout=str(self.root) + "\n")
        if command[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return Completed(stdout="main\n")
        if command[:3] == ["git", "rev-parse", "--short"]:
            return Completed(stdout="abc123\n")
        if command[:3] == ["git", "status", "--porcelain=v1"]:
            return Completed(stdout=" M install/ai-collab-observer.py\n M README.md\n")
        if command[:2] == ["ps", "-axo"]:
            return Completed(stdout=f"123 00:01 opencode run tests --dir {self.root}\n")
        return Completed(returncode=1, stderr="unexpected command")

    def test_observer_writes_semantic_snapshot_and_alerts(self):
        summary = _mod.observe_project(self.collab, now=self.now, runner=self.fake_runner, system="Darwin")

        self.assertEqual(summary["project"], self.root.name)
        self.assertIn("opencode", summary["active_agents"])
        snapshot = json.loads((self.collab / "live" / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["current_task_id"], "task-123")
        self.assertIn("opencode run tests", snapshot["current_command"])
        self.assertEqual(snapshot["git"]["dirty_files"][0]["path"], "install/ai-collab-observer.py")
        self.assertTrue(any(alert["type"] == "dirty-locked-file" for alert in summary["alerts"]))
        self.assertTrue((self.collab / "live" / "summary.json").exists())
        self.assertTrue((self.collab / "live" / "opencode.events.jsonl").exists())

    def test_agent_reported_command_takes_precedence(self):
        live = self.collab / "live"
        live.mkdir()
        (live / "codex.agent.json").write_text(
            json.dumps(
                {
                    "agent": "codex",
                    "updated": "2026-06-15T12:00:00Z",
                    "phase": "command",
                    "current_command": "python3 -m unittest install/test_observer.py",
                    "task_id": "task-codex",
                }
            ),
            encoding="utf-8",
        )
        (live / "codex.agent.events.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-06-15T12:00:01Z",
                    "agent": "codex",
                    "event": "command",
                    "command": "python3 -m unittest install/test_observer.py",
                    "exit_code": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        _mod.observe_project(self.collab, now=self.now, runner=self.fake_runner, system="Darwin")

        snapshot = json.loads((live / "codex.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["status"], "running")
        self.assertEqual(snapshot["current_task_id"], "task-codex")
        self.assertEqual(snapshot["current_command"], "python3 -m unittest install/test_observer.py")
        self.assertEqual(snapshot["reported_events"][0]["event"], "command")

    def test_process_snapshot_filters_processes_from_other_projects(self):
        other = self.root.parent / "other-project"

        def runner(command, **kwargs):
            if command[:2] == ["ps", "-axo"]:
                return Completed(
                    stdout=(
                        f"111 00:01 opencode --port 111 --dir {other}\n"
                        "/Applications/Codex.app/Contents/MacOS/Codex\n"
                        f"222 00:02 opencode --port 222 --dir {self.root}\n"
                    )
                )
            return Completed(returncode=1)

        processes = _mod.process_snapshot(self.root, ["opencode", "codex"], runner=runner)

        self.assertEqual(len(processes["opencode"]), 1)
        self.assertIn(str(self.root), processes["opencode"][0]["command"])
        self.assertEqual(processes["codex"], [])

    def test_screenshots_enabled_by_default(self):
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(summary["screenshot"]["status"], "captured")
        self.assertEqual(len(calls), 1)
        self.assertIn("-R", calls[0])
        self.assertIn("10,20,800,600", calls[0])
        self.assertTrue(Path(summary["screenshot"]["path"]).exists())

    def test_project_screenshot_skips_other_project_window(self):
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout="Antigravity IDE\tFounder Engineering\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                return Completed()
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(summary["screenshot"]["status"], "skipped")
        self.assertIn("no visible window matched", summary["screenshot"]["reason"])
        self.assertEqual(calls, [])

    def test_project_screenshot_falls_back_after_project_window_match(self):
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t0,0,2048,1280\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                if "-R" in command:
                    return Completed(returncode=1, stderr="could not create image from rect")
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(summary["screenshot"]["status"], "captured")
        self.assertEqual(summary["screenshot"]["fallback"], "screen-after-project-window-match")
        self.assertEqual(len(calls), 2)

    def test_screenshots_can_be_disabled_with_env(self):
        os.environ["AI_COLLAB_OBSERVER_SCREENSHOTS"] = "0"

        _mod.observe_project(self.collab, now=self.now, runner=self.fake_runner, system="Darwin")

        self.assertFalse((self.collab / "live" / "screenshots").exists())

    def test_screenshots_are_throttled(self):
        os.environ["AI_COLLAB_OBSERVER_SCREENSHOT_MODE"] = "screen"
        os.environ["AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL"] = "999"
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            return self.fake_runner(command, **kwargs)

        first = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")
        second = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(first["screenshot"]["status"], "captured")
        self.assertEqual(second["screenshot"], {})
        self.assertEqual(len(calls), 1)
        self.assertTrue(Path(first["screenshot"]["path"]).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
