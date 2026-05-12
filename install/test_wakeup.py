#!/usr/bin/env python3
"""
Tests for ai-collab-wakeup.py
Run with: python3 -m pytest install/test_wakeup.py -v
       or: python3 install/test_wakeup.py
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

    def test_successful_adapter_claims_inbox(self):
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

        self.assertEqual(result["action"], "claimed")
        self.assertEqual(result["adapter_result"]["status"], "success")
        meta = self.read_meta()
        self.assertEqual(meta["status"], "claimed")
        self.assertEqual(meta["claimed_by"], "mock-success")
        self.assertEqual(meta["claimed_at"], "2026-05-12T12:00:00Z")

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
            "AI_COLLAB_ANTIGRAVITY_BIN": os.environ.get("AI_COLLAB_ANTIGRAVITY_BIN"),
            "AI_COLLAB_ANTIGRAVITY_MODE": os.environ.get("AI_COLLAB_ANTIGRAVITY_MODE"),
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

    def test_cli_adapter_requires_project_allowlist(self):
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

    def test_opencode_visible_posts_to_tui_port(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"
        posts = []

        def fake_poster(url, payload, **kwargs):
            posts.append((url, payload, kwargs))
            return 200, "ok"

        result = _mod.run_opencode_visible_adapter(
            {
                "project_path": "/tmp/project",
                "target_slug": "opencode",
                "inbox_path": "/tmp/project/.ai-collab/inbox-opencode.md",
                "task_id": "task-123",
                "synthetic_prompt": "read visible inbox",
            },
            timeout=10,
            poster=fake_poster,
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "opencode-visible")
        self.assertEqual(posts[0][0], "http://127.0.0.1:12345/tui/append-prompt")
        self.assertEqual(posts[0][1]["text"], "read visible inbox")

    def test_visible_adapter_routes_opencode(self):
        os.environ["AI_COLLAB_WAKEUP_CLI_PROJECTS"] = "/tmp/project"
        os.environ["AI_COLLAB_OPENCODE_PORTS"] = "12345"

        old_post = _mod.post_json
        try:
            _mod.post_json = lambda url, payload, **kwargs: (200, "ok")
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
        finally:
            _mod.post_json = old_post

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "opencode-visible")

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

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["adapter_name"], "antigravity-chat")
        self.assertEqual(calls[0][0][:5], ["/usr/bin/antigravity", "chat", "--mode", "agent", "--reuse-window"])
        self.assertIn("--add-file", calls[0][0])
        self.assertEqual(calls[0][0][-1], "read visible inbox")

    def test_visible_adapter_blocks_unallowed_project(self):
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


if __name__ == "__main__":
    unittest.main()
