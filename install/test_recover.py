#!/usr/bin/env python3
"""Tests for ai-collab-recover.py."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_recover",
    Path(__file__).parent / "ai-collab-recover.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_recover"] = _mod
_spec.loader.exec_module(_mod)


class TestRecover(unittest.TestCase):
    def test_prunes_wakeup_dedupe_for_unfinished_inbox(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            (collab / "inbox-codex.md").write_text(
                """---
task_id: task-123
status: unread
---
## Task
Wake Codex.
""",
                encoding="utf-8",
            )
            state_file = root / "state.json"
            state_file.write_text(json.dumps({"task-123:100:0": "seen", "other:1:0": "seen"}), encoding="utf-8")

            result = _mod.recover_project(
                root,
                summary_script=root / "missing-summary.py",
                state_file=state_file,
                max_context_age_seconds=3600,
                dry_run=False,
                now=datetime(2026, 6, 23, tzinfo=timezone.utc),
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["wakeup_state"]["status"], "updated")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("task-123:100:0", state)
            self.assertIn("other:1:0", state)
            self.assertTrue((collab / "live" / "recovery.json").exists())

    def test_keeps_terminal_inbox_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            (collab / "inbox-codex.md").write_text(
                """---
task_id: task-123
status: done
---
## Task
Already done.
""",
                encoding="utf-8",
            )
            state_file = root / "state.json"
            state_file.write_text(json.dumps({"task-123:100:0": "seen"}), encoding="utf-8")

            result = _mod.recover_project(
                root,
                summary_script=root / "missing-summary.py",
                state_file=state_file,
                max_context_age_seconds=3600,
                dry_run=False,
            )

            self.assertEqual(result["wakeup_state"]["status"], "skipped")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("task-123:100:0", state)

    def test_requeues_failed_unclaimed_wakeup_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            inbox = collab / "inbox-opencode.md"
            inbox.write_text(
                """---
task_id: task-123
status: failed
attempts: 3
last_attempt: 2026-06-23T18:49:30Z
claimed_by: 
claimed_at: 
done_at: 2026-06-23T18:49:30Z
---
## Task
Wake OpenCode.
""",
                encoding="utf-8",
            )
            state_file = root / "state.json"
            state_file.write_text(json.dumps({"task-123:100:0": "seen"}), encoding="utf-8")

            result = _mod.recover_project(
                root,
                summary_script=root / "missing-summary.py",
                state_file=state_file,
                max_context_age_seconds=3600,
                dry_run=False,
                now=datetime(2026, 6, 23, 19, 30, tzinfo=timezone.utc),
            )

            self.assertEqual(result["requeued_failed_wakeups"]["status"], "updated")
            meta, _body = _mod.parse_frontmatter(inbox.read_text(encoding="utf-8"))
            self.assertEqual(meta["status"], "unread")
            self.assertEqual(meta["attempts"], "0")
            self.assertEqual(meta["last_attempt"], "")
            self.assertEqual(meta["done_at"], "")
            self.assertEqual(meta["recovered_by"], "ai-collab-recover")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("task-123:100:0", state)

    def test_does_not_requeue_claimed_or_already_recovered_failed_task(self):
        self.assertFalse(
            _mod.should_requeue_failed_wakeup(
                {"task_id": "task-1", "status": "failed", "attempts": "3", "claimed_by": "opencode"}
            )
        )
        self.assertFalse(
            _mod.should_requeue_failed_wakeup(
                {"task_id": "task-1", "status": "failed", "attempts": "3", "recovered_by": "ai-collab-recover"}
            )
        )

    def test_refreshes_stale_context_with_summary_script(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            collab = root / ".ai-collab"
            collab.mkdir()
            context = collab / "CONTEXT.md"
            context.write_text("old", encoding="utf-8")
            log = collab / "codex-20260623-120000.md"
            log.write_text("---\nai: Codex\n---\n## Working On\nRecovering\n", encoding="utf-8")
            old = time.time() - 7200
            os.utime(context, (old, old))

            summary = root / "summary.py"
            summary.write_text(
                "from pathlib import Path\nPath('.ai-collab/CONTEXT.md').write_text('new context')\n",
                encoding="utf-8",
            )
            result = _mod.recover_project(
                root,
                summary_script=summary,
                state_file=root / "state.json",
                max_context_age_seconds=3600,
                dry_run=False,
            )

            self.assertEqual(result["context"]["status"], "updated")
            self.assertEqual(context.read_text(encoding="utf-8"), "new context")

    def test_discover_projects_finds_collab_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            project = home / "work" / "repo"
            (project / ".ai-collab").mkdir(parents=True)
            found = _mod.discover_projects(home, max_depth=4)
            self.assertIn(project, found)


if __name__ == "__main__":
    unittest.main(verbosity=2)
