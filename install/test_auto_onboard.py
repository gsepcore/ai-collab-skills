#!/usr/bin/env python3
"""
Tests for ai-collab-auto-onboard.py
Run with: python3 install/test_auto_onboard.py
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "ai_collab_auto_onboard",
    Path(__file__).parent / "ai-collab-auto-onboard.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_auto_onboard"] = _mod
_spec.loader.exec_module(_mod)

process_log = _mod.process_log


PROTOCOL = """\
# AI Collab Protocol

## Cursor

Add to `.cursorrules`:

```
## AI Collab Protocol

Cursor snippet.
```

---

## Codex / GPT

Add to your Codex system prompt:

```
## AI Collab Protocol

Codex snippet.
```

---

## OpenCode / Minimax

Add to your OpenCode system prompt:

```
## AI Collab Protocol

OpenCode snippet.
```
"""


class TestAutoOnboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collab = self.root / ".ai-collab"
        self.collab.mkdir()
        self.protocol = self.root / "protocol.md"
        self.protocol.write_text(PROTOCOL, encoding="utf-8")
        self._old_protocol = os.environ.get("AI_COLLAB_PROTOCOL_FILE")
        os.environ["AI_COLLAB_PROTOCOL_FILE"] = str(self.protocol)
        self.now = datetime(2026, 5, 13, 11, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        if self._old_protocol is None:
            os.environ.pop("AI_COLLAB_PROTOCOL_FILE", None)
        else:
            os.environ["AI_COLLAB_PROTOCOL_FILE"] = self._old_protocol
        self.tmp.cleanup()

    def write_log(self, name: str):
        path = self.collab / name
        path.write_text(
            "---\nai: test\nsession: 20260513-110000\nupdated: 2026-05-13T11:00:00Z\n---\n",
            encoding="utf-8",
        )
        return path

    def test_known_cursor_appends_rules_and_team(self):
        log = self.write_log("cursor-20260513-110000.md")

        result = process_log("demo", log, now=self.now)

        self.assertEqual(result, {"action": "appended", "slug": "cursor"})
        self.assertIn("Cursor snippet", (self.root / ".cursorrules").read_text(encoding="utf-8"))
        team = (self.collab / "TEAM.md").read_text(encoding="utf-8")
        self.assertIn("- claude-code (director)", team)
        self.assertIn("- cursor", team)

    def test_known_codex_uses_agents_md(self):
        log = self.write_log("codex-20260513-110000.md")

        result = process_log("demo", log, now=self.now)

        self.assertEqual(result["action"], "appended")
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=codex", agents)
        self.assertIn("Codex snippet", agents)

    def test_shared_agents_md_can_append_multiple_agents(self):
        opencode = self.write_log("opencode-20260513-110000.md")
        codex = self.write_log("codex-20260513-110000.md")

        process_log("demo", opencode, now=self.now)
        process_log("demo", codex, now=self.now)

        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", agents)
        self.assertIn("AI-COLLAB-START agent=codex", agents)
        self.assertIn("OpenCode snippet", agents)
        self.assertIn("Codex snippet", agents)
        opencode_rules = (self.root / ".opencode" / "rules" / "ai-collab.md").read_text(encoding="utf-8")
        self.assertIn("AI-COLLAB-START agent=opencode", opencode_rules)
        self.assertIn("OpenCode snippet", opencode_rules)

    def test_idempotent_reprocessing_does_not_duplicate(self):
        log = self.write_log("cursor-20260513-110000.md")

        process_log("demo", log, now=self.now)
        result = process_log("demo", log, now=self.now)

        self.assertEqual(result, {"action": "noop", "slug": "cursor"})
        self.assertEqual((self.root / ".cursorrules").read_text(encoding="utf-8").count("Cursor snippet"), 1)
        self.assertEqual((self.collab / "TEAM.md").read_text(encoding="utf-8").count("- cursor"), 1)

    def test_unknown_slug_notifies_inbox_all_and_team(self):
        log = self.write_log("mistral-20260513-110000.md")

        result = process_log("demo", log, now=self.now)

        self.assertEqual(result, {"action": "notified-user", "slug": "mistral"})
        inbox = (self.collab / "inbox-all.md").read_text(encoding="utf-8")
        self.assertIn("status: unread", inbox)
        self.assertIn("New agent detected: mistral", inbox)
        self.assertIn("/collab onboard mistral", inbox)
        self.assertIn("- mistral", (self.collab / "TEAM.md").read_text(encoding="utf-8"))

    def test_non_session_log_is_ignored(self):
        log = self.write_log("README.md")

        result = process_log("demo", log, now=self.now)

        self.assertEqual(result, {"action": "ignored", "slug": ""})
        self.assertFalse((self.root / ".cursorrules").exists())


if __name__ == "__main__":
    unittest.main()
