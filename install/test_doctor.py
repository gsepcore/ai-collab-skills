#!/usr/bin/env python3
"""
Tests for ai-collab-doctor.py
Run with: python3 install/test_doctor.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_spec = importlib.util.spec_from_file_location(
    "ai_collab_doctor",
    Path(__file__).parent / "ai-collab-doctor.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_doctor"] = _mod
_spec.loader.exec_module(_mod)


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        for rel in (*_mod.REQUIRED_CLAUDE_FILES, *_mod.REQUIRED_SKILL_FILES):
            path = self.home / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("ok\n", encoding="utf-8")
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "python3 ~/.claude/ai-collab-summary.py"}]}
                ]
            }
        }
        (self.home / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def levels(self, results):
        return [result.level for result in results]

    def test_happy_path_without_launchd_has_no_failures(self):
        results = _mod.run_checks(self.home, include_launchd=False)
        self.assertNotIn("FAIL", self.levels(results))
        names = [result.name for result in results]
        self.assertIn(".claude/ai-collab-recover.py", names)
        self.assertIn(".claude/ai-collab-team.py", names)

    def test_missing_required_file_is_failure(self):
        (self.home / ".claude/ai-collab-wakeup.py").unlink()
        results = _mod.run_checks(self.home, include_launchd=False)
        failures = [result for result in results if result.level == "FAIL"]
        self.assertEqual(len(failures), 1)
        self.assertIn("ai-collab-wakeup.py", failures[0].name)

    def test_invalid_settings_json_is_failure(self):
        (self.home / ".claude/settings.json").write_text("{broken", encoding="utf-8")
        results = _mod.run_checks(self.home, include_launchd=False)
        self.assertIn("FAIL", self.levels(results))

    def test_settings_without_hooks_warns(self):
        (self.home / ".claude/settings.json").write_text("{}", encoding="utf-8")
        results = _mod.run_checks(self.home, include_launchd=False)
        warnings = [result for result in results if result.level == "WARN"]
        self.assertTrue(any(result.name == "hooks" for result in warnings))

    def test_invalid_queue_json_is_failure(self):
        (self.home / ".ai-collab-wakeup-events.json").write_text("[broken", encoding="utf-8")
        results = _mod.run_checks(self.home, include_launchd=False)
        failures = [result for result in results if result.level == "FAIL"]
        self.assertTrue(any(result.name == ".ai-collab-wakeup-events.json" for result in failures))

    def test_codex_visible_check_warns_when_cli_is_missing(self):
        with patch.object(_mod, "antigravity_chat_executable", return_value=""):
            results = _mod.run_checks(self.home, include_launchd=False)
        codex_visible = [result for result in results if result.name == "codex-visible"]
        self.assertEqual(len(codex_visible), 1)
        self.assertEqual(codex_visible[0].level, "WARN")
        self.assertIn("not found", codex_visible[0].message)

    def test_codex_visible_check_is_ok_when_cli_exists(self):
        with patch.object(_mod, "antigravity_chat_executable", return_value="/usr/bin/antigravity-ide"):
            results = _mod.run_checks(self.home, include_launchd=False)
        codex_visible = [result for result in results if result.name == "codex-visible"]
        self.assertEqual(codex_visible[0].level, "OK")
        self.assertIn("supported Antigravity chat CLI", codex_visible[0].message)

    def test_ocr_engine_check_ok_when_tesseract_exists(self):
        with patch.object(_mod.shutil, "which", return_value="/opt/homebrew/bin/tesseract"):
            results = _mod.check_ocr_engine()

        self.assertEqual(results[0].level, "OK")
        self.assertIn("tesseract available", results[0].message)

    def test_ocr_engine_check_warns_when_missing(self):
        with patch.object(_mod.shutil, "which", return_value=None):
            results = _mod.check_ocr_engine()

        self.assertEqual(results[0].level, "WARN")
        self.assertIn("metadata-only", results[0].message)

    def test_strict_exit_code_fails_on_failure_only(self):
        results = [_mod.warn("daemon", "not loaded")]
        self.assertEqual(_mod.exit_code(results, strict=True), 0)
        results.append(_mod.fail("settings", "bad"))
        self.assertEqual(_mod.exit_code(results, strict=True), 1)
        self.assertEqual(_mod.exit_code(results, strict=False), 0)


if __name__ == "__main__":
    unittest.main()
