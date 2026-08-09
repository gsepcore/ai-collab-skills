#!/usr/bin/env python3
"""
Multi-agent run orchestration for AI Collab.

This helper adds a safe director layer on top of the existing inbox/thread
protocol. It never replaces the base protocol: it creates run state under
`.ai-collab/runs/`, writes normal inbox files for assignments, and uses
top-level `thread-*.md` files so the existing daemon can wake mentioned agents.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_TASK_STATES = {"done", "failed"}
ACTIVE_INBOX_STATES = {"unread", "claimed", "running", "blocked", "review"}
DIRECTOR_ONLY_COMMANDS = {"add-task", "assign", "set-task", "finalize"}
TASK_STATES = {"planned", "assigned", "claimed", "running", "blocked", "review", "done", "failed"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str, fallback: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")
    return slug or fallback


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


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=False) + "\n")


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body_start = end + len("\n---")
    if text[body_start : body_start + 1] == "\n":
        body_start += 1
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip():
            meta[key.strip()] = value.strip()
    return meta, text[body_start:]


def render_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def collab_dir(root: Path) -> Path:
    return root / ".ai-collab"


def runs_dir(root: Path) -> Path:
    return collab_dir(root) / "runs"


def run_dir(root: Path, run_id: str) -> Path:
    return runs_dir(root) / run_id


def tasks_path(root: Path, run_id: str) -> Path:
    return run_dir(root, run_id) / "tasks.json"


def director_path(root: Path, run_id: str) -> Path:
    return run_dir(root, run_id) / "director.json"


def load_director(root: Path, run_id: str) -> dict[str, Any]:
    data = read_json(director_path(root, run_id), {})
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"Run not found: {run_id}")
    return data


def assert_director(root: Path, run_id: str, actor: str | None, force: bool = False) -> None:
    if force:
        return
    if not actor:
        raise SystemExit("--actor is required for director-only operations")
    director = load_director(root, run_id).get("director")
    if actor != director:
        raise SystemExit(f"Director lock is held by {director}; {actor} cannot perform this operation")


def load_tasks(root: Path, run_id: str) -> dict[str, Any]:
    data = read_json(tasks_path(root, run_id), {"schema": "ai-collab.tasks.v1", "tasks": []})
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return {"schema": "ai-collab.tasks.v1", "tasks": []}
    return data


def save_tasks(root: Path, run_id: str, data: dict[str, Any]) -> None:
    write_json(tasks_path(root, run_id), data)


def find_task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in data.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise SystemExit(f"Task not found: {task_id}")


def registered_agents(root: Path) -> set[str]:
    agents_path = collab_dir(root) / "agents.json"
    data = read_json(agents_path, {})
    result: set[str] = set()
    if isinstance(data, dict):
        for item in data.get("agents", []):
            if isinstance(item, dict) and item.get("agent"):
                result.add(str(item["agent"]))
    team_path = collab_dir(root) / "TEAM.md"
    if team_path.exists():
        for line in team_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                result.add(stripped[2:].split()[0].strip("*`"))
    return result


def load_role_profile(root: Path) -> dict[str, Any]:
    data = read_json(collab_dir(root) / "roles.json", {})
    if isinstance(data, dict) and isinstance(data.get("assignments"), dict):
        return data
    return {"assignments": {}}


def role_owner(root: Path, role: str) -> str:
    normalized = slugify(role)
    aliases = {
        "director": "senior-director",
        "senior": "senior-director",
        "db": "database",
        "data": "database",
        "security": "security-review",
        "architecture": "architecture-review",
        "functional": "functional-review",
        "functionality": "functional-review",
        "deploy": "deployment",
        "design": "ui-ux-design",
        "ui": "ui-ux-design",
        "ux": "ui-ux-design",
    }
    normalized = aliases.get(normalized, normalized)
    item = load_role_profile(root).get("assignments", {}).get(normalized, {})
    owner = item.get("primary") if isinstance(item, dict) else None
    if not owner:
        raise SystemExit(
            f"Development-team role is unassigned: {normalized}. "
            "Run /collab team configure or pass an explicit --owner."
        )
    return str(owner)


def assigned_role_agents(root: Path) -> list[str]:
    result: list[str] = []
    for item in load_role_profile(root).get("assignments", {}).values():
        owner = item.get("primary") if isinstance(item, dict) else None
        if owner and owner not in result:
            result.append(str(owner))
    return result


def thread_path(root: Path, task_id: str) -> Path:
    return collab_dir(root) / f"thread-{task_id}.md"


def append_thread(root: Path, run_id: str, task_id: str, author: str, message: str, now: datetime | None = None) -> Path:
    now = now or utc_now()
    path = thread_path(root, task_id)
    timestamp = isoformat_z(now)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    meta, body = parse_frontmatter(existing)
    if meta.get("status") == "closed":
        raise SystemExit(f"Thread is closed: {path}")
    if not meta:
        meta = {
            "thread": task_id,
            "run_id": run_id,
            "project": root.name,
            "inbox": f"inbox-{task_owner(root, run_id, task_id)}.md",
            "created": timestamp,
            "updated": timestamp,
            "participants": author,
            "status": "open",
        }
    participants = parse_csv(meta.get("participants"))
    if author not in participants:
        participants.append(author)
    meta["updated"] = timestamp
    meta["participants"] = ", ".join(sorted(participants))
    section = f"## {timestamp} -- {author}\n\n{message.strip()}\n\n---\n"
    new_body = f"{body.rstrip()}\n\n{section}" if body.strip() else section
    atomic_write(path, render_frontmatter(meta, new_body))
    return path


def task_owner(root: Path, run_id: str, task_id: str) -> str:
    task = find_task(load_tasks(root, run_id), task_id)
    return str(task.get("owner", ""))


def inbox_path(root: Path, owner: str) -> Path:
    return collab_dir(root) / f"inbox-{owner}.md"


def active_inbox(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, ""
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
    status = meta.get("status", "")
    return status in ACTIVE_INBOX_STATES, status


def write_status(root: Path, run_id: str, status: str, now: datetime | None = None) -> None:
    now = now or utc_now()
    director = load_director(root, run_id)
    tasks = load_tasks(root, run_id).get("tasks", [])
    counts: dict[str, int] = {}
    for task in tasks:
        if isinstance(task, dict):
            counts[str(task.get("status", "unknown"))] = counts.get(str(task.get("status", "unknown")), 0) + 1
    lines = [
        f"# Run Status: {run_id}",
        "",
        f"- director: {director.get('director')}",
        f"- status: {status}",
        f"- updated: {isoformat_z(now)}",
        f"- tasks: {json.dumps(counts, sort_keys=True)}",
        "",
    ]
    atomic_write(run_dir(root, run_id) / "status.md", "\n".join(lines))


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    now = utc_now()
    run_id = args.run_id or f"{now.strftime('%Y%m%d-%H%M%S')}-{slugify(args.title or args.goal, 'run')}"
    path = run_dir(root, run_id)
    if path.exists() and not args.force:
        raise SystemExit(f"Run already exists: {run_id}")
    path.mkdir(parents=True, exist_ok=True)

    director_slug = args.director or role_owner(root, "senior-director")
    agents = parse_csv(args.agents) or [agent for agent in assigned_role_agents(root) if agent != director_slug]
    roster = registered_agents(root)
    missing = sorted(agent for agent in [director_slug, *agents] if agent and roster and agent not in roster)
    if missing and not args.force:
        raise SystemExit(f"Agent(s) not registered in TEAM.md/agents.json: {', '.join(missing)}")

    director = {
        "schema": "ai-collab.director.v1",
        "run_id": run_id,
        "project": root.name,
        "director": director_slug,
        "director_lock": "active",
        "status": "planned",
        "started_by": args.started_by or "user",
        "created": isoformat_z(now),
        "updated": isoformat_z(now),
        "agents": agents,
        "safety": {
            "single_director_per_run": True,
            "no_active_inbox_overwrite": True,
            "owner_required_per_task": True,
            "allowed_files_required_for_code_tasks": True,
            "final_validation_required": True,
        },
    }
    write_json(director_path(root, run_id), director)
    write_json(tasks_path(root, run_id), {"schema": "ai-collab.tasks.v1", "run_id": run_id, "tasks": []})
    plan = [
        f"# Multi-Agent Plan: {args.title or run_id}",
        "",
        f"Run ID: `{run_id}`",
        f"Director: `{director_slug}`",
        f"Agents: `{', '.join(agents)}`",
        "",
        "## Goal",
        args.goal.strip(),
        "",
        "## Safety Rules",
        "- One active director controls this run.",
        "- Every task has one owner and explicit file boundaries.",
        "- Agents discuss questions in task threads before crossing boundaries.",
        "- Do not overwrite active inboxes; create a new task or wait for terminal status.",
        "- Final summary requires validation evidence.",
        "",
        "## Tasks",
        "Use `ai-collab-orchestrate.py add-task` to add implementation tasks.",
        "",
    ]
    atomic_write(path / "PLAN.md", "\n".join(plan))
    write_status(root, run_id, "planned", now)
    print(f"[AI-COLLAB] Run initialized: {run_id}")
    print(f"  director: {director_slug}")
    print(f"  path: {path}")
    return 0


def cmd_add_task(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    assert_director(root, args.run_id, args.actor, args.force)
    now = utc_now()
    data = load_tasks(root, args.run_id)
    task_id = args.task_id or f"{args.run_id}-{slugify(args.title)}"
    if any(isinstance(task, dict) and task.get("id") == task_id for task in data["tasks"]):
        raise SystemExit(f"Task already exists: {task_id}")
    requested_owner = (args.owner or "").strip()
    routed_owner = role_owner(root, args.role) if args.role and not requested_owner else ""
    owner = requested_owner or routed_owner
    if not owner:
        raise SystemExit("Task ownership requires --owner or a configured --role.")
    roster = registered_agents(root)
    if roster and owner not in roster and not args.force:
        raise SystemExit(f"Owner is not registered: {owner}")
    task = {
        "id": task_id,
        "title": args.title,
        "owner": owner,
        "role": args.role or "",
        "status": "planned",
        "priority": args.priority,
        "created": isoformat_z(now),
        "updated": isoformat_z(now),
        "depends_on": parse_csv(args.depends_on),
        "allowed_files": parse_csv(args.allowed_files),
        "do_not_touch": parse_csv(args.do_not_touch),
        "validation": args.validation or "",
        "description": args.description.strip(),
        "thread": f"thread-{task_id}.md",
    }
    data["tasks"].append(task)
    save_tasks(root, args.run_id, data)
    role_note = f" for role `{args.role}`" if args.role else ""
    append_thread(root, args.run_id, task_id, args.actor, f"Task created{role_note} for @{owner}: {args.description}")
    write_status(root, args.run_id, "planned")
    print(f"[AI-COLLAB] Task added: {task_id} -> {owner}")
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    assert_director(root, args.run_id, args.actor, args.force)
    now = utc_now()
    data = load_tasks(root, args.run_id)
    task = find_task(data, args.task_id)
    owner = str(task.get("owner", "")).strip()
    if not owner:
        raise SystemExit(f"Task has no owner: {args.task_id}")
    active, status = active_inbox(inbox_path(root, owner))
    if active and not args.force:
        raise SystemExit(f"inbox-{owner}.md is active ({status}); refusing to overwrite")
    task["status"] = "assigned"
    task["updated"] = isoformat_z(now)
    save_tasks(root, args.run_id, data)
    message = args.message or str(task.get("description", ""))
    body = f"""---
from: {args.actor}
to: {owner}
task_id: {task['id']}
run_id: {args.run_id}
priority: {task.get('priority', 'normal')}
updated: {isoformat_z(now)}
status: unread
attempts: 0
last_attempt:
claimed_by:
claimed_at:
done_at:
---

## Task
{message.strip()}

Team role: {task.get('role') or '(explicit owner)'}

## Boundaries
Allowed files: {', '.join(task.get('allowed_files') or ['(none specified; ask director before editing code)'])}
Do not touch: {', '.join(task.get('do_not_touch') or ['(none)'])}
Dependencies: {', '.join(task.get('depends_on') or ['(none)'])}

## Conversation
Use `.ai-collab/thread-{task['id']}.md` for questions, review requests, and handoff notes. Mention agents with `@slug` when you need an answer.

## Exit Criteria
- Update the task thread with a short summary.
- Mark this inbox `status: done` when complete, or `status: blocked` with the blocker.
- Update your session log.
- Validation: {task.get('validation') or 'director will validate'}
"""
    atomic_write(inbox_path(root, owner), body)
    append_thread(root, args.run_id, task["id"], args.actor, f"Assigned to @{owner}.\n\n{message}")
    write_status(root, args.run_id, "running")
    print(f"[AI-COLLAB] Task assigned: {task['id']} -> inbox-{owner}.md")
    return 0


def cmd_thread(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    path = append_thread(root, args.run_id, args.task_id, args.author, args.message)
    print(f"[AI-COLLAB] Thread updated: {path}")
    return 0


def cmd_set_task(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    if args.status not in TASK_STATES:
        raise SystemExit(f"Invalid status: {args.status}")
    assert_director(root, args.run_id, args.actor, args.force)
    now = utc_now()
    data = load_tasks(root, args.run_id)
    task = find_task(data, args.task_id)
    task["status"] = args.status
    task["updated"] = isoformat_z(now)
    if args.summary:
        task["summary"] = args.summary
        append_thread(root, args.run_id, args.task_id, args.actor, f"Status set to `{args.status}`.\n\n{args.summary}")
    save_tasks(root, args.run_id, data)
    write_status(root, args.run_id, "running")
    print(f"[AI-COLLAB] Task {args.task_id} -> {args.status}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    director = load_director(root, args.run_id)
    tasks = load_tasks(root, args.run_id).get("tasks", [])
    print(f"Run: {args.run_id}")
    print(f"Director: {director.get('director')} ({director.get('director_lock')})")
    print(f"Status: {director.get('status')}")
    for task in tasks:
        if isinstance(task, dict):
            print(f"- {task.get('id')} [{task.get('status')}] owner={task.get('owner')} title={task.get('title')}")
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    assert_director(root, args.run_id, args.actor, args.force)
    now = utc_now()
    director = load_director(root, args.run_id)
    data = load_tasks(root, args.run_id)
    unfinished = [task for task in data.get("tasks", []) if isinstance(task, dict) and task.get("status") not in TERMINAL_TASK_STATES]
    if unfinished and not args.force:
        raise SystemExit("Cannot finalize; unfinished tasks: " + ", ".join(str(task.get("id")) for task in unfinished))
    if not args.validation and not args.force:
        raise SystemExit("--validation is required to finalize")
    director["status"] = "completed"
    director["director_lock"] = "released"
    director["updated"] = isoformat_z(now)
    director["completed"] = isoformat_z(now)
    write_json(director_path(root, args.run_id), director)
    summary_lines = [
        f"# Final Summary: {args.run_id}",
        "",
        f"Director: `{director.get('director')}`",
        f"Completed: {isoformat_z(now)}",
        "",
        "## Result",
        args.summary.strip(),
        "",
        "## Validation",
        args.validation.strip(),
        "",
        "## Tasks",
    ]
    for task in data.get("tasks", []):
        if isinstance(task, dict):
            summary_lines.append(f"- `{task.get('id')}` [{task.get('status')}] {task.get('title')} - {task.get('summary', '')}")
    atomic_write(run_dir(root, args.run_id) / "final-summary.md", "\n".join(summary_lines) + "\n")
    write_status(root, args.run_id, "completed", now)
    print(f"[AI-COLLAB] Run finalized: {args.run_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coordinate a safe multi-agent AI Collab run.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a directed multi-agent run.")
    init.add_argument("--goal", required=True)
    init.add_argument("--director", default="", help="Run director. Defaults to the configured senior-director role.")
    init.add_argument("--agents", default="", help="Comma-separated participating agents. Defaults to agents with configured team roles.")
    init.add_argument("--title", default="")
    init.add_argument("--run-id", default="")
    init.add_argument("--started-by", default="user")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    add = sub.add_parser("add-task", help="Add a task to a run.")
    add.add_argument("--run-id", required=True)
    add.add_argument("--actor", required=True)
    add.add_argument("--task-id", default="")
    add.add_argument("--title", required=True)
    add.add_argument("--owner", default="", help="Explicit owner. Optional when --role has a configured primary agent.")
    add.add_argument("--role", default="", help="Development-team role used to route this task, e.g. frontend or qa.")
    add.add_argument("--description", required=True)
    add.add_argument("--priority", default="normal")
    add.add_argument("--depends-on", default="")
    add.add_argument("--allowed-files", default="")
    add.add_argument("--do-not-touch", default="")
    add.add_argument("--validation", default="")
    add.add_argument("--force", action="store_true")
    add.set_defaults(func=cmd_add_task)

    assign = sub.add_parser("assign", help="Write a task to its owner's inbox.")
    assign.add_argument("--run-id", required=True)
    assign.add_argument("--actor", required=True)
    assign.add_argument("--task-id", required=True)
    assign.add_argument("--message", default="")
    assign.add_argument("--force", action="store_true")
    assign.set_defaults(func=cmd_assign)

    thread = sub.add_parser("thread", help="Append an agent-to-agent thread message.")
    thread.add_argument("--run-id", required=True)
    thread.add_argument("--task-id", required=True)
    thread.add_argument("--author", required=True)
    thread.add_argument("--message", required=True)
    thread.set_defaults(func=cmd_thread)

    set_task = sub.add_parser("set-task", help="Set a task status.")
    set_task.add_argument("--run-id", required=True)
    set_task.add_argument("--actor", required=True)
    set_task.add_argument("--task-id", required=True)
    set_task.add_argument("--status", required=True)
    set_task.add_argument("--summary", default="")
    set_task.add_argument("--force", action="store_true")
    set_task.set_defaults(func=cmd_set_task)

    status = sub.add_parser("status", help="Print run status.")
    status.add_argument("--run-id", required=True)
    status.set_defaults(func=cmd_status)

    finalize = sub.add_parser("finalize", help="Finalize a run with validation evidence.")
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--actor", required=True)
    finalize.add_argument("--summary", required=True)
    finalize.add_argument("--validation", default="")
    finalize.add_argument("--force", action="store_true")
    finalize.set_defaults(func=cmd_finalize)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
