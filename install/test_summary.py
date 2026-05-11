#!/usr/bin/env python3
"""
Tests for ai-collab-summary.py
Run with: python3 -m pytest install/test_summary.py -v
       or: python3 install/test_summary.py
"""
import sys, os, tempfile, unittest
from pathlib import Path

# Import functions from the summary script (hyphenated filename needs importlib)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ai_collab_summary",
    Path(__file__).parent / "ai-collab-summary.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_summary"] = _mod
_spec.loader.exec_module(_mod)

parse_frontmatter = _mod.parse_frontmatter
extract_section   = _mod.extract_section
collect_items     = _mod.collect_items
_main             = _mod.main

# ── Helpers ────────────────────────────────────────────────────────────────

SAMPLE_LOG = """\
---
ai: Claude Code (claude-sonnet-4-6)
session: 20260511-143022
project: my-project
updated: 2026-05-11 14:30:22
---

## Working On
Fixing the auth timeout in src/auth.ts — JWT tokens expire too early.

## Files Modified This Session
- `src/auth.ts` — increased expiry from 5min to 15min
- `src/auth.test.ts` — added tests for refresh flow

## Decisions Made
- 15min JWT expiry — balances security with UX

## Issues Identified
- `src/auth.ts:42` — race condition on concurrent refresh

## Do Not Touch (Avoid Conflicts)
- `src/auth.ts` — currently being refactored

## Handoff Note
Auth fix is complete. Race condition on line 42 is next.
"""

# ── parse_frontmatter ───────────────────────────────────────────────────────

class TestParseFrontmatter(unittest.TestCase):

    def test_extracts_all_fields(self):
        meta, _ = parse_frontmatter(SAMPLE_LOG)
        self.assertEqual(meta["ai"], "Claude Code (claude-sonnet-4-6)")
        self.assertEqual(meta["session"], "20260511-143022")
        self.assertEqual(meta["project"], "my-project")
        self.assertEqual(meta["updated"], "2026-05-11 14:30:22")

    def test_body_does_not_include_frontmatter(self):
        _, body = parse_frontmatter(SAMPLE_LOG)
        self.assertNotIn("session:", body)
        self.assertIn("Working On", body)

    def test_no_frontmatter_returns_full_content(self):
        content = "## Working On\nSome task\n"
        meta, body = parse_frontmatter(content)
        self.assertEqual(meta, {})
        self.assertEqual(body, content)

    def test_unclosed_frontmatter_returns_full_content(self):
        content = "---\nai: Claude\nno closing delimiter"
        meta, body = parse_frontmatter(content)
        self.assertEqual(meta, {})

    def test_empty_content(self):
        meta, body = parse_frontmatter("")
        self.assertEqual(meta, {})

    def test_frontmatter_with_colon_in_value(self):
        content = "---\nai: Claude Code (claude-sonnet-4-6)\nupdated: 2026-05-11 14:30:22\n---\nbody"
        meta, _ = parse_frontmatter(content)
        self.assertEqual(meta["ai"], "Claude Code (claude-sonnet-4-6)")
        self.assertEqual(meta["updated"], "2026-05-11 14:30:22")

# ── extract_section ─────────────────────────────────────────────────────────

class TestExtractSection(unittest.TestCase):

    def setUp(self):
        _, self.body = parse_frontmatter(SAMPLE_LOG)

    def test_extracts_working_on(self):
        result = extract_section(self.body, "Working On")
        self.assertIn("auth timeout", result)
        self.assertIn("JWT", result)

    def test_extracts_files_modified(self):
        result = extract_section(self.body, "Files Modified This Session")
        self.assertIn("src/auth.ts", result)
        self.assertIn("src/auth.test.ts", result)

    def test_extracts_decisions(self):
        result = extract_section(self.body, "Decisions Made")
        self.assertIn("15min JWT", result)

    def test_extracts_issues(self):
        result = extract_section(self.body, "Issues Identified")
        self.assertIn("race condition", result)

    def test_extracts_do_not_touch(self):
        result = extract_section(self.body, "Do Not Touch (Avoid Conflicts)")
        self.assertIn("src/auth.ts", result)

    def test_extracts_handoff_note(self):
        result = extract_section(self.body, "Handoff Note")
        self.assertIn("Race condition", result)

    def test_missing_section_returns_empty(self):
        result = extract_section(self.body, "Nonexistent Section")
        self.assertEqual(result, "")

    def test_section_stops_at_next_header(self):
        result = extract_section(self.body, "Working On")
        self.assertNotIn("Files Modified", result)

# ── collect_items ───────────────────────────────────────────────────────────

class TestCollectItems(unittest.TestCase):

    def test_returns_bullet_lines(self):
        text = "- item one\n- item two\n- item three"
        items = collect_items(text)
        self.assertEqual(len(items), 3)
        self.assertIn("- item one", items)

    def test_skips_empty_lines(self):
        text = "- item one\n\n- item two"
        items = collect_items(text)
        self.assertEqual(len(items), 2)

    def test_skips_lone_dash(self):
        text = "-\n- real item\n*"
        items = collect_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "- real item")

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(collect_items(""), [])

    def test_non_bullet_lines_included(self):
        text = "Some prose line\n- bullet"
        items = collect_items(text)
        self.assertEqual(len(items), 2)

# ── integration: main() with temp directory ─────────────────────────────────

class TestMainIntegration(unittest.TestCase):

    def _write_log(self, collab_dir, filename, ai, working, decisions="", modified="", issues="", dnt="", handoff=""):
        content = f"""---
ai: {ai}
session: 20260511-143022
project: test-project
updated: 2026-05-11 14:30:00
---

## Working On
{working}

## Files Modified This Session
{modified}

## Decisions Made
{decisions}

## Issues Identified
{issues}

## Do Not Touch (Avoid Conflicts)
{dnt}

## Handoff Note
{handoff}
"""
        (collab_dir / filename).write_text(content, encoding="utf-8")

    def test_generates_context_from_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collab_dir = Path(tmpdir) / ".ai-collab"
            collab_dir.mkdir()
            self._write_log(collab_dir, "claude-001.md",
                ai="Claude Code",
                working="Fixing auth bug in src/auth.ts",
                decisions="- Use 15min JWT expiry",
                issues="- src/auth.ts:42 race condition",
                dnt="- src/auth.ts — being refactored",
                handoff="Auth fix done, race condition next."
            )
            self._write_log(collab_dir, "cursor-001.md",
                ai="Cursor",
                working="Writing tests for auth module",
                modified="- src/auth.test.ts — added 5 tests"
            )

            # Run main with cwd set to tmpdir
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # Import and run main
                _main()
            finally:
                os.chdir(old_cwd)

            context_path = collab_dir / "CONTEXT.md"
            self.assertTrue(context_path.exists(), "CONTEXT.md should be created")
            context = context_path.read_text(encoding="utf-8")

            self.assertIn("Claude Code", context)
            self.assertIn("Cursor", context)
            self.assertIn("Fixing auth bug", context)
            self.assertIn("15min JWT", context)
            self.assertIn("race condition", context)
            self.assertIn("src/auth.ts", context)
            self.assertIn("Context for New AI", context)

    def test_empty_collab_dir_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collab_dir = Path(tmpdir) / ".ai-collab"
            collab_dir.mkdir()
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with self.assertRaises(SystemExit) as cm:
                    _main()
                self.assertEqual(cm.exception.code, 0)
            finally:
                os.chdir(old_cwd)

    def test_ignores_protocol_and_context_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collab_dir = Path(tmpdir) / ".ai-collab"
            collab_dir.mkdir()
            (collab_dir / "PROTOCOL.md").write_text("# Protocol", encoding="utf-8")
            (collab_dir / "CONTEXT.md").write_text("# Old context", encoding="utf-8")
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with self.assertRaises(SystemExit) as cm:
                    _main()
                self.assertEqual(cm.exception.code, 0)
            finally:
                os.chdir(old_cwd)

    def test_deduplicates_items_across_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            collab_dir = Path(tmpdir) / ".ai-collab"
            collab_dir.mkdir()
            same_decision = "- Use PostgreSQL for storage"
            self._write_log(collab_dir, "claude-001.md", ai="Claude", working="DB setup", decisions=same_decision)
            self._write_log(collab_dir, "cursor-001.md", ai="Cursor", working="DB setup", decisions=same_decision)
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                _main()
            finally:
                os.chdir(old_cwd)
            context = (collab_dir / "CONTEXT.md").read_text()
            self.assertEqual(context.count("Use PostgreSQL"), 1, "Duplicate decisions should be deduplicated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
