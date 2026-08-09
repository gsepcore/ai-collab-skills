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
                "AI_COLLAB_OBSERVER_SEMANTIC_OCR",
                "AI_COLLAB_OBSERVER_TESSERACT_BIN",
                "AI_COLLAB_PROJECT_ALIASES",
                "AI_COLLAB_IDE_BRIDGE_DIR",
            )
        }
        for key in self._env:
            os.environ.pop(key, None)
        os.environ["AI_COLLAB_IDE_BRIDGE_DIR"] = str(self.root / "empty-bridges")
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

    def test_observer_reports_open_discussions(self):
        discussion_dir = self.collab / "discussions"
        discussion_dir.mkdir()
        (discussion_dir / "discussion-20260616-api.md").write_text(
            """---
schema: ai-collab.thread.v2
thread: discussion-20260616-api
kind: discussion
topic: API boundary
project: demo
participants: codex, opencode
status: open
updated: 2026-06-16T12:00:00Z
---
## 2026-06-16T12:00:00Z -- codex

type: question
to: opencode

@opencode should we use an adapter?

---
""",
            encoding="utf-8",
        )

        summary = _mod.observe_project(self.collab, now=self.now, runner=self.fake_runner, system="Darwin")

        self.assertEqual(summary["conversations"][0]["topic"], "API boundary")
        self.assertEqual(summary["conversations"][0]["latest"]["author"], "codex")
        snapshot = json.loads((self.collab / "live" / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["conversations"][0]["thread"], "discussion-20260616-api")
        self.assertEqual(
            snapshot["thread_mentions"][0]["path"],
            str((discussion_dir / "discussion-20260616-api.md").resolve()),
        )

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

    def test_opencode_process_matches_global_project_by_session_directory(self):
        def getter(url, **kwargs):
            if url.endswith("/project/current"):
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_root", "directory": str(self.root)}]
            return 404, ""

        matched = _mod.opencode_process_matches_project("opencode --port 52721", self.root, getter=getter)

        self.assertTrue(matched)

    def test_opencode_process_rejects_other_project_sessions(self):
        other = self.root.parent / "other-project"

        def getter(url, **kwargs):
            if url.endswith("/project/current"):
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_other", "directory": str(other)}]
            return 404, ""

        matched = _mod.opencode_process_matches_project("opencode --port 52721", self.root, getter=getter)

        self.assertFalse(matched)

    def test_project_identity_uses_git_remote_and_aliases(self):
        os.environ["AI_COLLAB_PROJECT_ALIASES"] = "collab eyes;workspace-alpha"
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            """[remote "origin"]
  url = git@github.com:gsepcore/ai-collab-skills.git
""",
            encoding="utf-8",
        )

        identity = _mod.project_identity(self.root)

        self.assertIn("ai-collab-skills", identity["signals"])
        self.assertIn("collab eyes", identity["signals"])
        self.assertIn("workspace-alpha", identity["signals"])
        self.assertEqual(identity["git_remotes"], ["git@github.com:gsepcore/ai-collab-skills.git"])

    def test_process_snapshot_matches_project_by_cwd(self):
        def runner(command, **kwargs):
            if command[:2] == ["ps", "-axo"]:
                return Completed(stdout="333 00:03 codex\n")
            if command[:2] == ["lsof", "-a"]:
                return Completed(stdout=f"p333\nn{self.root}\n")
            return Completed(returncode=1)

        processes = _mod.process_snapshot(self.root, ["codex"], runner=runner, system="Darwin")

        self.assertEqual(len(processes["codex"]), 1)
        self.assertEqual(processes["codex"][0]["pid"], "333")

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

    def test_screenshots_do_not_require_active_agents_by_default(self):
        (self.collab / "inbox-opencode.md").unlink()
        (self.collab / "opencode-20260615-115900.md").unlink()
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["ps", "-axo"]:
                return Completed(stdout="")
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(summary["active_agents"], [])
        self.assertEqual(summary["screenshot"]["status"], "captured")
        self.assertEqual(len(calls), 1)

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

    def test_screenshot_failure_updates_last_marker(self):
        calls = []

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t0,0,2048,1280\n")
            if command[:2] == ["screencapture", "-x"]:
                calls.append(command)
                return Completed(returncode=1, stderr="could not create image from display")
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")
        marker = json.loads((self.collab / "live" / "screenshots" / ".last.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["screenshot"]["status"], "failed")
        self.assertEqual(marker["status"], "failed")
        self.assertEqual(marker["reason"], "could not create image from display")
        self.assertEqual(marker["window"]["title"], self.root.name)
        self.assertEqual(len(calls), 2)

    def test_health_and_semantic_files_are_written(self):
        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")
        health = json.loads((self.collab / "live" / "health.json").read_text(encoding="utf-8"))
        semantic = json.loads(Path(summary["screenshot"]["semantic"]["path"]).read_text(encoding="utf-8"))

        self.assertIn(health["overall"], {"ok", "degraded"})
        self.assertEqual(semantic["schema"], "ai-collab.vision.v1")
        self.assertTrue(summary["screenshot"]["semantic"]["path"].endswith(".semantic.json"))
        self.assertEqual(summary["health"]["path"], str((self.collab / "live" / "health.json").resolve()))

    def test_ocr_semantics_can_detect_visual_error(self):
        os.environ["AI_COLLAB_OBSERVER_TESSERACT_BIN"] = "/tmp/fake-tesseract"

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Antigravity IDE\t{self.root.name}\t10,20,800,600\n")
            if command[:2] == ["screencapture", "-x"]:
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            if command[0] == "/tmp/fake-tesseract":
                return Completed(stdout="Traceback: fatal error in build")
            return self.fake_runner(command, **kwargs)

        summary = _mod.observe_project(self.collab, now=self.now, runner=runner, system="Darwin")

        self.assertEqual(summary["screenshot"]["semantic"]["state"], "error")
        self.assertTrue(any(alert["type"] == "visual-error" for alert in summary["alerts"]))

    def test_visual_proof_requires_real_surfaces_and_maps_visible_port(self):
        os.environ["AI_COLLAB_OBSERVER_TESSERACT_BIN"] = "/tmp/fake-tesseract"
        tsv = "\n".join(
            [
                "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                "1\t1\t0\t0\t0\t0\t0\t0\t1800\t1100\t-1\t",
                "5\t1\t1\t1\t1\t1\t80\t30\t100\t20\t95\tOpenCode",
                "5\t1\t1\t1\t1\t2\t1450\t30\t80\t20\t96\tCodex",
            ]
        )

        def runner(command, **kwargs):
            if command[:2] == ["osascript", "-e"]:
                return Completed(stdout=f"Electron\t900\t{self.root.name} — Claude Code\t0,0,1800,1100\n")
            if command[:2] == ["screencapture", "-x"]:
                Path(command[-1]).write_bytes(b"png")
                return Completed()
            if command[0].endswith("sips"):
                return Completed()
            if command[0] == "/tmp/fake-tesseract":
                return Completed(stdout=tsv)
            if command[:2] == ["ps", "-p"]:
                return Completed(stdout="ttys021\n")
            if command[:2] == ["ps", "-axo"]:
                return Completed(stdout=f"25233 00:01 opencode --port 60466 --dir {self.root}\n")
            return self.fake_runner(command, **kwargs)

        original_inventory = _mod.ide_bridge_inventory
        _mod.ide_bridge_inventory = lambda root, runner: [
            {
                "owner": "ide-visible-bridge",
                "pid": 901,
                "port": 52678,
                "ide": "Antigravity IDE",
                "project_paths": [str(self.root)],
                "inventory_status": "ok",
                "terminals": [],
                "host_ancestor_pids": [900],
            }
        ]
        try:
            summary = _mod.observe_project(
                self.collab,
                now=self.now,
                runner=runner,
                system="Darwin",
                force_screenshot=True,
                visual_required_agents=["codex", "opencode"],
                screenshot_tag="test",
            )
        finally:
            _mod.ide_bridge_inventory = original_inventory
        roster = json.loads((self.collab / "live" / "visual-roster.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["visual_roster"]["status"], "verified")
        self.assertEqual(roster["screenshot"]["visible_agents"], ["codex", "opencode"])
        self.assertTrue(Path(roster["evidence_path"]).exists())
        codex = next(item for item in roster["agents"] if item["agent"] == "codex")
        self.assertEqual(codex["host_surface"]["host_pid"], "900")
        self.assertEqual(
            codex["host_surface"]["evidence_standard"],
            "registered-shared-project-host+position-bound-top-band-label",
        )
        self.assertTrue(codex["host_surface"]["registered_host_match"])
        self.assertIsNone(codex["host_surface"]["agent_owned_port"])
        opencode = next(item for item in roster["agents"] if item["agent"] == "opencode")
        self.assertEqual(opencode["owned_ports"][0]["port"], 60466)
        self.assertEqual(opencode["processes"][0]["tty"], "ttys021")

    def test_native_chat_rejects_unregistered_electron_host(self):
        live = self.collab / "live"
        screenshots = live / "screenshots"
        screenshots.mkdir(parents=True)
        image = screenshots / "team.png"
        image.write_bytes(b"png")
        semantic_path = screenshots / "team.semantic.json"
        semantic_path.write_text(
            json.dumps(
                {
                    "project_match": True,
                    "visible_agents": ["codex"],
                    "agent_visual_hits": {"codex": [{"text": "Codex", "position": "right"}]},
                    "ocr": {"status": "ok"},
                }
            ),
            encoding="utf-8",
        )
        screenshot = {
            "status": "captured",
            "path": str(image),
            "captured_at": "2026-06-15T12:00:00Z",
            "window": {"app": "Electron", "pid": "900", "title": "demo — opencode"},
            "semantic": {"path": str(semantic_path)},
        }
        original_inventory = _mod.ide_bridge_inventory
        _mod.ide_bridge_inventory = lambda root, runner: [
            {
                "owner": "ide-visible-bridge",
                "pid": 901,
                "port": 52678,
                "ide": "Antigravity IDE",
                "project_paths": [str(self.root)],
                "inventory_status": "ok",
                "terminals": [],
                "host_ancestor_pids": [777],
            }
        ]
        try:
            roster = _mod.build_visual_roster(
                root=self.root,
                live_dir=live,
                now=self.now,
                agents=["codex"],
                snapshots={"codex": {"processes": [], "latest_log": {}}},
                screenshot=screenshot,
                required_agents=["codex"],
                runner=self.fake_runner,
            )
        finally:
            _mod.ide_bridge_inventory = original_inventory

        self.assertEqual(roster["status"], "failed")
        self.assertEqual(roster["missing_or_unverified"], ["codex"])
        self.assertFalse(roster["agents"][0]["host_surface"]["registered_host_match"])

    def test_background_refresh_does_not_mutate_screenshot_bound_roster(self):
        live = self.collab / "live"
        screenshots = live / "screenshots"
        screenshots.mkdir(parents=True)
        image = screenshots / "team.png"
        image.write_bytes(b"png")
        semantic_path = screenshots / "team.semantic.json"
        semantic_path.write_text(
            json.dumps(
                {
                    "project_match": True,
                    "visible_agents": ["codex"],
                    "agent_visual_hits": {"codex": [{"text": "Codex", "position": "right"}]},
                    "ocr": {"status": "ok"},
                }
            ),
            encoding="utf-8",
        )
        screenshot = {
            "status": "captured",
            "path": str(image),
            "captured_at": "2026-06-15T12:00:00Z",
            "window": {"app": "Electron", "pid": "900", "title": "demo"},
            "semantic": {"path": str(semantic_path)},
        }
        original_inventory = _mod.ide_bridge_inventory
        _mod.ide_bridge_inventory = lambda root, runner: [
            {
                "owner": "ide-visible-bridge",
                "pid": 901,
                "port": 52678,
                "ide": "Antigravity IDE",
                "project_paths": [str(self.root)],
                "inventory_status": "ok",
                "terminals": [],
                "host_ancestor_pids": [900],
            }
        ]
        try:
            initial = _mod.build_visual_roster(
                root=self.root,
                live_dir=live,
                now=self.now,
                agents=["codex"],
                snapshots={"codex": {"processes": [], "latest_log": {}}},
                screenshot=screenshot,
                required_agents=["codex"],
                runner=self.fake_runner,
            )
            (screenshots / ".last.json").write_text(json.dumps(screenshot), encoding="utf-8")
            immutable = Path(initial["evidence_path"])
            original_bytes = immutable.read_bytes()

            refreshed = _mod.build_visual_roster(
                root=self.root,
                live_dir=live,
                now=self.now,
                agents=["codex"],
                snapshots={"codex": {"processes": [], "latest_log": {}}},
                screenshot=None,
                required_agents=None,
                runner=self.fake_runner,
            )
        finally:
            _mod.ide_bridge_inventory = original_inventory

        self.assertEqual(initial["required_agents"], ["codex"])
        self.assertEqual(refreshed["required_agents"], [])
        self.assertEqual(immutable.read_bytes(), original_bytes)
        self.assertEqual(json.loads(immutable.read_text(encoding="utf-8"))["required_agents"], ["codex"])

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
