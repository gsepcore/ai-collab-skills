#!/usr/bin/env python3
"""
Tests for ai-collab-codex-bridge.py.
Run with: python3 install/test_codex_bridge.py
"""
import importlib.util
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_codex_bridge",
    Path(__file__).parent / "ai-collab-codex-bridge.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestCodexBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".ai-collab").mkdir()
        self.now = datetime(2026, 6, 19, 13, 30, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_discussion_mentions_codex(self):
        path = _mod.write_codex_discussion(
            self.root,
            {"from_agent": "opencode", "topic": "Need Codex", "message": "please review"},
            now=self.now,
        )

        text = path.read_text(encoding="utf-8")
        self.assertIn("participants: opencode, codex", text)
        self.assertIn("to: codex", text)
        self.assertIn("@codex please review", text)

    def test_adapter_mode_mapping(self):
        self.assertEqual(_mod.adapter_mode("background"), "codex-auto")
        self.assertEqual(_mod.adapter_mode("visible"), "antigravity-chat")
        self.assertEqual(_mod.adapter_mode("notify-only"), "notify-only")

    def test_handle_message_dispatches_wakeup_with_background_auto_mode(self):
        calls = []

        def fake_runner(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr="")

        result = _mod.handle_message(
            {
                "project_path": str(self.root),
                "from_agent": "opencode",
                "topic": "Bridge Test",
                "message": "@codex ping",
                "mode": "background",
            },
            runner=fake_runner,
            now=self.now,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(Path(result["thread_path"]).exists())
        self.assertEqual(result["wakeup"]["adapter_mode"], "codex-auto")
        self.assertEqual(calls[0][1]["env"]["AI_COLLAB_WAKEUP_ADAPTER"], "codex-auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
