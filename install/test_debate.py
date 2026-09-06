#!/usr/bin/env python3
"""
Tests for ai-collab-debate.py

Covers the round-robin fairness fix from discussion-20260817-214951 (point 3):
picking "whose turn it is" from the thread's last-message author broke as
soon as a nudge escalated to visible chat, because that escalation appends
its own handoff message authored by the debate's own --author (not a
participant) -- so "the first participant that isn't the last author" kept
resolving to the same agent every time. A silent participant (e.g. codex,
historically silent for months) could eat every round while the other
participant never got a turn.

Run with: python3 -m pytest install/test_debate.py -v
       or: python3 install/test_debate.py
"""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ai_collab_debate",
    Path(__file__).parent / "ai-collab-debate.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ai_collab_debate"] = _mod
_spec.loader.exec_module(_mod)


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FakeConverse:
    """Stands in for the real ai-collab-converse.py subprocess.

    `silent_agents` never produce a real reply (mirrors codex's historical
    behavior): every nudge to them appends a system-authored visible-escalation
    handoff (authored by the debate's own --author, exactly like the real
    tool does) and returns exit code 3, matching converse.py's
    `dispatch_and_optionally_wait` contract (0 == real reply verified, 3 ==
    visible prompt delivered but no real reply observed).
    """

    def __init__(self, silent_agents: set[str]):
        self.silent_agents = silent_agents
        self.calls: list[str] = []
        self._clock = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

    def _next_ts(self) -> str:
        self._clock += timedelta(seconds=1)
        return _isoformat(self._clock)

    def _append(self, thread_path: Path, author: str, message: str) -> None:
        text = thread_path.read_text(encoding="utf-8")
        text = text.rstrip() + f"\n\n## {self._next_ts()} -- {author}\n\n{message}\n"
        thread_path.write_text(text, encoding="utf-8")

    def __call__(self, helper: Path, root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args[args.index("--to") + 1] if "--to" in args else "")
        thread_name = args[args.index("--thread") + 1]
        author = args[args.index("--author") + 1]
        to = args[args.index("--to") + 1]
        thread_path = Path(thread_name) if Path(thread_name).is_absolute() else root / ".ai-collab" / "discussions" / thread_name

        if to in self.silent_agents:
            self._append(
                thread_path,
                author,
                f"type: handoff\nto: {to}\ntags: visible-escalation\n\n@{to} Visible fallback after the internal grace period expired.",
            )
            return subprocess.CompletedProcess(
                args, 3, stdout="", stderr="[AI-COLLAB] ERROR: visible prompts were submitted but real replies were not observed from: " + to
            )

        self._append(thread_path, to, f"type: answer\n\n@{author} respuesta real de {to}.")
        return subprocess.CompletedProcess(args, 0, stdout="[AI-COLLAB] Verified real thread replies from: " + to, stderr="")


class TestResolveParticipants(unittest.TestCase):
    def test_explicit_participants_are_used_verbatim(self):
        result = _mod.resolve_participants(Path("/any"), " codex , opencode ")

        self.assertEqual(result, ["codex", "opencode"])

    def test_default_resolves_to_every_registered_agent(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / ".ai-collab").mkdir(parents=True)
        (root / ".ai-collab" / "agents.json").write_text(
            json.dumps(
                {
                    "schema": "ai-collab.agents.v2",
                    "project_id": "prj_test",
                    "agents": [
                        {"agent": "claude-code", "agent_id": "agt_1", "container": "vscode"},
                        {"agent": "codex", "agent_id": "agt_2", "container": "vscode"},
                        {"agent": "opencode", "agent_id": "agt_3", "container": "vscode"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            result = _mod.resolve_participants(root, "")
        finally:
            tmp.cleanup()

        self.assertEqual(result, ["claude-code", "codex", "opencode"])

    def test_missing_agents_file_requires_explicit_participants(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            with self.assertRaises(SystemExit):
                _mod.resolve_participants(Path(tmp.name), "")
        finally:
            tmp.cleanup()


class TestFindDecision(unittest.TestCase):
    # Reproduces a real false positive hit live on 2026-08-20: claude-code's
    # own type:answer message said "...antes de cerrar con RESUMEN DE
    # EJECUCION" (talking ABOUT closing later, not closing now) and got
    # mistaken for the actual close, ending the debate after a single
    # round with no real convergence from opencode/codex.

    def _thread(self, *messages: str) -> str:
        return "".join(messages)

    def _message(self, ts: str, author: str, type_: str, body: str) -> str:
        return f"## {ts} -- {author}\n\ntype: {type_}\n\n{body}\n\n"

    def test_answer_that_merely_mentions_closing_later_is_not_a_decision(self):
        text = self._thread(
            self._message(
                "2026-08-20T11:41:08Z",
                "claude-code",
                "answer",
                "De acuerdo con tus 4 puntos. Dejo correr el round-robin antes de cerrar con RESUMEN DE EJECUCION.",
            )
        )
        messages = _mod.parse_messages(text)

        decision = _mod.find_decision(messages, "2026-08-20T11:00:00Z")

        self.assertIsNone(decision)

    def test_type_decision_with_title_alone_is_detected(self):
        text = self._thread(
            self._message(
                "2026-08-20T12:00:00Z",
                "opencode",
                "decision",
                "RESUMEN DE EJECUCION -- cierro esta ronda.\nEnfoque acordado: X.",
            )
        )
        messages = _mod.parse_messages(text)

        decision = _mod.find_decision(messages, "2026-08-20T11:00:00Z")

        self.assertIsNotNone(decision)
        self.assertEqual(decision["author"], "opencode")

    def test_fallback_type_requires_both_title_and_closing_line(self):
        text = self._thread(
            self._message(
                "2026-08-20T12:00:00Z",
                "opencode",
                "review",
                "RESUMEN DE EJECUCION -- esto es un cierre real.\n"
                "PENDIENTE DE AUTORIZACION DE LUIS -- no implementar hasta que el confirme.",
            )
        )
        messages = _mod.parse_messages(text)

        decision = _mod.find_decision(messages, "2026-08-20T11:00:00Z")

        self.assertIsNotNone(decision)

    def test_fallback_type_detects_closing_line_for_any_user_not_just_luis(self):
        # This skill runs in many different students' projects, not just one
        # person's -- the closing line/marker must not be hardcoded to one
        # name (confirmed: it used to require the literal text "de luis").
        text = self._thread(
            self._message(
                "2026-08-20T12:00:00Z",
                "opencode",
                "review",
                "RESUMEN DE EJECUCION -- esto es un cierre real.\n"
                "PENDIENTE DE AUTORIZACION -- no implementar hasta que el usuario confirme.",
            )
        )
        messages = _mod.parse_messages(text)

        decision = _mod.find_decision(messages, "2026-08-20T11:00:00Z")

        self.assertIsNotNone(decision)

    def test_fallback_type_with_only_title_is_not_a_decision(self):
        text = self._thread(
            self._message(
                "2026-08-20T12:00:00Z",
                "opencode",
                "proposal",
                "Mi propuesta apunta a un RESUMEN DE EJECUCION mas adelante, todavia no.",
            )
        )
        messages = _mod.parse_messages(text)

        decision = _mod.find_decision(messages, "2026-08-20T11:00:00Z")

        self.assertIsNone(decision)


class TestDebateRoundRobin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        discussions = self.root / ".ai-collab" / "discussions"
        discussions.mkdir(parents=True)
        self.thread = discussions / "discussion-test.md"
        self.thread.write_text(
            "---\nthread: discussion-test\nparticipants: claude-code, codex, opencode\nstatus: open\n---\n"
            "## 2026-08-20T11:59:00Z -- claude-code\n\ntype: question\n\nkickoff\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_debate(self, *, silent_agents: set[str], rounds: int = 3, wait_seconds: int = 5):
        fake = FakeConverse(silent_agents)
        args = _mod.build_parser().parse_args(
            [
                "--root",
                str(self.root),
                "run",
                "--topic",
                "test",
                "--author",
                "claude-code",
                "--participants",
                "codex,opencode",
                "--rounds",
                str(rounds),
                "--wait-seconds",
                str(wait_seconds),
                "--thread",
                str(self.thread),
            ]
        )
        old = _mod.run_converse
        _mod.run_converse = fake
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = _mod.cmd_run(args)
        finally:
            _mod.run_converse = old
        return code, buf.getvalue(), fake

    def test_silent_participant_does_not_starve_the_responsive_one(self):
        # This exact shape (codex permanently silent, opencode always
        # replying) is what actually happened in discussion-20260817-214951:
        # codex consumed 6/6 turns and opencode never got called on.
        code, output, fake = self.run_debate(silent_agents={"codex"})

        self.assertIn("@opencode=3/3", output)
        self.assertIn("@codex=0/3", output)
        # opencode must have actually been nudged 3 times, not skipped.
        self.assertEqual(fake.calls.count("opencode"), 3)
        # The debate must still terminate (bounded by the safety cap), not
        # hang forever nudging the silent agent.
        self.assertEqual(code, 3)

    def test_no_reply_attempt_does_not_consume_a_used_round(self):
        # A single timeout must not itself exhaust a participant's rounds --
        # only a confirmed real reply should.
        code, output, fake = self.run_debate(silent_agents={"codex"}, rounds=1)

        self.assertIn("No confirmed reply from @codex", output)
        self.assertIn("@codex=0/1", output)
        self.assertIn("@opencode=1/1", output)

    def test_both_responsive_participants_get_fair_turns(self):
        code, output, fake = self.run_debate(silent_agents=set(), rounds=2)

        self.assertEqual(fake.calls.count("codex"), 2)
        self.assertEqual(fake.calls.count("opencode"), 2)
        self.assertIn("@codex=2/2", output)
        self.assertIn("@opencode=2/2", output)

    def test_stops_as_soon_as_execution_summary_decision_lands(self):
        fake = FakeConverse(silent_agents=set())

        def fake_with_decision(helper, root, args):
            to = args[args.index("--to") + 1]
            if to == "opencode" and fake.calls.count("opencode") == 0:
                fake.calls.append(to)
                text = self.thread.read_text(encoding="utf-8")
                # Must be after cmd_run's own cursor (real utc_now() at resume
                # time, since this test resumes an existing --thread) or
                # find_decision ignores it as a stale pre-debate message. A
                # hardcoded past timestamp broke this test the moment real
                # wall-clock time passed it (caught 2026-08-27).
                decision_ts = _isoformat(datetime.now(timezone.utc) + timedelta(minutes=5))
                text += (
                    f"\n\n## {decision_ts} -- opencode\n\n"
                    "type: decision\n\nRESUMEN DE EJECUCION -- test.\n"
                    "PENDIENTE DE AUTORIZACION DE LUIS -- no implementar hasta que el confirme.\n"
                )
                self.thread.write_text(text, encoding="utf-8")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return fake(helper, root, args)

        old = _mod.run_converse
        _mod.run_converse = fake_with_decision
        args = _mod.build_parser().parse_args(
            [
                "--root",
                str(self.root),
                "run",
                "--topic",
                "test",
                "--author",
                "claude-code",
                "--participants",
                "codex,opencode",
                "--rounds",
                "3",
                "--wait-seconds",
                "5",
                "--thread",
                str(self.thread),
            ]
        )
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = _mod.cmd_run(args)
        finally:
            _mod.run_converse = old

        self.assertEqual(code, 0)
        self.assertIn("RESUMEN DE EJECUCION", buf.getvalue())
        # Stopped right after the decision landed, not after burning every round.
        self.assertLessEqual(len(fake.calls), 2)


if __name__ == "__main__":
    unittest.main()
