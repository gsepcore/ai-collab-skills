#!/usr/bin/env python3
"""Persistent agent identity and per-runtime session registration for AI Collab."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def agent_record(root: Path, slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(root / ".ai-collab" / "agents.json")
    for row in manifest.get("agents", []):
        if isinstance(row, dict) and row.get("agent") == slug:
            return manifest, row
    raise SystemExit(f"Agent is not registered in this project: {slug}. Run /collab setup first.")


def new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"ses_{stamp}_{secrets.token_hex(6)}"


def register(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    manifest, agent = agent_record(root, args.agent)
    expected_agent_id = str(agent.get("agent_id") or "")
    if not expected_agent_id:
        raise SystemExit("agents.json has no agent_id; rerun /collab setup to migrate identities.")
    if args.agent_id and args.agent_id != expected_agent_id:
        raise SystemExit(f"agent_id mismatch for {args.agent}: expected {expected_agent_id}")
    sessions = root / ".ai-collab" / "live" / "sessions"
    requested_pid = args.pid or os.getppid()
    current = read_json(sessions / f"current-{args.agent}.json")
    reusable = (
        not args.session_id
        and not getattr(args, "new", False)
        and current.get("status") == "active"
        and current.get("agent_id") == expected_agent_id
        and int(current.get("pid") or 0) == requested_pid
        and current.get("surface_kind") == args.surface_kind
    )
    session_id = str(current.get("session_id")) if reusable else (args.session_id or new_session_id())
    path = sessions / f"{session_id}.json"
    if path.exists():
        current = read_json(path)
        if current.get("agent_id") != expected_agent_id:
            raise SystemExit("session_id already belongs to another agent identity")
    timestamp = now_z()
    payload = {
        "schema": "ai-collab.session.v1",
        "project": root.name,
        "project_path": str(root),
        "project_id": manifest.get("project_id"),
        "agent": args.agent,
        "agent_id": expected_agent_id,
        "session_id": session_id,
        "container": args.container or agent.get("container") or "unknown",
        "surface_kind": args.surface_kind,
        "surface_id": args.surface_id or f"{args.surface_kind}:{session_id}",
        "pid": requested_pid,
        "tty": args.tty or "",
        "host_pid": args.host_pid or 0,
        "adapter": args.adapter or "",
        "status": "active",
        "started": read_json(path).get("started") or timestamp,
        "heartbeat_at": timestamp,
    }
    atomic_write(path, payload)
    atomic_write(sessions / f"current-{args.agent}.json", payload)
    print(json.dumps(payload, indent=2))
    return 0


def heartbeat(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    path = root / ".ai-collab" / "live" / "sessions" / f"{args.session_id}.json"
    payload = read_json(path)
    if not payload:
        raise SystemExit(f"Unknown session_id: {args.session_id}")
    payload["heartbeat_at"] = now_z()
    payload["status"] = args.status
    atomic_write(path, payload)
    atomic_write(path.parent / f"current-{payload['agent']}.json", payload)
    print(json.dumps(payload, indent=2))
    return 0


def resolve(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    sessions = root / ".ai-collab" / "live" / "sessions"
    candidates: list[dict[str, Any]] = []
    for path in sessions.glob("ses_*.json"):
        payload = read_json(path)
        if payload.get("status") != "active":
            continue
        if args.agent and payload.get("agent") != args.agent:
            continue
        if args.agent_id and payload.get("agent_id") != args.agent_id:
            continue
        candidates.append(payload)
    candidates.sort(key=lambda item: str(item.get("heartbeat_at") or ""), reverse=True)
    print(json.dumps({"sessions": candidates}, indent=2))
    return 0 if candidates else 3


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Register and resolve exact AI Collab runtime sessions.")
    sub = result.add_subparsers(dest="command", required=True)
    register_parser = sub.add_parser("register")
    register_parser.add_argument("--root", required=True)
    register_parser.add_argument("--agent", required=True)
    register_parser.add_argument("--agent-id", default="")
    register_parser.add_argument("--session-id", default="")
    register_parser.add_argument("--new", action="store_true", help="force a new session code even for the same runtime PID")
    register_parser.add_argument("--container", default="")
    register_parser.add_argument("--surface-kind", default="process", choices=["process", "terminal", "ide-native-chat", "api"])
    register_parser.add_argument("--surface-id", default="")
    register_parser.add_argument("--pid", type=int, default=0)
    register_parser.add_argument("--tty", default="")
    register_parser.add_argument("--host-pid", type=int, default=0)
    register_parser.add_argument("--adapter", default="")
    register_parser.set_defaults(func=register)
    heartbeat_parser = sub.add_parser("heartbeat")
    heartbeat_parser.add_argument("--root", required=True)
    heartbeat_parser.add_argument("--session-id", required=True)
    heartbeat_parser.add_argument("--status", default="active", choices=["active", "idle", "blocked", "done"])
    heartbeat_parser.set_defaults(func=heartbeat)
    resolve_parser = sub.add_parser("resolve")
    resolve_parser.add_argument("--root", required=True)
    resolve_parser.add_argument("--agent", default="")
    resolve_parser.add_argument("--agent-id", default="")
    resolve_parser.set_defaults(func=resolve)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
