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
        values = list(args)
        if values and values[0] in {
            "start", "reply", "question", "answer", "proposal", "decision", "blocker", "review", "handoff"
        } and "--queue-only" not in values:
            values.append("--queue-only")
        return _mod.main(["--root", str(self.root), *values])

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

    def test_incidental_at_annotation_is_not_registered_as_participant(self):
        (self.collab / "agents.json").write_text(
            json.dumps(
                {
                    "agents": [
                        {"agent": "codex"},
                        {"agent": "opencode"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.run_cli(
            "start",
            "--author",
            "opencode",
            "--topic",
            "Visual coordinates",
            "--to",
            "codex",
            "--message",
            "The Codex label is visible @left:1408 in the right pane.",
        )

        meta, _body = _mod.parse_frontmatter(self.only_discussion().read_text(encoding="utf-8"))
        self.assertEqual(meta["participants"], "codex, opencode")

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

    def test_stable_discussion_id_reuses_one_file_across_retries(self):
        for message in ("First attempt.", "Retry after wake repair."):
            self.run_cli(
                "start",
                "--author",
                "codex",
                "--topic",
                "Architecture debate",
                "--discussion-id",
                "run-42-technical-kickoff",
                "--to",
                "opencode",
                "--message",
                message,
            )

        path = self.collab / "discussions" / "discussion-run-42-technical-kickoff.md"
        self.assertTrue(path.is_file())
        self.assertEqual(len(list((self.collab / "discussions").glob("*.md"))), 1)
        text = path.read_text(encoding="utf-8")
        self.assertIn("First attempt.", text)
        self.assertIn("Retry after wake repair.", text)

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

    def test_visible_dispatch_failure_is_reported_and_not_treated_as_reply(self):
        original = _mod.dispatch_visible
        original_visual = _mod.visual_proof
        original_prepare = _mod.prepare_visible_surfaces
        _mod.dispatch_visible = lambda path, root, targets=None: {"ok": False, "reason": "no visible bridge"}
        _mod.prepare_visible_surfaces = lambda root, targets: {"ok": True, "result": {"action": "prepare-visible"}}
        _mod.visual_proof = lambda root, agents, stage: {
            "ok": True,
            "result": {
                "visual_roster": str(root / ".ai-collab/live/visual-roster.json"),
                "screenshot": {"path": str(root / ".ai-collab/live/screenshots/team.png")},
            },
        }
        try:
            result = _mod.main(
                [
                    "--root", str(self.root), "start", "--author", "codex", "--topic", "Kickoff",
                    "--to", "claude-code,opencode", "--message", "Give your technical opinion.",
                    "--internal-wait-seconds", "0",
                ]
            )
        finally:
            _mod.dispatch_visible = original
            _mod.visual_proof = original_visual
            _mod.prepare_visible_surfaces = original_prepare
        self.assertEqual(result, 2)
        self.assertTrue(self.only_discussion().exists())

    def test_observe_mode_keeps_eyes_but_visual_ambiguity_does_not_block_delivery(self):
        path = self.collab / "thread-observe.md"
        kickoff = "2026-08-09T12:00:00Z"
        _mod.append_message(
            path, root=self.root, author="codex", message="@opencode review",
            recipients=["opencode"], now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        originals = (_mod.dispatch_visible, _mod.prepare_visible_surfaces, _mod.visual_proof, _mod.emit_escalation_notice)
        proof_calls = []
        dispatches = []
        _mod.dispatch_visible = lambda path, root, targets=None: dispatches.append(targets) or {"ok": True, "result": {}}
        _mod.prepare_visible_surfaces = lambda root, targets: {"ok": False, "reason": "stale route"}
        _mod.visual_proof = lambda root, agents, stage: proof_calls.append(stage) or {"ok": False, "reason": "ambiguous roster"}
        _mod.emit_escalation_notice = lambda *args, **kwargs: None
        try:
            result = _mod.dispatch_and_optionally_wait(
                path, root=self.root, author="codex", recipients=["opencode"], kickoff_at=kickoff,
                queue_only=False, internal_wait_seconds=0, wait_seconds=0,
                visual_agents=["codex", "opencode"], requested_visual_mode="observe",
            )
        finally:
            (_mod.dispatch_visible, _mod.prepare_visible_surfaces, _mod.visual_proof, _mod.emit_escalation_notice) = originals

        self.assertEqual(result, 0)
        self.assertEqual(dispatches, [["opencode"]])
        self.assertEqual(proof_calls, ["before-visible-turn", "after-visible-turn"])

    def test_strict_visual_mode_still_fails_closed(self):
        path = self.collab / "thread-strict.md"
        kickoff = "2026-08-09T12:00:00Z"
        _mod.append_message(
            path, root=self.root, author="codex", message="@opencode audit",
            recipients=["opencode"], now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        originals = (_mod.dispatch_visible, _mod.prepare_visible_surfaces, _mod.emit_escalation_notice)
        dispatches = []
        _mod.dispatch_visible = lambda path, root, targets=None: dispatches.append(targets) or {"ok": True}
        _mod.prepare_visible_surfaces = lambda root, targets: {"ok": False, "reason": "stale route"}
        _mod.emit_escalation_notice = lambda *args, **kwargs: None
        try:
            result = _mod.dispatch_and_optionally_wait(
                path, root=self.root, author="codex", recipients=["opencode"], kickoff_at=kickoff,
                queue_only=False, internal_wait_seconds=0, wait_seconds=0,
                visual_agents=["codex", "opencode"], requested_visual_mode="strict",
            )
        finally:
            (_mod.dispatch_visible, _mod.prepare_visible_surfaces, _mod.emit_escalation_notice) = originals

        self.assertEqual(result, 4)
        self.assertEqual(dispatches, [])

    def test_only_codex_skips_internal_grace(self):
        (self.collab / "capabilities.json").write_text(
            json.dumps({"agents": [{
                "agent": "claude-code-ide",
                "delivery": {"primary": "visible-chat"},
                "wake_policy": {"internal_grace_seconds": 7},
            }]}),
            encoding="utf-8",
        )

        self.assertEqual(_mod.internal_grace_seconds(self.root, "claude-code-ide", -1), 7)
        self.assertEqual(_mod.internal_grace_seconds(self.root, "codex", 30), 0)

    def test_internal_reply_skips_visible_escalation(self):
        path = self.collab / "thread-internal.md"
        kickoff = "2026-08-09T12:00:00Z"
        _mod.append_message(
            path,
            root=self.root,
            author="codex",
            message="@opencode please review",
            recipients=["opencode"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        _mod.append_message(
            path,
            root=self.root,
            author="opencode",
            message="@codex Claimed; I am reviewing now.",
            recipients=["codex"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 1, tzinfo=_mod.timezone.utc),
        )
        original = _mod.dispatch_visible
        visible_calls = []
        _mod.dispatch_visible = lambda path, root, targets=None: visible_calls.append(targets)
        try:
            result = _mod.dispatch_and_optionally_wait(
                path,
                root=self.root,
                author="codex",
                recipients=["opencode"],
                kickoff_at=kickoff,
                queue_only=False,
                internal_wait_seconds=15,
                wait_seconds=0,
                visual_agents=["codex", "opencode"],
            )
        finally:
            _mod.dispatch_visible = original

        self.assertEqual(result, 0)
        self.assertEqual(visible_calls, [])

    def test_timeout_notifies_before_targeted_visible_escalation(self):
        path = self.collab / "thread-timeout.md"
        kickoff = "2026-08-09T12:00:00Z"
        _mod.append_message(
            path,
            root=self.root,
            author="codex",
            message="@claude-code @opencode please review",
            recipients=["claude-code", "opencode"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        _mod.append_message(
            path,
            root=self.root,
            author="claude-code",
            message="@codex Claimed internally.",
            recipients=["codex"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 1, tzinfo=_mod.timezone.utc),
        )
        original_dispatch = _mod.dispatch_visible
        original_visual = _mod.visual_proof
        original_notice = _mod.emit_escalation_notice
        original_prepare = _mod.prepare_visible_surfaces
        visible_targets = []
        notices = []
        _mod.dispatch_visible = lambda path, root, targets=None: (
            visible_targets.append(targets) or {"ok": True, "result": {"action": "thread-mentions"}}
        )
        _mod.visual_proof = lambda root, agents, stage: {"ok": True, "skipped": True}
        _mod.emit_escalation_notice = lambda root, targets, source, grace: notices.append((targets, grace))
        _mod.prepare_visible_surfaces = lambda root, targets: {"ok": True, "result": {"targets": targets}}
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                result = _mod.dispatch_and_optionally_wait(
                    path,
                    root=self.root,
                    author="codex",
                    recipients=["claude-code", "opencode"],
                    kickoff_at=kickoff,
                    queue_only=False,
                    internal_wait_seconds=0,
                    wait_seconds=0,
                    visual_agents=["codex", "claude-code", "opencode"],
                )
        finally:
            _mod.dispatch_visible = original_dispatch
            _mod.visual_proof = original_visual
            _mod.emit_escalation_notice = original_notice
            _mod.prepare_visible_surfaces = original_prepare

        self.assertEqual(result, 0)
        self.assertEqual(visible_targets, [["opencode"]])
        self.assertEqual(notices, [(["opencode"], 0)])
        text = output.getvalue()
        self.assertLess(text.index("NOTICE:"), text.index("Visible escalation:"))

    def test_legacy_bridge_focus_submit_is_verified_then_followed_with_evidence(self):
        path = self.collab / "thread-legacy.md"
        kickoff = "2026-08-09T12:00:00Z"
        _mod.append_message(
            path,
            root=self.root,
            author="codex",
            message="@opencode please review",
            recipients=["opencode"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        originals = (
            _mod.dispatch_visible,
            _mod.visual_proof,
            _mod.emit_escalation_notice,
            _mod.prepare_visible_surfaces,
        )
        dispatches = []
        _mod.dispatch_visible = lambda path, root, targets=None: (
            dispatches.append(targets) or {"ok": True, "result": {"action": "thread-mentions"}}
        )
        _mod.visual_proof = lambda root, agents, stage: {
            "ok": True,
            "result": {
                "visual_roster": str(root / ".ai-collab/live/visual-roster.json"),
                "screenshot": {"path": str(root / ".ai-collab/live/screenshots/team.png")},
            },
        }
        _mod.emit_escalation_notice = lambda *args, **kwargs: None
        _mod.prepare_visible_surfaces = lambda root, targets: {
            "ok": True,
            "result": {
                "results": [{"target_slug": "opencode", "status": "legacy-focus-on-submit"}]
            },
        }
        try:
            result = _mod.dispatch_and_optionally_wait(
                path,
                root=self.root,
                author="codex",
                recipients=["opencode"],
                kickoff_at=kickoff,
                queue_only=False,
                internal_wait_seconds=0,
                wait_seconds=0,
                visual_agents=["codex", "opencode"],
            )
        finally:
            (
                _mod.dispatch_visible,
                _mod.visual_proof,
                _mod.emit_escalation_notice,
                _mod.prepare_visible_surfaces,
            ) = originals

        self.assertEqual(result, 0)
        self.assertEqual(dispatches, [["opencode"], ["opencode"]])
        self.assertIn("visible-escalation", path.read_text(encoding="utf-8"))

    def test_reply_evidence_requires_agent_authored_message(self):
        path = self.collab / "thread-kickoff.md"
        _mod.append_message(
            path,
            root=self.root,
            author="codex",
            message="@claude-code please reply",
            recipients=["claude-code"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 0, tzinfo=_mod.timezone.utc),
        )
        self.assertEqual(_mod.message_authors_after(path, "2026-08-09T12:00:00Z"), set())
        _mod.append_message(
            path,
            root=self.root,
            author="claude-code",
            message="@codex My recommendation is to fail closed.",
            recipients=["codex"],
            now=_mod.datetime(2026, 8, 9, 12, 0, 1, tzinfo=_mod.timezone.utc),
        )
        self.assertEqual(_mod.message_authors_after(path, "2026-08-09T12:00:00Z"), {"claude-code"})


if __name__ == "__main__":
    unittest.main()
