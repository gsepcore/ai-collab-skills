#!/usr/bin/env python3
"""
Tests for ai-collab-wakeup.py
Run with: python3 -m pytest install/test_wakeup.py -v
       or: python3 install/test_wakeup.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_wakeup",
    Path(__file__).parent / "ai-collab-wakeup.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_wakeup"] = _mod
_spec.loader.exec_module(_mod)

parse_frontmatter = _mod.parse_frontmatter
render_frontmatter = _mod.render_frontmatter
process_inbox = _mod.process_inbox
process_thread = _mod.process_thread
append_thread_message = _mod.append_thread_message
run_wakeup_adapter = _mod.run_wakeup_adapter
run_codex_acp_adapter = _mod.run_codex_acp_adapter
run_acp_adapter = _mod.run_acp_adapter
run_ide_terminal_visible_adapter = _mod.run_ide_terminal_visible_adapter
prepare_ide_terminal_visible_surface = _mod.prepare_ide_terminal_visible_surface
prepare_antigravity_chat_surface = _mod.prepare_antigravity_chat_surface
run_ide_native_chat_adapter = _mod.run_ide_native_chat_adapter


SAMPLE_INBOX = """\
---
from: Claude Code
to: codex
task_id: task-123
priority: high
updated: 2026-05-12T12:00:00Z
status: unread
attempts: 0
last_attempt:
claimed_by:
claimed_at:
done_at:
---

## Task
Do the thing.
"""


class TestFrontmatter(unittest.TestCase):
    def test_parse_frontmatter(self):
        meta, body = parse_frontmatter(SAMPLE_INBOX)
        self.assertEqual(meta["task_id"], "task-123")
        self.assertEqual(meta["status"], "unread")
        self.assertIn("Do the thing", body)

    def test_render_frontmatter(self):
        meta = {"status": "unread", "attempts": "1"}
        text = render_frontmatter(meta, "body")
        self.assertIn("status: unread", text)
        self.assertTrue(text.endswith("body"))


class TestProcessInbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inbox = self.root / ".ai-collab" / "inbox-codex.md"
        self.inbox.parent.mkdir()
        self.events = self.root / "events.json"
        self.state = self.root / "state.json"
        self.log = self.root / "wakeup.log"
        self.now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write_inbox(self, content=SAMPLE_INBOX):
        self.inbox.write_text(content, encoding="utf-8")

    def read_meta(self):
        meta, _ = parse_frontmatter(self.inbox.read_text(encoding="utf-8"))
        return meta

    def test_notify_only_produces_wake_event_without_consuming_attempt(self):
        os.environ["AI_COLLAB_WAKEUP_ADAPTER"] = "notify-only"
        self.write_inbox()
        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "notified")
        events = json.loads(self.events.read_text(encoding="utf-8"))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["task_id"], "task-123")
        self.assertEqual(events[0]["target_slug"], "codex")
        self.assertEqual(events[1]["event_type"], "adapter_result")
        self.assertEqual(events[1]["adapter_result"]["adapter_name"], "notify-only")

        meta = self.read_meta()
        self.assertEqual(meta["status"], "unread")
        self.assertEqual(meta["attempts"], "0")
        self.assertEqual(meta["last_attempt"], "")

    def test_successful_adapter_records_visible_dispatch_without_faking_agent_claim(self):
        self.write_inbox()
        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )

        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(result["adapter_result"]["status"], "success")
        meta = self.read_meta()
        self.assertEqual(meta["status"], "unread")
        self.assertEqual(meta["claimed_by"], "")
        self.assertEqual(meta["claimed_at"], "")
        self.assertEqual(meta["visible_adapter"], "mock-success")
        self.assertEqual(meta["visible_dispatched_at"], "2026-05-12T12:00:00Z")

    def test_capability_grace_waits_before_visible_inbox_dispatch(self):
        self.inbox = self.inbox.parent / "inbox-opencode.md"
        (self.inbox.parent / "capabilities.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "agent": "opencode",
                            "visible": {"adapter": "mock-success", "native_chat_only": False},
                            "delivery": {"primary": "internal-inbox", "fallback": "visible-chat"},
                            "wake_policy": {"internal_grace_seconds": 15, "sleep_threshold_seconds": 60},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.write_inbox(SAMPLE_INBOX.replace("to: codex", "to: opencode"))

        waiting = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )
        dispatched = process_inbox(
            self.inbox,
            "gsep",
            now=self.now + timedelta(seconds=16),
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )

        self.assertEqual(waiting["action"], "internal-grace")
        self.assertEqual(waiting["grace_seconds"], 15)
        self.assertEqual(dispatched["action"], "dispatched")

    def test_codex_bypasses_internal_grace_and_uses_visible_chat_immediately(self):
        (self.inbox.parent / "capabilities.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "agent": "codex",
                            "visible": {"adapter": "mock-success", "native_chat_only": True},
                            "delivery": {"primary": "visible-chat", "fallback": "visible-chat"},
                            "wake_policy": {"internal_grace_seconds": 30, "sleep_threshold_seconds": 60},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.write_inbox()

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )

        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(result["adapter_result"]["status"], "success")

    def test_done_produces_no_event(self):
        self.write_inbox(SAMPLE_INBOX.replace("status: unread", "status: done"))
        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "ignored")
        self.assertFalse(self.events.exists())

    def test_done_inbox_closes_existing_thread(self):
        self.write_inbox(SAMPLE_INBOX.replace("status: unread", "status: done"))
        thread = self.inbox.parent / "thread-task-123.md"
        append_thread_message(
            thread,
            task_id="task-123",
            project="gsep",
            inbox_name="inbox-codex.md",
            author_slug="codex",
            message="Work is ready.",
            now=self.now,
        )

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "closed-thread")
        meta, body = parse_frontmatter(thread.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "closed")
        self.assertIn("Task closed: status=done", body)

    def test_backoff_prevents_second_attempt_too_soon(self):
        content = SAMPLE_INBOX.replace("attempts: 0", "attempts: 1").replace(
            "last_attempt:", "last_attempt: 2026-05-12T11:59:58Z"
        )
        self.write_inbox(content)

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-failed",
        )

        self.assertEqual(result["action"], "backoff")
        self.assertFalse(self.events.exists())

    def test_retry_after_backoff_creates_next_attempt(self):
        content = SAMPLE_INBOX.replace("attempts: 0", "attempts: 1").replace(
            "last_attempt:", "last_attempt: 2026-05-12T11:59:50Z"
        )
        self.write_inbox(content)

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-failed",
        )

        self.assertEqual(result["action"], "event")
        self.assertEqual(self.read_meta()["attempts"], "2")

    def test_max_attempt_marks_failed(self):
        content = SAMPLE_INBOX.replace("attempts: 0", "attempts: 2").replace(
            "last_attempt:", "last_attempt: 2026-05-12T11:59:00Z"
        )
        self.write_inbox(content)

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-failed",
            max_attempts=3,
        )

        self.assertEqual(result["action"], "failed")
        meta = self.read_meta()
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["attempts"], "3")
        self.assertEqual(meta["done_at"], "2026-05-12T12:00:00Z")

    def test_already_at_max_attempts_marks_failed_without_event(self):
        content = SAMPLE_INBOX.replace("attempts: 0", "attempts: 3")
        self.write_inbox(content)

        result = process_inbox(
            self.inbox,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            max_attempts=3,
        )

        self.assertEqual(result["action"], "failed")
        self.assertFalse(self.events.exists())
        self.assertEqual(self.read_meta()["status"], "failed")


class TestAdapters(unittest.TestCase):
    def setUp(self):
        self._env = {
            "AI_COLLAB_WAKEUP_CLI_PROJECTS": os.environ.get("AI_COLLAB_WAKEUP_CLI_PROJECTS"),
            "AI_COLLAB_WAKEUP_CLI_TARGETS": os.environ.get("AI_COLLAB_WAKEUP_CLI_TARGETS"),
            "AI_COLLAB_WAKEUP_VISIBLE_TARGETS": os.environ.get("AI_COLLAB_WAKEUP_VISIBLE_TARGETS"),
            "AI_COLLAB_WAKEUP_DRY_RUN": os.environ.get("AI_COLLAB_WAKEUP_DRY_RUN"),
            "AI_COLLAB_OPENCODE_PORTS": os.environ.get("AI_COLLAB_OPENCODE_PORTS"),
            "AI_COLLAB_OPENCODE_SYNTHETIC": os.environ.get("AI_COLLAB_OPENCODE_SYNTHETIC"),
            "AI_COLLAB_OPENCODE_BIN": os.environ.get("AI_COLLAB_OPENCODE_BIN"),
            "AI_COLLAB_CODEX_BIN": os.environ.get("AI_COLLAB_CODEX_BIN"),
            "AI_COLLAB_CLAUDE_BIN": os.environ.get("AI_COLLAB_CLAUDE_BIN"),
            "AI_COLLAB_ANTIGRAVITY_BIN": os.environ.get("AI_COLLAB_ANTIGRAVITY_BIN"),
            "AI_COLLAB_ANTIGRAVITY_MODE": os.environ.get("AI_COLLAB_ANTIGRAVITY_MODE"),
            "AI_COLLAB_CODEX_ACP_COMMAND": os.environ.get("AI_COLLAB_CODEX_ACP_COMMAND"),
        }
        for key in self._env:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_notify_only_is_degraded(self):
        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "codex",
                "inbox_path": "/tmp/project/.ai-collab/inbox-codex.md",
                "task_id": "task-123",
                "synthetic_prompt": "read inbox",
            },
            mode="notify-only",
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "notify-only")

    def test_unknown_adapter_fails(self):
        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "codex",
                "inbox_path": "/tmp/project/.ai-collab/inbox-codex.md",
                "task_id": "task-123",
                "synthetic_prompt": "read inbox",
            },
            mode="unknown",
        )

        self.assertEqual(result["status"], "failed")

    def test_claude_visible_uses_exact_project_ide_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "project"
            root.mkdir()
            registry = Path(d) / "bridges"
            registry.mkdir()
            (registry / "123.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "port": 43123,
                        "token": "secret-token",
                        "project_paths": [str(root)],
                        "ide": "Antigravity IDE",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def poster(url, payload, *, timeout, headers=None):
                calls.append((url, payload, timeout, headers))
                return 200, json.dumps({"status": "success"})

            result = run_ide_terminal_visible_adapter(
                {
                    "project_path": str(root),
                    "target_slug": "claude-code",
                    "inbox_path": str(root / ".ai-collab/inbox-claude-code.md"),
                    "source_path": str(root / ".ai-collab/thread-kickoff.md"),
                    "task_id": "kickoff",
                    "synthetic_prompt": "Read and answer the visible team thread.",
                },
                timeout=20,
                poster=poster,
                registry_dir=registry,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["adapter_name"], "ide-terminal-visible")
            self.assertEqual(calls[0][0], "http://127.0.0.1:43123/terminal/send")
            self.assertEqual(calls[0][1]["target_slug"], "claude-code")
            self.assertEqual(calls[0][3], {"Authorization": "Bearer secret-token"})

    def test_native_claude_visible_uses_registered_session_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "project"
            (root / ".ai-collab" / "live").mkdir(parents=True)
            registry = Path(d) / "bridges"
            registry.mkdir()
            (registry / "123.json").write_text(
                json.dumps({
                    "pid": os.getpid(), "port": 43124, "token": "secret-token",
                    "project_paths": [str(root)], "ide": "Antigravity IDE",
                }),
                encoding="utf-8",
            )
            calls = []

            def poster(url, payload, *, timeout, headers=None):
                calls.append((url, payload, timeout, headers))
                return 200, json.dumps({
                    "status": "success", "agent_id": "agt_claude_ide", "session_id": "ses_native_1",
                    "surface_id": "vscode-native:claude-code-ide:abc",
                })

            result = run_ide_native_chat_adapter(
                {
                    "project_path": str(root), "target_slug": "claude-code-ide",
                    "inbox_path": str(root / ".ai-collab/inbox-claude-code-ide.md"),
                    "source_path": str(root / ".ai-collab/thread-wake.md"), "task_id": "wake",
                    "synthetic_prompt": "Read and answer your internal inbox.",
                },
                timeout=20, poster=poster, registry_dir=registry,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["session_id"], "ses_native_1")
            self.assertEqual(result["agent_id"], "agt_claude_ide")
            self.assertEqual(calls[0][0], "http://127.0.0.1:43124/native/send")
            self.assertEqual(calls[0][1]["target_slug"], "claude-code-ide")
            evidence = json.loads((root / ".ai-collab/live/claude-code-ide.visible.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["session_id"], "ses_native_1")

    def test_claude_visible_refuses_bridge_for_other_project(self):
        with tempfile.TemporaryDirectory() as d:
            registry = Path(d) / "bridges"
            registry.mkdir()
            (registry / "123.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "port": 43123,
                        "token": "secret-token",
                        "project_paths": [str(Path(d) / "other")],
                    }
                ),
                encoding="utf-8",
            )
            result = run_ide_terminal_visible_adapter(
                {
                    "project_path": str(Path(d) / "project"),
                    "target_slug": "claude-code",
                    "inbox_path": "",
                    "task_id": "kickoff",
                    "synthetic_prompt": "hello",
                },
                timeout=20,
                registry_dir=registry,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("matched this project", result["message"])

    def test_executable_for_checks_launchd_fallback_paths(self):
        old_path = _mod.shutil.which
        old_dirs = _mod.FALLBACK_BIN_DIRS
        old_globs = _mod.FALLBACK_BIN_GLOBS
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "opencode"
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
            try:
                _mod.shutil.which = lambda name: None
                _mod.FALLBACK_BIN_DIRS = (Path(d),)
                _mod.FALLBACK_BIN_GLOBS = ()
                self.assertEqual(_mod.executable_for("opencode"), str(exe))
            finally:
                _mod.shutil.which = old_path
                _mod.FALLBACK_BIN_DIRS = old_dirs
                _mod.FALLBACK_BIN_GLOBS = old_globs

    def test_executable_for_checks_packaged_extension_binary_dirs(self):
        old_path = _mod.shutil.which
        old_dirs = _mod.FALLBACK_BIN_DIRS
        old_globs = _mod.FALLBACK_BIN_GLOBS
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            packaged = root / "moonshot.kimi" / "bin" / "kimi"
            packaged.mkdir(parents=True)
            exe = packaged / "kimi"
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
            try:
                _mod.shutil.which = lambda name: None
                _mod.FALLBACK_BIN_DIRS = ()
                _mod.FALLBACK_BIN_GLOBS = (str(root / "*" / "bin" / "*"),)
                self.assertEqual(_mod.executable_for("kimi"), str(exe))
            finally:
                _mod.shutil.which = old_path
                _mod.FALLBACK_BIN_DIRS = old_dirs
                _mod.FALLBACK_BIN_GLOBS = old_globs

    def test_cli_adapter_success_uses_runner(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        old_path = _mod.shutil.which
        try:
            _mod.shutil.which = lambda name: f"/usr/bin/{name}"
            result = run_wakeup_adapter(
                {
                    "project_path": "/tmp/project",
                    "target_slug": "opencode",
                    "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read inbox",
                },
                mode="cli",
                runner=fake_runner,
            )
        finally:
            _mod.shutil.which = old_path

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls[0][0][:3], ["/usr/bin/opencode", "run", "read inbox"])
        self.assertIn("--dir", calls[0][0])
        self.assertIn("--file", calls[0][0])

    def test_cli_adapter_blocks_project_not_in_allowlist(self):
        # When operator restricts the allowlist explicitly, projects outside
        # the list must be blocked.
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/some/other/project"
        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read inbox",
            },
            mode="cli",
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "cli-guardrail")
        self.assertIn("AI_COLLAB_WAKEUP_CLI_PROJECTS", result["message"])

    def test_cli_adapter_allows_project_when_allowlist_unset(self):
        # Active-by-default: when no allowlist is configured, any project with
        # a .ai-collab/ directory is allowed. The user opted in by installing
        # the skill into the project.
        os.environ["AI_COLLAB_OPENCODE_BIN"] = "/usr/bin/opencode"
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read inbox",
            },
            mode="cli",
            runner=fake_runner,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "cli")
        self.assertEqual(calls[0][:3], ["/usr/bin/opencode", "run", "read inbox"])

    def test_cli_adapter_dry_run_does_not_execute(self):
        os.environ["AI_COLLAB_WAKEUP_DRY_RUN"] = "1"
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            raise AssertionError("runner should not execute in dry-run")

        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read inbox",
            },
            mode="cli",
            runner=fake_runner,
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "cli-dry-run")
        self.assertEqual(calls, [])

    def test_discover_opencode_ports_from_env_and_ps(self):
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stdout = "node /x/opencode --port 23456\n"
                stderr = ""

            return Completed()

        self.assertEqual(_mod.discover_opencode_ports(runner=fake_runner), [12345, 23456])

    def test_opencode_visible_posts_visible_prompt_by_default(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/tmp/project"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_test", "directory": "/tmp/project"}]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "opencode-visible")
        self.assertEqual(posts[0][0], "http://127.0.0.1:12345/tui/clear-prompt?directory=%2Ftmp%2Fproject")
        self.assertEqual(posts[1][0], "http://127.0.0.1:12345/tui/append-prompt?directory=%2Ftmp%2Fproject")
        self.assertEqual(posts[1][1]["text"], "read visible inbox")
        self.assertEqual(posts[2][0], "http://127.0.0.1:12345/tui/submit-prompt?directory=%2Ftmp%2Fproject")

    def test_kilo_visible_posts_visible_prompt_with_auth_header(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_KILO_PORTS"] = "34567"
        os.environ["AI_COLLAB_KILO_BASIC_AUTH"] = "user:pass"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        result = _mod.run_kilo_visible_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "kilo",
                "inbox_path": "/tmp/project/.ai-collab/inbox-kilo.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            poster=fake_poster,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "kilo-visible")
        self.assertEqual(posts[0][0], "http://127.0.0.1:34567/tui/clear-prompt?directory=%2Ftmp%2Fproject")
        self.assertEqual(posts[1][1]["text"], "read visible inbox")
        self.assertIn("Authorization", posts[0][2]["headers"])

    def test_kilo_visible_reports_auth_hint_on_401(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_KILO_PORTS"] = "34567"

        def fake_poster(url, payload, **kwargs):
            return 401, "Unauthorized"

        result = _mod.run_kilo_visible_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "kilo",
                "inbox_path": "/tmp/project/.ai-collab/inbox-kilo.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            poster=fake_poster,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("AI_COLLAB_KILO_BASIC_AUTH", result["message"])

    def test_opencode_visible_synthetic_mode_is_opt_in(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"
        os.environ["AI_COLLAB_OPENCODE_SYNTHETIC"] = "1"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/tmp/project"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_test", "directory": "/tmp/project"}]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "opencode-synthetic")
        self.assertEqual(posts[0][0], "http://127.0.0.1:12345/session/ses_test/prompt_async")
        self.assertEqual(posts[0][1]["parts"][0]["synthetic"], True)

    def test_visible_adapter_routes_opencode(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_WAKEUP_CLI_TARGETS"] = "codex"
        os.environ["AI_COLLAB_WAKEUP_VISIBLE_TARGETS"] = "opencode"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"

        old_post = _mod.post_json
        old_get = _mod.get_json
        try:
            _mod.post_json = lambda url, payload, **kwargs: (200, "ok")

            def fake_runner(command, **kwargs):
                class Completed:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Completed()

            def fake_get(url, **kwargs):
                if "/project/current" in url:
                    return 200, {"worktree": "/tmp/project"}
                if url.endswith("/session"):
                    return 200, [{"id": "ses_test", "directory": "/tmp/project"}]
                return 404, ""

            _mod.get_json = fake_get
            result = run_wakeup_adapter(
                {
                    "project_path": "/tmp/project",
                    "target_slug": "opencode",
                    "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read visible inbox",
                },
                mode="visible",
                runner=fake_runner,
            )
        finally:
            _mod.post_json = old_post
            _mod.get_json = old_get

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "opencode-visible")

    def test_visible_adapter_uses_opencode_tui_without_hidden_cli_duplicate(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"
        os.environ["AI_COLLAB_OPENCODE_BIN"] = "/usr/bin/opencode"
        posts = []
        calls = []

        def fake_runner(command, **kwargs):
            if command[:2] == ["ps", "ax"]:
                class PsCompleted:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return PsCompleted()
            calls.append(command)

            class Completed:
                returncode = 0
                stdout = "done"
                stderr = ""

            return Completed()

        old_post = _mod.post_json
        old_get = _mod.get_json
        try:
            _mod.post_json = lambda url, payload, **kwargs: posts.append((url, payload, kwargs)) or (200, "ok")

            def fake_get(url, **kwargs):
                if "/project/current" in url:
                    return 200, {"worktree": "/tmp/project"}
                if url.endswith("/session"):
                    return 200, [{"id": "ses_test", "directory": "/tmp/project"}]
                return 404, ""

            _mod.get_json = fake_get
            result = run_wakeup_adapter(
                {
                    "project_path": "/tmp/project",
                    "target_slug": "opencode",
                    "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read visible inbox",
                },
                mode="visible",
                runner=fake_runner,
            )
        finally:
            _mod.post_json = old_post
            _mod.get_json = old_get

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "opencode-visible")
        self.assertEqual(len(posts), 3)
        self.assertEqual(calls, [])

    def test_opencode_visible_prefers_port_matching_project(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/gsep"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111,22222"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "127.0.0.1:11111/project/current" in url:
                return 200, {"worktree": "/tmp/other-project"}
            if "127.0.0.1:22222/project/current" in url:
                return 200, {"worktree": "/tmp/gsep"}
            if "127.0.0.1:22222/session" in url:
                return 200, [{"id": "ses_gsep", "directory": "/tmp/gsep"}]
            if "127.0.0.1:11111/session" in url:
                return 200, [{"id": "ses_other", "directory": "/tmp/other-project"}]
            return 404, ""

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/gsep",
                "target_slug": "opencode",
                "inbox_path": "/tmp/gsep/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[0][0], "http://127.0.0.1:22222/tui/clear-prompt?directory=%2Ftmp%2Fgsep")
        self.assertEqual(posts[1][0], "http://127.0.0.1:22222/tui/append-prompt?directory=%2Ftmp%2Fgsep")
        self.assertEqual(posts[2][0], "http://127.0.0.1:22222/tui/submit-prompt?directory=%2Ftmp%2Fgsep")

    def test_opencode_visible_matches_global_project_by_session_directory(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/asistente-nuevo"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111,22222"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "127.0.0.1:11111/project/current" in url:
                return 200, {"worktree": "/"}
            if "127.0.0.1:11111/session" in url:
                return 200, [{"id": "ses_asistente", "directory": "/tmp/asistente-nuevo"}]
            if "127.0.0.1:22222/project/current" in url:
                return 200, {"worktree": "/tmp/other-project"}
            if "127.0.0.1:22222/session" in url:
                return 200, [{"id": "ses_other", "directory": "/tmp/other-project"}]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = (
                    "98765001 opencode --port 11111\n"
                    "98765002 opencode --port 22222\n"
                )
            elif command[:3] == ["lsof", "-a", "-p"]:
                pid = command[3]
                cwd = "/tmp/asistente-nuevo" if pid == "98765001" else "/tmp/other-project"
                completed.stdout = f"p{pid}\nfcwd\nn{cwd}\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\nopencode --port 22222\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/asistente-nuevo",
                "target_slug": "opencode",
                "inbox_path": "/tmp/asistente-nuevo/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(posts[0][0], "http://127.0.0.1:11111/tui/clear-prompt?directory=%2Ftmp%2Fasistente-nuevo")

    def test_opencode_visible_shared_global_history_uses_only_matching_process_cwd(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/non-git-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111,22222"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, [
                    {"id": "ses_target", "directory": "/tmp/non-git-project"},
                    {"id": "ses_other", "directory": "/tmp/other-project"},
                ]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = (
                    "98765001 opencode --port 11111\n"
                    "98765002 opencode --port 22222\n"
                )
            elif command[:3] == ["lsof", "-a", "-p"]:
                pid = command[3]
                cwd = "/tmp/other-project" if pid == "98765001" else "/tmp/non-git-project"
                completed.stdout = f"p{pid}\nfcwd\nn{cwd}\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\nopencode --port 22222\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/non-git-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/non-git-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(posts), 3)
        self.assertTrue(all("22222" in url for url, _, _ in posts))

    def test_opencode_visible_matches_global_non_git_project_by_process_cwd(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/non-git-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111,22222"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_other", "directory": "/tmp/other-project"}]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = (
                    "98765001 opencode --port 11111\n"
                    "98765002 opencode --port 22222\n"
                )
            elif command[:3] == ["lsof", "-a", "-p"]:
                pid = command[3]
                cwd = "/tmp/non-git-project" if pid == "98765001" else "/tmp/other-project"
                completed.stdout = f"p{pid}\nfcwd\nn{cwd}\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\nopencode --port 22222\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/non-git-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/non-git-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(posts), 3)
        self.assertEqual(
            posts[0][0],
            "http://127.0.0.1:11111/tui/clear-prompt?directory=%2Ftmp%2Fnon-git-project",
        )
        self.assertTrue(all("22222" not in url for url, _, _ in posts))

    def test_opencode_visible_cwd_fallback_refuses_cross_project_process(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/non-git-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, [{"id": "ses_other", "directory": "/tmp/other-project"}]
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = "98765002 opencode --port 11111\n"
            elif command[:3] == ["lsof", "-a", "-p"]:
                completed.stdout = "p98765002\nfcwd\nn/tmp/other-project\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/non-git-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/non-git-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("refusing cross-project wakeup", result["message"])
        self.assertIn("process.cwd=/tmp/other-project", result["message"])
        self.assertEqual(posts, [])

    def test_opencode_visible_cwd_fallback_does_not_override_explicit_other_project(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/non-git-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/tmp/other-project"}
            if url.endswith("/session"):
                return 200, []
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = "98765001 opencode --port 11111\n"
            elif command[:3] == ["lsof", "-a", "-p"]:
                completed.stdout = "p98765001\nfcwd\nn/tmp/non-git-project\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/non-git-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/non-git-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("refusing cross-project wakeup", result["message"])
        self.assertEqual(posts, [])

    def test_opencode_visible_cwd_fallback_refuses_ambiguous_same_project_processes(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/non-git-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111,22222"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "/project/current" in url:
                return 200, {"worktree": "/"}
            if url.endswith("/session"):
                return 200, []
            return 404, ""

        def fake_runner(command, **kwargs):
            class Completed:
                returncode = 0
                stderr = ""

            completed = Completed()
            if command[:4] == ["ps", "ax", "-o", "pid=,command="]:
                completed.stdout = (
                    "98765001 opencode --port 11111\n"
                    "98765002 opencode --port 22222\n"
                )
            elif command[:3] == ["lsof", "-a", "-p"]:
                completed.stdout = f"p{command[3]}\nfcwd\nn/tmp/non-git-project\n"
            elif command[:4] == ["ps", "ax", "-o", "command="]:
                completed.stdout = "opencode --port 11111\nopencode --port 22222\n"
            else:
                completed.returncode = 1
                completed.stdout = ""
            return completed

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/non-git-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/non-git-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            runner=fake_runner,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("refusing ambiguous wakeup", result["message"])
        self.assertEqual(posts, [])

    def test_opencode_visible_refuses_cross_project_fallback(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/new-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        def fake_getter(url, **kwargs):
            if "127.0.0.1:11111/project/current" in url:
                return 200, {"worktree": "/tmp/other-project"}
            if "127.0.0.1:11111/session" in url:
                return 200, [{"id": "ses_other", "directory": "/tmp/other-project"}]
            return 404, ""

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/new-project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/new-project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            poster=fake_poster,
            getter=fake_getter,
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("refusing cross-project wakeup", result["message"])
        self.assertIn("project/current.worktree=/tmp/other-project", result["message"])
        self.assertIn("session.directory=/tmp/other-project", result["message"])
        self.assertEqual(posts, [])

    def test_visible_adapter_never_falls_back_to_hidden_opencode_cli(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/new-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111"
        os.environ["AI_COLLAB_OPENCODE_BIN"] = "/usr/bin/opencode"
        calls = []

        def fake_runner(command, **kwargs):
            if command[:2] == ["ps", "ax"]:
                class PsCompleted:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return PsCompleted()
            calls.append(command)

            class Completed:
                returncode = 0
                stdout = "done"
                stderr = ""

            return Completed()

        old_post = _mod.post_json
        old_get = _mod.get_json
        try:
            _mod.post_json = lambda url, payload, **kwargs: (500, "panel refused")

            def fake_get(url, **kwargs):
                if "/project/current" in url:
                    return 200, {"worktree": "/tmp/new-project"}
                if url.endswith("/session"):
                    return 200, [{"id": "ses_new", "directory": "/tmp/new-project"}]
                return 404, ""

            _mod.get_json = fake_get
            result = run_wakeup_adapter(
                {
                    "project_path": "/tmp/new-project",
                    "target_slug": "opencode",
                    "inbox_path": "/tmp/new-project/.ai-collab/inbox-opencode.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read visible inbox",
                },
                mode="visible",
                runner=fake_runner,
            )
        finally:
            _mod.post_json = old_post
            _mod.get_json = old_get

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["adapter_name"], "opencode-visible")
        self.assertEqual(calls, [])

    def test_opencode_auto_explicitly_falls_back_to_cli(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/new-project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "11111"
        os.environ["AI_COLLAB_OPENCODE_BIN"] = "/usr/bin/opencode"
        calls = []

        def fake_runner(command, **kwargs):
            if command[:2] == ["ps", "ax"]:
                class PsCompleted:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return PsCompleted()
            calls.append(command)

            class Completed:
                returncode = 0
                stdout = "done"
                stderr = ""

            return Completed()

        old_post = _mod.post_json
        old_get = _mod.get_json
        try:
            _mod.post_json = lambda url, payload, **kwargs: (500, "panel refused")

            def fake_get(url, **kwargs):
                if "/project/current" in url:
                    return 200, {"worktree": "/tmp/new-project"}
                if url.endswith("/session"):
                    return 200, [{"id": "ses_new", "directory": "/tmp/new-project"}]
                return 404, ""

            _mod.get_json = fake_get
            result = run_wakeup_adapter(
                {
                    "project_path": "/tmp/new-project",
                    "target_slug": "opencode",
                    "inbox_path": "/tmp/new-project/.ai-collab/inbox-opencode.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read visible inbox",
                },
                mode="opencode-auto",
                runner=fake_runner,
            )
        finally:
            _mod.post_json = old_post
            _mod.get_json = old_get

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "cli")
        self.assertEqual(result["fallback_from"], "opencode-visible")
        self.assertEqual(calls[0][:3], ["/usr/bin/opencode", "run", "read visible inbox"])

    def test_antigravity_chat_adapter_uses_reuse_window(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_ANTIGRAVITY_BIN"] = "/usr/bin/antigravity"
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "codex",
                "inbox_path": "/tmp/project/.ai-collab/inbox-codex.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            mode="antigravity-chat",
            runner=fake_runner,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "antigravity-chat")
        self.assertEqual(calls[0][0][:5], ["/usr/bin/antigravity", "chat", "--mode", "agent", "--reuse-window"])
        self.assertIn("--add-file", calls[0][0])
        self.assertEqual(calls[0][0][-1], "read visible inbox")

    def test_codex_acp_builds_protocol_messages(self):
        messages = _mod.build_codex_acp_messages(
            {
                "project_path": "/tmp/project",
                "target_slug": "codex",
                "inbox_path": "/tmp/project/.ai-collab/inbox-codex.md",
                "task_id": "task-123",
                "synthetic_prompt": "read invisible inbox",
            }
        )

        self.assertEqual([m["method"] for m in messages], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(messages[1]["params"]["cwd"], "/tmp/project")
        self.assertEqual(messages[2]["params"]["sessionId"], "$SESSION_ID")
        self.assertIn("Inbox path:", messages[2]["params"]["prompt"][0]["text"])

    def test_codex_acp_dry_run_does_not_start_agent(self):
        os.environ["AI_COLLAB_WAKEUP_DRY_RUN"] = "1"
        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "codex",
                "inbox_path": "/tmp/project/.ai-collab/inbox-codex.md",
                "task_id": "task-123",
                "synthetic_prompt": "read invisible inbox",
            },
            mode="codex-acp",
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "codex-acp-dry-run")

    def test_codex_acp_adapter_processes_mock_agent(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "fake_codex_acp.py"
            script.write_text(
                """#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentInfo": {"name": "fake-codex-acp"}}
    elif method == "session/new":
        result = {"sessionId": "ses_mock"}
    elif method == "session/prompt":
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}), flush=True)
""",
                encoding="utf-8",
            )
            script.chmod(0o755)
            os.environ["AI_COLLAB_CODEX_ACP_COMMAND"] = f"{sys.executable} {script}"

            result = run_codex_acp_adapter(
                {
                    "project_path": d,
                    "target_slug": "codex",
                    "inbox_path": f"{d}/.ai-collab/inbox-codex.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read invisible inbox",
                },
                timeout=5,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "codex-acp")
        self.assertIn("ses_mock", result["message"])

    def test_codex_filesystem_adapter_records_receipt_for_referenced_discussion(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            discussions = collab / "discussions"
            discussions.mkdir(parents=True)
            inbox = collab / "inbox-codex.md"
            discussion = discussions / "discussion-20260619-opencode-status.md"
            discussion.write_text(
                """---
schema: ai-collab.thread.v2
thread: discussion-20260619-opencode-status
kind: discussion
topic: opencode-status
project: gsep
created: 2026-06-19T13:00:00Z
updated: 2026-06-19T13:00:00Z
participants: opencode, codex
status: open
---
## 2026-06-19T13:00:00Z -- opencode

type: question
to: codex

@codex are you awake?

---
""",
                encoding="utf-8",
            )
            inbox.write_text(
                SAMPLE_INBOX.replace("task-123", "wake-codex").replace(
                    "Do the thing.",
                    "OpenCode asked for Codex in `.ai-collab/discussions/discussion-20260619-opencode-status.md`.",
                ),
                encoding="utf-8",
            )

            result = _mod.run_codex_filesystem_adapter(
                {
                    "project_path": str(root),
                    "target_slug": "codex",
                    "inbox_path": str(inbox),
                    "source_path": str(inbox),
                    "source_type": "inbox",
                    "task_id": "wake-codex",
                    "synthetic_prompt": "read inbox",
                },
                now=datetime(2026, 6, 19, 13, 55, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(result["status"], "degraded")
            self.assertEqual(result["adapter_name"], "codex-filesystem")
            meta, _ = parse_frontmatter(inbox.read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "unread")
            self.assertEqual(meta["claimed_by"], "")
            self.assertIn("Codex filesystem wake receipt was recorded", discussion.read_text(encoding="utf-8"))
            self.assertIn("-- codex-filesystem", discussion.read_text(encoding="utf-8"))
            self.assertTrue((collab / "live" / "codex.agent.json").exists())
            self.assertTrue(list(collab.glob("codex-20260619-135500.md")))

    def test_codex_auto_runs_cli_exec_when_acp_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            thread = collab / "thread-task-123.md"
            append_thread_message(
                thread,
                task_id="task-123",
                project="gsep",
                inbox_name="",
                author_slug="opencode",
                message="@codex wake up.",
                now=datetime(2026, 6, 19, 13, 56, 0, tzinfo=timezone.utc),
            )
            os.environ["AI_COLLAB_CODEX_ACP_COMMAND"] = "/definitely/missing/codex-acp"
            calls = []

            def fake_runner(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return _mod.subprocess.CompletedProcess(cmd, 0, "done", "")

            result = run_wakeup_adapter(
                {
                    "project_path": str(root),
                    "target_slug": "codex",
                    "inbox_path": "",
                    "source_path": str(thread),
                    "source_type": "thread",
                    "task_id": "task-123",
                    "synthetic_prompt": "read thread",
                },
                mode="codex-auto",
                timeout=1,
                runner=fake_runner,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["adapter_name"], "cli")
            self.assertEqual(result["fallback_from"], "codex-acp")
            self.assertTrue(calls)
            self.assertIn("exec", calls[0][0])

    def test_codex_auto_records_degraded_filesystem_receipt_when_acp_and_cli_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            thread = collab / "thread-task-123.md"
            append_thread_message(
                thread,
                task_id="task-123",
                project="gsep",
                inbox_name="",
                author_slug="opencode",
                message="@codex wake up.",
                now=datetime(2026, 6, 19, 13, 56, 0, tzinfo=timezone.utc),
            )
            os.environ["AI_COLLAB_CODEX_ACP_COMMAND"] = "/definitely/missing/codex-acp"

            def failing_runner(cmd, **kwargs):
                return _mod.subprocess.CompletedProcess(cmd, 1, "", "cli failed")

            result = run_wakeup_adapter(
                {
                    "project_path": str(root),
                    "target_slug": "codex",
                    "inbox_path": "",
                    "source_path": str(thread),
                    "source_type": "thread",
                    "task_id": "task-123",
                    "synthetic_prompt": "read thread",
                },
                mode="codex-auto",
                timeout=1,
                runner=failing_runner,
            )

            self.assertEqual(result["status"], "degraded")
            self.assertEqual(result["adapter_name"], "codex-filesystem")
            self.assertEqual(result["fallback_from"], "cli")
            self.assertIn("Codex filesystem wake receipt was recorded", thread.read_text(encoding="utf-8"))

    def test_generic_acp_adapter_processes_kimi_mock_agent(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "fake_kimi_acp.py"
            script.write_text(
                """#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentInfo": {"name": "fake-kimi-acp"}}
    elif method == "session/new":
        result = {"sessionId": "ses_kimi"}
    elif method == "session/prompt":
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}), flush=True)
""",
                encoding="utf-8",
            )
            script.chmod(0o755)
            os.environ["AI_COLLAB_KIMI_ACP_COMMAND"] = f"{sys.executable} {script}"

            result = run_acp_adapter(
                {
                    "project_path": d,
                    "target_slug": "kimi",
                    "inbox_path": f"{d}/.ai-collab/inbox-kimi.md",
                    "task_id": "task-123",
                    "synthetic_prompt": "read invisible inbox",
                },
                timeout=5,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["adapter_name"], "kimi-acp")
        self.assertIn("ses_kimi", result["message"])

    def test_hermes_uri_adapter_prefills_prompt(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_HERMES_URI_TEMPLATE"] = "test://hermes?prompt={prompt}"
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()

        result = _mod.run_hermes_uri_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "hermes",
                "inbox_path": "/tmp/project/.ai-collab/inbox-hermes.md",
                "task_id": "task-123",
                "synthetic_prompt": "hello visible",
            },
            timeout=5,
            runner=fake_runner,
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(calls[0][0], ["open", "test://hermes?prompt=hello%20visible"])

    def test_focus_only_visible_preparation_does_not_send_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            registry = Path(d)
            (registry / "bridge.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "port": 43123,
                        "token": "secret",
                        "project_paths": ["/tmp/project"],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_poster(url, payload, **kwargs):
                calls.append((url, payload, kwargs))
                return 200, json.dumps({"terminal_name": "OpenCode", "agent_pid": 42, "tty": "ttys001"})

            result = prepare_ide_terminal_visible_surface(
                "/tmp/project",
                "opencode",
                poster=fake_poster,
                registry_dir=registry,
            )

        self.assertEqual(result["status"], "success")
        self.assertTrue(calls[0][0].endswith("/terminal/show"))
        self.assertNotIn("prompt", calls[0][1])

    def test_legacy_bridge_prepare_requires_focus_on_submit_without_faking_focus(self):
        with tempfile.TemporaryDirectory() as d:
            registry = Path(d)
            (registry / "bridge.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "port": 43123,
                        "token": "secret",
                        "project_paths": ["/tmp/project"],
                    }
                ),
                encoding="utf-8",
            )
            result = prepare_ide_terminal_visible_surface(
                "/tmp/project",
                "opencode",
                poster=lambda *args, **kwargs: (404, '{"status":"failed","message":"not found"}'),
                registry_dir=registry,
            )

        self.assertEqual(result["status"], "legacy-focus-on-submit")

    def test_antigravity_chat_prepare_does_not_require_ide_bridge(self):
        # codex/antigravity dispatch directly through the antigravity CLI
        # (build_antigravity_chat_command), not through an IDE bridge like the
        # terminal/native-chat adapters, so preparation must not depend on one.
        os.environ["AI_COLLAB_ANTIGRAVITY_BIN"] = "/usr/bin/antigravity"

        result = prepare_antigravity_chat_surface("/tmp/project", "codex")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["adapter_name"], "antigravity-chat-prepare")

    def test_antigravity_chat_prepare_fails_without_executable(self):
        old_lookup = _mod.antigravity_executable
        _mod.antigravity_executable = lambda: None
        try:
            result = prepare_antigravity_chat_surface("/tmp/project", "codex")
        finally:
            _mod.antigravity_executable = old_lookup

        self.assertEqual(result["status"], "failed")
        self.assertIn("no antigravity executable found", result["message"])

    def test_prepare_visible_cli_routes_codex_to_antigravity_not_terminal(self):
        # Regression for the bug reported by the user: `--prepare-visible`
        # only branched on native-chat targets and fell through to
        # prepare_ide_terminal_visible_surface for everything else, including
        # codex. That adapter looks for a project-matched IDE-bridge terminal
        # for codex, which does not exist (codex is reached through
        # antigravity-chat), so it always failed with a 409-style rejection
        # and hard-blocked the entire visible escalation (dispatch_and_
        # optionally_wait in ai-collab-converse.py returns before ever
        # calling the correct antigravity-chat adapter). codex/antigravity
        # must route to prepare_antigravity_chat_surface instead.
        os.environ["AI_COLLAB_ANTIGRAVITY_BIN"] = "/usr/bin/antigravity"
        with tempfile.TemporaryDirectory() as d:
            project_root = Path(d) / "project"
            project_root.mkdir()

            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = _mod.main(["ai-collab-wakeup.py", "--prepare-visible", str(project_root), "codex"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue().strip())
        self.assertTrue(payload["ok"])
        (result,) = payload["results"]
        self.assertEqual(result["adapter_name"], "antigravity-chat-prepare")
        self.assertNotEqual(result["adapter_name"], "ide-terminal-visible-prepare")

    def test_visible_adapter_blocks_project_not_in_allowlist(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/some/other/project"
        result = run_wakeup_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            mode="visible",
        )

        self.assertEqual(result["status"], "degraded")
        self.assertIn("guardrail", result["adapter_name"])

    def test_successful_cli_does_not_overwrite_agent_done(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = str(root)
            inbox = root / ".ai-collab" / "inbox-opencode.md"
            inbox.parent.mkdir()
            inbox.write_text(SAMPLE_INBOX.replace("to: codex", "to: opencode"), encoding="utf-8")

            def fake_runner(command, **kwargs):
                text = inbox.read_text(encoding="utf-8").replace("status: unread", "status: done")
                inbox.write_text(text, encoding="utf-8")

                class Completed:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return Completed()

            old_path = _mod.shutil.which
            try:
                _mod.shutil.which = lambda name: f"/usr/bin/{name}"
                result = process_inbox(
                    inbox,
                    "smoke",
                    events_file=root / "events.json",
                    state_file=root / "state.json",
                    log_file=root / "wakeup.log",
                    adapter_mode="cli",
                    adapter_runner=fake_runner,
                )
            finally:
                _mod.shutil.which = old_path

            self.assertEqual(result["action"], "adapter-updated")
            meta, _ = parse_frontmatter(inbox.read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "done")
            self.assertEqual(meta["attempts"], "0")


class TestProcessThread(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collab = self.root / ".ai-collab"
        self.collab.mkdir()
        (self.collab / "TEAM.md").write_text(
            """---
project: gsep
---

## Roster

- claude-code
- codex
- opencode
""",
            encoding="utf-8",
        )
        self.thread = self.collab / "thread-task-123.md"
        self.events = self.root / "events.json"
        self.state = self.root / "state.json"
        self.log = self.root / "wakeup.log"
        self.now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, author="claude", message="@codex please review this."):
        append_thread_message(
            self.thread,
            task_id="task-123",
            project="gsep",
            inbox_name="inbox-codex.md",
            author_slug=author,
            message=message,
            now=self.now,
        )

    def write_capabilities(self, agent, *, native_chat_only=False, grace=15, sleep_threshold=60):
        (self.collab / "capabilities.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {
                            "agent": agent,
                            "visible": {
                                "adapter": "mock-success",
                                "native_chat_only": native_chat_only,
                            },
                            "wake_policy": {
                                "internal_grace_seconds": grace,
                                "sleep_threshold_seconds": sleep_threshold,
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_thread_waits_for_internal_response_before_visible_escalation(self):
        self.write_capabilities("opencode")
        append_thread_message(
            self.thread,
            task_id="task-123",
            project="gsep",
            inbox_name="",
            author_slug="codex",
            message="@opencode please review this.",
            now=self.now,
        )

        waiting = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )
        dispatched = process_thread(
            self.thread,
            "gsep",
            now=self.now + timedelta(seconds=16),
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )

        self.assertEqual(waiting["results"][0]["action"], "internal-grace")
        self.assertEqual(waiting["results"][0]["grace_seconds"], 15)
        self.assertEqual(dispatched["results"][0]["action"], "dispatched")

    def test_sleeping_native_codex_escalates_without_internal_grace(self):
        self.write_capabilities("codex", native_chat_only=True)
        self.append()

        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
            adapter_mode="mock-success",
        )

        self.assertEqual(result["results"][0]["action"], "dispatched")

    def test_thread_mention_produces_wake_event(self):
        self.append()
        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "thread-mentions")
        events = json.loads(self.events.read_text(encoding="utf-8"))
        self.assertEqual(events[0]["source_type"], "thread")
        self.assertEqual(events[0]["reason"], "thread-mention")
        self.assertEqual(events[0]["target_slug"], "codex")
        self.assertEqual(events[0]["thread_path"], str(self.thread))
        self.assertIn("mentioned", events[0]["synthetic_prompt"])

    def test_thread_mention_dedupes_same_message_and_target(self):
        self.append()
        process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )
        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["results"][0]["action"], "deduped")
        events = json.loads(self.events.read_text(encoding="utf-8"))
        self.assertEqual(len(events), 2)

    def test_thread_does_not_wake_author_self_mention(self):
        self.append(author="codex", message="@codex note to self.")
        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "ignored")
        self.assertEqual(result["reason"], "no-mentions")
        self.assertFalse(self.events.exists())

    def test_thread_mention_skips_agent_not_in_project(self):
        (self.collab / "TEAM.md").write_text(
            """---
project: gsep
---

## Roster

- claude-code
- codex
""",
            encoding="utf-8",
        )
        self.append(author="codex", message="@opencode please compare this.")

        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "thread-mentions")
        self.assertEqual(result["results"][0]["action"], "skipped")
        self.assertEqual(result["results"][0]["reason"], "agent-not-in-project")
        self.assertFalse(self.events.exists())

    def test_closed_thread_is_ignored(self):
        self.append()
        meta, body = parse_frontmatter(self.thread.read_text(encoding="utf-8"))
        meta["status"] = "closed"
        self.thread.write_text(render_frontmatter(meta, body), encoding="utf-8")

        result = process_thread(
            self.thread,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "ignored")
        self.assertEqual(result["reason"], "closed")
        self.assertFalse(self.events.exists())

    def test_discussion_mention_uses_project_root(self):
        discussion_dir = self.collab / "discussions"
        discussion_dir.mkdir()
        discussion = discussion_dir / "discussion-20260616-api.md"
        append_thread_message(
            discussion,
            task_id="discussion-20260616-api",
            project="gsep",
            inbox_name="",
            author_slug="codex",
            message="@opencode please compare the two API options.",
            now=self.now,
        )

        result = process_thread(
            discussion,
            "gsep",
            now=self.now,
            events_file=self.events,
            state_file=self.state,
            log_file=self.log,
        )

        self.assertEqual(result["action"], "thread-mentions")
        events = json.loads(self.events.read_text(encoding="utf-8"))
        self.assertEqual(events[0]["target_slug"], "opencode")
        self.assertEqual(events[0]["project_path"], str(self.root))
        self.assertEqual(events[0]["thread_path"], str(discussion))


if __name__ == "__main__":
    unittest.main()
