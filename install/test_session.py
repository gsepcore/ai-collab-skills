#!/usr/bin/env python3
"""Tests for persistent agent identity and runtime session registration."""
import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_spec = importlib.util.spec_from_file_location("ai_collab_session", Path(__file__).parent / "ai-collab-session.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestSessionIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        collab = self.root / ".ai-collab"
        collab.mkdir()
        (collab / "agents.json").write_text(json.dumps({
            "schema": "ai-collab.agents.v2", "project_id": "prj_test",
            "agents": [{"agent": "claude-code-ide", "agent_id": "agt_native", "container": "ide-native"}],
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_registers_distinct_runtime_session_codes(self):
        base = dict(root=str(self.root), agent="claude-code-ide", agent_id="agt_native", session_id="",
                    container="ide-native", surface_kind="ide-native-chat", surface_id="pane:claude",
                    pid=123, tty="", host_pid=99, adapter="ide-native-chat", new=False)
        self.assertEqual(_mod.register(argparse.Namespace(**base)), 0)
        base["pid"] = 124
        self.assertEqual(_mod.register(argparse.Namespace(**base)), 0)
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in (self.root / ".ai-collab/live/sessions").glob("ses_*.json")]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["agent_id"] for row in rows}, {"agt_native"})
        self.assertEqual(len({row["session_id"] for row in rows}), 2)

    def test_rejects_agent_id_collision(self):
        args = argparse.Namespace(root=str(self.root), agent="claude-code-ide", agent_id="agt_wrong", session_id="",
                                  container="", surface_kind="process", surface_id="", pid=0, tty="", host_pid=0, adapter="", new=False)
        with self.assertRaises(SystemExit):
            _mod.register(args)

    def test_same_runtime_reuses_its_session_code(self):
        args = argparse.Namespace(
            root=str(self.root), agent="claude-code-ide", agent_id="agt_native", session_id="",
            container="ide-native", surface_kind="ide-native-chat", surface_id="pane:claude",
            pid=123, tty="", host_pid=99, adapter="ide-native-chat", new=False,
        )
        self.assertEqual(_mod.register(args), 0)
        first = json.loads((self.root / ".ai-collab/live/sessions/current-claude-code-ide.json").read_text(encoding="utf-8"))
        self.assertEqual(_mod.register(args), 0)
        second = json.loads((self.root / ".ai-collab/live/sessions/current-claude-code-ide.json").read_text(encoding="utf-8"))
        self.assertEqual(first["session_id"], second["session_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
