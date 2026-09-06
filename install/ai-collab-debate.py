#!/usr/bin/env python3
"""
Bounded multi-round agent debate for AI Collab.

Luis's spec (2026-08-17): agents should be able to go back and forth for two
or three turns each on a technical question, converge on a clear
implementation path, and stop at a written execution summary that the human
director must explicitly authorize before anyone touches code.

This helper is intentionally a thin conductor on top of the existing
`ai-collab-converse.py` delivery stack (internal-first, visible-chat
escalation, real-reply waiting). It adds only what was missing:
  - round-robin turns across participants, capped at N rounds each
  - stopping the instant a `type: decision` execution-summary message lands
  - a hard structural guarantee that this script never edits files or code
    itself -- it only ever sends/reads messages, so the human-authorization
    boundary cannot be bypassed by a prompting mistake.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISION_MARKERS = ("resumen de ejecucion", "resumen de ejecución", "execution summary")
# Generic on purpose: this skill runs in many different people's projects
# (not just one person's), so the closing line this tool teaches agents to
# write -- and the marker it looks for to know a round actually closed --
# must not hardcode any one person's name.
CLOSING_MARKERS = ("pendiente de autorizacion", "pendiente de autorización")
MESSAGE_RE = re.compile(
    r"(?m)^##\s+(?P<ts>\S+)\s+(?:--|—)\s+(?P<author>[a-zA-Z0-9_-]+)\s*\n"
    r"(?P<body>.*?)(?=^##\s+\S+\s+(?:--|—)\s+[a-zA-Z0-9_-]+\s*$|\Z)",
    re.DOTALL,
)
TYPE_RE = re.compile(r"(?m)^type:\s*(\S+)\s*$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except OSError:
        pass
    return Path(os.getcwd()).resolve()


def installed_helper(name: str) -> Path | None:
    env_name = f"AI_COLLAB_{name.upper().replace('-', '_')}_SCRIPT"
    configured = os.environ.get(env_name, "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().with_name(f"ai-collab-{name}.py"),
        Path.home() / ".claude" / f"ai-collab-{name}.py",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    return None


def resolve_participants(root: Path, explicit: str) -> list[str]:
    parsed = [p.strip() for p in explicit.split(",") if p.strip()]
    if parsed:
        return parsed
    manifest_path = root / ".ai-collab" / "agents.json"
    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    agents = manifest.get("agents") if isinstance(manifest, dict) else None
    slugs = [
        str(item.get("agent")).strip()
        for item in agents
        if isinstance(item, dict) and str(item.get("agent")).strip()
    ] if isinstance(agents, list) else []
    if not slugs:
        raise SystemExit("--participants is required (or .ai-collab/agents.json must list registered agents)")
    return slugs


def parse_messages(text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for match in MESSAGE_RE.finditer(text):
        body = match.group("body")
        type_match = TYPE_RE.search(body)
        messages.append(
            {
                "ts": match.group("ts"),
                "author": match.group("author"),
                "type": type_match.group(1) if type_match else "",
                "body": body.strip(),
            }
        )
    return messages


def find_decision(messages: list[dict[str, str]], after_ts: str) -> dict[str, str] | None:
    for message in messages:
        if message["ts"] <= after_ts:
            continue
        if message["type"] not in ("decision", "review", "answer", "proposal"):
            continue
        lowered = message["body"].lower()
        has_title = any(marker in lowered for marker in DECISION_MARKERS)
        if not has_title:
            continue
        # type: decision is the deliberate, correctly-tagged signal -- the
        # title marker alone is enough. Any other type is a real risk of a
        # false positive: an ordinary message that merely *talks about*
        # closing with a "RESUMEN DE EJECUCION" later (e.g. "dejo correr el
        # debate antes de cerrar con RESUMEN DE EJECUCION") would otherwise
        # be mistaken for the close itself. Require the literal closing
        # authorization line too, since that combination is not something
        # an agent would write in passing.
        if message["type"] != "decision" and not any(marker in lowered for marker in CLOSING_MARKERS):
            continue
        return message
    return None


def run_converse(
    helper: Path,
    root: Path,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(helper), "--root", str(root), *args]
    return subprocess.run(command, cwd=str(root), text=True, capture_output=True, check=False)


KICKOFF_PREAMBLE = (
    "MODO DEBATE (maximo {rounds} intervenciones por agente). Reglas:\n"
    "1) No edites ni crees archivos todavia -- esto es solo discusion tecnica.\n"
    "2) Cada intervencion: lee el hilo completo, suma tu opinion, senala riesgos "
    "o alternativas, y responde a lo que dijeron los demas (no repitas tu propio punto).\n"
    "3) En cuanto haya un camino de implementacion claro, quien lo vea mas nitido debe "
    "cerrar la ronda con un mensaje `type: decision` titulado 'RESUMEN DE EJECUCION' que "
    "incluya: el enfoque acordado, archivos/alcance afectado, riesgos abiertos, y la linea "
    "literal 'PENDIENTE DE AUTORIZACION -- no implementar hasta que el usuario confirme.'\n"
    "4) Nadie escribe codigo de este tema hasta que el usuario autorice ese resumen explicitamente.\n\n"
)


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    converse = installed_helper("converse")
    if not converse:
        raise SystemExit("ai-collab-converse.py is not installed")

    participants = resolve_participants(root, args.participants)

    thread_path: Path
    if args.thread:
        thread_path = (root / args.thread).resolve() if not Path(args.thread).is_absolute() else Path(args.thread)
        if not thread_path.exists():
            raise SystemExit(f"--thread does not exist: {thread_path}")
        cursor = isoformat_z(utc_now())
        print(f"[AI-COLLAB-DEBATE] Resuming thread: {thread_path}")
    else:
        message = KICKOFF_PREAMBLE.format(rounds=args.rounds) + args.message
        started_at = isoformat_z(utc_now())
        start_args = [
            "start",
            "--author",
            args.author,
            "--topic",
            args.topic,
            "--kind",
            "discussion",
            "--type",
            "question",
            "--to",
            ",".join(participants),
            "--tags",
            "debate-mode",
            "--message",
            message,
            "--wait-seconds",
            str(args.wait_seconds),
        ]
        completed = run_converse(converse, root, start_args)
        print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        thread_path = find_started_thread(root, args.topic, started_at)
        if not thread_path:
            raise SystemExit("Could not locate the discussion thread that was just created")
        cursor = started_at

    # Round-robin state. Picking "next agent" used to be inferred from the
    # thread's last message author, which broke as soon as a nudge escalated
    # to visible chat: that escalation appends its own handoff message
    # authored by the debate's own --author, so "whoever isn't the last
    # author" kept resolving to the first participant over and over whenever
    # that author wasn't itself a participant (see discussion-20260817-214951,
    # point 3 -- codex ate 6/6 turns because opencode was never picked).
    # Track whose turn it is explicitly instead of re-deriving it from text.
    total_rounds = args.rounds
    per_agent_used = {p: 0 for p in participants}
    per_agent_noreply_streak = {p: 0 for p in participants}
    queue = list(participants)
    # A no-reply attempt gets one immediate retry before yielding the floor,
    # so a single silent agent can delay but never fully consume the debate.
    max_attempts = total_rounds * len(participants) * 3
    attempt = 0
    decision: dict[str, str] | None = None

    def remaining() -> bool:
        return any(per_agent_used[p] < total_rounds for p in participants)

    while attempt < max_attempts and remaining():
        text = thread_path.read_text(encoding="utf-8")
        messages = parse_messages(text)
        decision = find_decision(messages, cursor)
        if decision:
            break

        if not queue:
            queue = [p for p in participants if per_agent_used[p] < total_rounds]
            if not queue:
                break
        next_agent = queue.pop(0)
        if per_agent_used[next_agent] >= total_rounds:
            continue

        attempt += 1
        print(
            f"[AI-COLLAB-DEBATE] Attempt {attempt}/{max_attempts} -> @{next_agent} "
            f"(used {per_agent_used[next_agent]}/{total_rounds} rounds)"
        )
        nudge_args = [
            "question",
            "--thread",
            thread_path.name,
            "--author",
            args.author,
            "--message",
            f"@{next_agent} tu turno en el debate (respondiendo a lo ultimo del hilo).",
            "--to",
            next_agent,
            "--wait-seconds",
            str(args.wait_seconds),
        ]
        completed = run_converse(converse, root, nudge_args)
        print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)

        # converse.py's question/dispatch_and_optionally_wait returns 0 only
        # once a real thread reply was verified for the recipient; any other
        # code (timeout, dispatch failure) means no real reply landed.
        if completed.returncode == 0:
            per_agent_used[next_agent] += 1
            per_agent_noreply_streak[next_agent] = 0
            if per_agent_used[next_agent] < total_rounds:
                queue.append(next_agent)
        else:
            per_agent_noreply_streak[next_agent] += 1
            print(
                f"[AI-COLLAB-DEBATE] No confirmed reply from @{next_agent} "
                f"(no-reply streak {per_agent_noreply_streak[next_agent]}); not counted as a used round."
            )
            if per_agent_noreply_streak[next_agent] < 2:
                queue.insert(0, next_agent)
            else:
                per_agent_noreply_streak[next_agent] = 0
                queue.append(next_agent)

        text = thread_path.read_text(encoding="utf-8")
        messages = parse_messages(text)
        decision = find_decision(messages, cursor)
        if decision:
            break

    text = thread_path.read_text(encoding="utf-8")
    messages = parse_messages(text)
    decision = decision or find_decision(messages, cursor)

    print("\n[AI-COLLAB-DEBATE] ---- RESULT ----")
    print(f"Thread: {thread_path}")
    if decision:
        print(f"Decision reached by @{decision['author']} at {decision['ts']}.")
        print("PENDIENTE DE AUTORIZACION DEL USUARIO antes de implementar.\n")
        print(decision["body"])
        return 0
    used_summary = ", ".join(f"@{p}={per_agent_used[p]}/{total_rounds}" for p in participants)
    print(f"No execution-summary decision after {attempt} attempts ({used_summary}).")
    print("Revisa el hilo y decide si conviene otra ronda o cerrarlo manualmente.")
    return 3


def find_started_thread(root: Path, topic: str, after_ts: str) -> Path | None:
    discussions_dir = root / ".ai-collab" / "discussions"
    if not discussions_dir.is_dir():
        return None
    candidates = sorted(discussions_dir.glob("discussion-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"topic: {topic}" in text:
            return candidate
    return candidates[0] if candidates else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded multi-round agent debate ending in a human-authorized execution summary.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Kick off (or resume) a bounded debate thread.")
    run.add_argument("--topic", required=True)
    run.add_argument("--message", default="", help="The technical question to debate.")
    run.add_argument("--author", required=True, help="Agent slug convening the debate (usually the director).")
    run.add_argument("--participants", default="", help="Comma-separated agent slugs, e.g. codex,opencode. Defaults to every registered agent in .ai-collab/agents.json.")
    run.add_argument("--rounds", type=int, default=3, help="Max turns per participant (default 3).")
    run.add_argument("--wait-seconds", type=int, default=240, help="Real-reply wait per turn (default 240s).")
    run.add_argument("--thread", default=None, help="Resume an existing thread file instead of starting a new one.")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
