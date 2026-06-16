#!/usr/bin/env python3
"""
Tests for ai-collab-converse.py
Run with: python3 install/test_converse.py
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_converse",
    Path(__file__).parent / "ai-collab-converse.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_converse"] = _mod
_spec.loader.exec_module(_mod)


class TestConverse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collab = self.root / ".ai-collab"
        self.collab.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return _mod.main(["--root", str(self.root), *args])

    def only_discussion(self):
        matches = list((self.collab / "discussions").glob("*.md"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_start_discussion_mentions_recipient_and_tracks_participants(self):
        self.run_cli(
            "start",
            "--author",
            "codex",
            "--topic",
            "API boundary",
            "--to",
            "opencode",
            "--type",
            "question",
            "--message",
            "Should we add an adapter?",
        )

        path = self.only_discussion()
        text = path.read_text(encoding="utf-8")
        meta, _body = _mod.parse_frontmatter(text)

        self.assertEqual(meta["schema"], _mod.SCHEMA_VERSION)
        self.assertEqual(meta["kind"], "discussion")
        self.assertEqual(meta["topic"], "API boundary")
        self.assertEqual(meta["participants"], "codex, opencode")
        self.assertIn("type: question", text)
        self.assertIn("to: opencode", text)
        self.assertIn("@opencode Should we add an adapter?", text)

    def test_reply_proposal_and_json_summary(self):
        self.run_cli(
            "start",
            "--author",
            "codex",
            "--topic",
            "API boundary",
            "--to",
            "opencode",
            "--message",
            "Please compare options.",
        )
        thread = self.only_discussion().stem
        self.run_cli(
            "proposal",
            "--thread",
            thread,
            "--author",
            "opencode",
            "--to",
            "codex",
            "--message",
            "Keep the public API stable and add an adapter.",
        )

        output = io.StringIO()
        with redirect_stdout(output):
            self.run_cli("summary")
        rows = json.loads(output.getvalue())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["latest_author"], "opencode")
        self.assertIn("adapter", rows[0]["latest_excerpt"])
        text = self.only_discussion().read_text(encoding="utf-8")
        self.assertIn("type: proposal", text)
        self.assertIn("@codex Keep the public API stable", text)

    def test_task_conversation_uses_compatible_thread_path(self):
        self.run_cli(
            "start",
            "--kind",
            "task",
            "--task-id",
            "billing-ui",
            "--author",
            "codex",
            "--topic",
            "Billing UI",
            "--to",
            "opencode",
            "--message",
            "Please review the component boundary.",
        )

        path = self.collab / "thread-billing-ui.md"
        self.assertTrue(path.exists())
        meta, text = _mod.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(meta["kind"], "task")
        self.assertEqual(meta["task_id"], "billing-ui")
        self.assertEqual(meta["inbox"], "inbox-opencode.md")
        self.assertIn("@opencode Please review", text)

    def test_close_prevents_later_replies(self):
        self.run_cli(
            "start",
            "--author",
            "codex",
            "--topic",
            "API boundary",
            "--message",
            "Opening note.",
        )
        thread = self.only_discussion().stem
        self.run_cli("close", "--thread", thread, "--author", "codex", "--message", "Done.")

        with self.assertRaises(SystemExit):
            self.run_cli("reply", "--thread", thread, "--author", "opencode", "--message", "Late reply.")

        meta, _body = _mod.parse_frontmatter(self.only_discussion().read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "closed")


if __name__ == "__main__":
    unittest.main()
