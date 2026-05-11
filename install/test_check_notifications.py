#!/usr/bin/env python3
"""
Tests for ai-collab-check-notifications.py
Run with: python3 -m pytest install/test_check_notifications.py -v
       or: python3 install/test_check_notifications.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ai_collab_check_notifications",
    Path(__file__).parent / "ai-collab-check-notifications.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_check_notifications"] = _mod
_spec.loader.exec_module(_mod)

coerce_list = _mod.coerce_list
parse_timestamp = _mod.parse_timestamp
filter_notifications = _mod.filter_notifications
format_note = _mod.format_note
atomic_write = _mod.atomic_write
split_by_project = _mod.split_by_project
detect_project = _mod.detect_project


class TestCoerceList(unittest.TestCase):
    def test_list_passes_through(self):
        self.assertEqual(coerce_list([1, 2, 3]), [1, 2, 3])

    def test_dict_becomes_singleton(self):
        self.assertEqual(coerce_list({"a": 1}), [{"a": 1}])

    def test_none_becomes_empty(self):
        self.assertEqual(coerce_list(None), [])

    def test_string_becomes_empty(self):
        self.assertEqual(coerce_list("not a list"), [])

    def test_int_becomes_empty(self):
        self.assertEqual(coerce_list(42), [])


class TestParseTimestamp(unittest.TestCase):
    def test_iso_with_z(self):
        self.assertIsNotNone(parse_timestamp("2026-05-11T17:00:00Z"))

    def test_iso_with_offset(self):
        self.assertIsNotNone(parse_timestamp("2026-05-11T17:00:00+00:00"))

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_timestamp("not a date"))

    def test_non_string_returns_none(self):
        self.assertIsNone(parse_timestamp(12345))
        self.assertIsNone(parse_timestamp(None))


class TestFilterNotifications(unittest.TestCase):
    def test_filters_non_dict_items(self):
        kept = filter_notifications([{"ai": "x"}, "junk", 42, None, {"ai": "y"}])
        self.assertEqual(len(kept), 2)

    def test_requires_ai_field(self):
        kept = filter_notifications([{"file": "x.md", "message": "no ai"}])
        self.assertEqual(kept, [])

    def test_requires_ai_string(self):
        kept = filter_notifications([{"ai": 123, "message": "not a string"}])
        self.assertEqual(kept, [])

    def test_filters_system_files(self):
        kept = filter_notifications([
            {"ai": "a", "file": "CONTEXT.md"},
            {"ai": "b", "file": "PROTOCOL.md"},
            {"ai": "c", "file": "normal.md"},
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["ai"], "c")

    def test_filters_old_notifications(self):
        kept = filter_notifications([
            {"ai": "a", "message": "ancient", "ts": "2000-01-01T00:00:00Z"},
        ])
        self.assertEqual(kept, [])

    def test_keeps_undated_notifications(self):
        kept = filter_notifications([{"ai": "a", "message": "no ts"}])
        self.assertEqual(len(kept), 1)


class TestFormatNote(unittest.TestCase):
    def test_basic_format(self):
        out = format_note({"ai": "opencode", "file": "x.md", "message": "hello"})
        self.assertIn("opencode", out)
        self.assertIn("x.md", out)
        self.assertIn("hello", out)

    def test_truncates_long_message(self):
        out = format_note({"ai": "a", "message": "x" * 10000})
        self.assertLess(len(out), 600)
        self.assertIn("...[truncated]", out)

    def test_newlines_collapsed(self):
        out = format_note({"ai": "a", "message": "line1\nline2\nline3"})
        self.assertNotIn("\n", out)

    def test_accepts_alternate_keys(self):
        for key in ("message", "note", "summary"):
            out = format_note({"ai": "a", key: "content"})
            self.assertIn("content", out)


class TestAtomicWrite(unittest.TestCase):
    def test_writes_content(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            atomic_write(path, "hello")
            with open(path) as f:
                self.assertEqual(f.read(), "hello")

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            with open(path, "w") as f:
                f.write("old content")
            atomic_write(path, "new content")
            with open(path) as f:
                self.assertEqual(f.read(), "new content")

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            atomic_write(path, "content")
            remaining = [f for f in os.listdir(d) if f.startswith(".tmp_")]
            self.assertEqual(remaining, [])


class TestSplitByProject(unittest.TestCase):
    def test_items_with_matching_project_go_to_current(self):
        items = [{"ai": "a", "project": "gsep"}, {"ai": "b", "project": "other"}]
        cur, oth = split_by_project(items, "gsep")
        self.assertEqual(len(cur), 1)
        self.assertEqual(len(oth), 1)
        self.assertEqual(cur[0]["ai"], "a")
        self.assertEqual(oth[0]["ai"], "b")

    def test_items_without_project_go_to_current(self):
        items = [{"ai": "a"}, {"ai": "b", "project": "gsep"}]
        cur, oth = split_by_project(items, "gsep")
        self.assertEqual(len(cur), 2)
        self.assertEqual(len(oth), 0)

    def test_empty_project_string_goes_to_current(self):
        items = [{"ai": "a", "project": ""}]
        cur, oth = split_by_project(items, "gsep")
        self.assertEqual(len(cur), 1)
        self.assertEqual(len(oth), 0)

    def test_non_dict_items_dropped(self):
        items = [{"ai": "a", "project": "gsep"}, "junk", 42, None]
        cur, oth = split_by_project(items, "gsep")
        self.assertEqual(len(cur), 1)
        self.assertEqual(len(oth), 0)


class TestDetectProject(unittest.TestCase):
    def test_env_override(self):
        old = os.environ.get("AI_COLLAB_PROJECT")
        try:
            os.environ["AI_COLLAB_PROJECT"] = "from-env"
            self.assertEqual(detect_project(), "from-env")
        finally:
            if old is None:
                os.environ.pop("AI_COLLAB_PROJECT", None)
            else:
                os.environ["AI_COLLAB_PROJECT"] = old

    def test_returns_string(self):
        os.environ.pop("AI_COLLAB_PROJECT", None)
        result = detect_project()
        self.assertIsInstance(result, str)


class TestFormatNoteWithProject(unittest.TestCase):
    def test_default_no_project_in_output(self):
        out = format_note({"ai": "opencode", "message": "x", "project": "gsep"})
        self.assertIn("opencode", out)
        self.assertNotIn("gsep", out)

    def test_cross_project_mode_shows_project(self):
        out = format_note(
            {"ai": "opencode", "message": "x", "project": "gsep"},
            include_project=True,
        )
        self.assertIn("opencode/gsep", out)


class TestMainIntegration(unittest.TestCase):
    """End-to-end via subprocess against the real script."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.notif_file = os.path.join(self.tmp, "notifications.json")
        self.script = str(Path(__file__).parent / "ai-collab-check-notifications.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, content):
        if content is None:
            if os.path.exists(self.notif_file):
                os.remove(self.notif_file)
        else:
            with open(self.notif_file, "w") as f:
                f.write(content)
        import subprocess
        # Patch HOME so the script reads our test file
        env = {**os.environ, "HOME": self.tmp, "AI_COLLAB_LOCK_TIMEOUT": "1"}
        # The script uses ~/.ai-collab-notifications.json which is HOME-relative
        target = os.path.join(self.tmp, ".ai-collab-notifications.json")
        if content is not None:
            with open(target, "w") as f:
                f.write(content)
        elif os.path.exists(target):
            os.remove(target)
        result = subprocess.run(
            ["python3", self.script],
            capture_output=True, text=True, env=env, timeout=5,
        )
        return result, target

    def test_silent_when_file_missing(self):
        r, _ = self._run(None)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.returncode, 0)

    def test_silent_when_empty_array(self):
        r, _ = self._run("[]")
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.returncode, 0)

    def test_silent_when_corrupt_json(self):
        r, target = self._run("not-json{{")
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.returncode, 0)
        with open(target) as f:
            self.assertEqual(f.read().strip(), "[]")

    def test_prints_valid_notification(self):
        r, _ = self._run('[{"ai":"opencode","file":"x.md","message":"hi"}]')
        self.assertIn("[AI-COLLAB", r.stdout)
        self.assertIn("[END AI-COLLAB]", r.stdout)
        self.assertIn("opencode", r.stdout)
        self.assertIn("hi", r.stdout)

    def test_clears_file_after_read(self):
        _, target = self._run('[{"ai":"opencode","message":"clear-me"}]')
        with open(target) as f:
            self.assertEqual(f.read().strip(), "[]")

    def test_always_exit_zero(self):
        for content in (None, "", "[]", "not-json", "null", '"string"'):
            r, _ = self._run(content)
            self.assertEqual(r.returncode, 0, f"Failed for content={content!r}")


class TestProjectIsolationIntegration(unittest.TestCase):
    """Verify that notifications from other projects are preserved, not shown."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.notif_file = os.path.join(self.tmp, ".ai-collab-notifications.json")
        self.script = str(Path(__file__).parent / "ai-collab-check-notifications.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, content, project="myproject", cross=False):
        with open(self.notif_file, "w") as f:
            f.write(content)
        import subprocess
        env = {
            **os.environ,
            "HOME": self.tmp,
            "AI_COLLAB_LOCK_TIMEOUT": "1",
            "AI_COLLAB_PROJECT": project,
        }
        if cross:
            env["AI_COLLAB_CROSS_PROJECT"] = "1"
        result = subprocess.run(
            ["python3", self.script],
            capture_output=True, text=True, env=env, timeout=5,
        )
        with open(self.notif_file) as f:
            after = f.read()
        return result, after

    def test_only_current_project_shown(self):
        items = [
            {"ai": "opencode", "project": "myproject", "message": "for me"},
            {"ai": "codex", "project": "other", "message": "for other"},
        ]
        r, after = self._run(json.dumps(items), project="myproject")
        self.assertIn("for me", r.stdout)
        self.assertNotIn("for other", r.stdout)

    def test_other_projects_preserved_in_file(self):
        items = [
            {"ai": "opencode", "project": "myproject", "message": "consume me"},
            {"ai": "codex", "project": "other", "message": "preserve me"},
        ]
        r, after = self._run(json.dumps(items), project="myproject")
        remaining = json.loads(after)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["project"], "other")
        self.assertIn("preserve me", remaining[0]["message"])

    def test_header_shows_active_project(self):
        items = [{"ai": "opencode", "project": "myproject", "message": "x"}]
        r, _ = self._run(json.dumps(items), project="myproject")
        self.assertIn("project=myproject", r.stdout)

    def test_cross_project_mode_shows_all(self):
        items = [
            {"ai": "opencode", "project": "myproject", "message": "a"},
            {"ai": "codex", "project": "other", "message": "b"},
        ]
        r, after = self._run(json.dumps(items), project="myproject", cross=True)
        self.assertIn("a", r.stdout)
        self.assertIn("b", r.stdout)
        self.assertIn("cross-project", r.stdout)
        self.assertIn("opencode/myproject", r.stdout)
        self.assertIn("codex/other", r.stdout)

    def test_cross_project_mode_clears_all(self):
        items = [
            {"ai": "opencode", "project": "myproject"},
            {"ai": "codex", "project": "other"},
        ]
        r, after = self._run(json.dumps(items), project="myproject", cross=True)
        self.assertEqual(json.loads(after), [])

    def test_legacy_notifications_without_project_consumed(self):
        items = [{"ai": "opencode", "message": "legacy no-project tag"}]
        r, after = self._run(json.dumps(items), project="myproject")
        self.assertIn("legacy no-project tag", r.stdout)
        self.assertEqual(json.loads(after), [])

    def test_only_other_project_in_file_silent(self):
        items = [{"ai": "codex", "project": "other", "message": "not mine"}]
        r, after = self._run(json.dumps(items), project="myproject")
        self.assertEqual(r.stdout.strip(), "")
        remaining = json.loads(after)
        self.assertEqual(len(remaining), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
