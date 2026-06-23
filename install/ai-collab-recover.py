#!/usr/bin/env python3
"""
AI Collab reboot/session recovery.

Restores the durable project context after a cold start without relying on an
agent's in-memory session. It is intentionally conservative: it regenerates
CONTEXT.md when needed, clears stale wakeup dedupe entries for unfinished inbox
tasks, and writes a project-local recovery report.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLAUDE_DIR = Path.home() / ".claude"
SUMMARY_SCRIPT = CLAUDE_DIR / "ai-collab-summary.py"
WAKEUP_STATE_FILE = Path.home() / ".ai-collab-wakeup-state.json"
TERMINAL_STATUSES = {"done", "failed"}
RECOVERABLE_STATUSES = {"unread", "claimed", "running", "blocked", "review"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _sep, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :])


def render_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append(body.lstrip("\n"))
    return "\n".join(lines).rstrip() + "\n"


def discover_projects(home: Path, max_depth: int) -> list[Path]:
    projects: list[Path] = []
    base_depth = len(home.parts)
    skip_names = {
        ".Trash",
        ".cache",
        ".npm",
        ".pnpm-store",
        ".cargo",
        ".rustup",
        "node_modules",
        "Library",
        "Movies",
        "Music",
        "Pictures",
    }
    for current, dirs, _files in os.walk(home):
        path = Path(current)
        if path.name in skip_names:
            dirs[:] = []
            continue
        if len(path.parts) - base_depth > max_depth:
            dirs[:] = []
            continue
        if ".ai-collab" in dirs:
            projects.append(path)
            dirs.remove(".ai-collab")
    return sorted(set(projects))


def latest_source_mtime(collab_dir: Path) -> float:
    mtimes: list[float] = []
    patterns = ["*.md", "discussions/*.md", "thread-*.md"]
    for pattern in patterns:
        for path in collab_dir.glob(pattern):
            if path.name == "CONTEXT.md" or path.name.startswith("PROTOCOL.md.bak-"):
                continue
            try:
                mtimes.append(path.stat().st_mtime)
            except OSError:
                continue
    return max(mtimes) if mtimes else 0.0


def context_needs_refresh(collab_dir: Path, *, max_age_seconds: int) -> bool:
    context = collab_dir / "CONTEXT.md"
    if not context.exists():
        return True
    try:
        context_mtime = context.stat().st_mtime
    except OSError:
        return True
    if latest_source_mtime(collab_dir) > context_mtime:
        return True
    age = utc_now().timestamp() - context_mtime
    return age > max_age_seconds


def regenerate_context(root: Path, summary_script: Path, *, dry_run: bool) -> dict[str, Any]:
    if not summary_script.exists():
        return {"status": "skipped", "reason": f"missing summary script: {summary_script}"}
    if dry_run:
        return {"status": "dry-run", "command": [sys.executable, str(summary_script)], "cwd": str(root)}
    completed = subprocess.run(
        [sys.executable, str(summary_script)],
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return {
        "status": "updated" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-1000:],
    }


def unfinished_task_ids(collab_dir: Path) -> list[str]:
    task_ids: list[str] = []
    for inbox in collab_dir.glob("inbox-*.md"):
        try:
            meta, _body = parse_frontmatter(inbox.read_text(encoding="utf-8"))
        except OSError:
            continue
        status = (meta.get("status") or "").strip()
        task_id = (meta.get("task_id") or "").strip()
        if task_id and status in RECOVERABLE_STATUSES and status not in TERMINAL_STATUSES:
            task_ids.append(task_id)
    return sorted(set(task_ids))


def should_requeue_failed_wakeup(meta: dict[str, str]) -> bool:
    status = (meta.get("status") or "").strip().lower()
    if status != "failed":
        return False
    if not (meta.get("task_id") or "").strip():
        return False
    if (meta.get("claimed_by") or "").strip() or (meta.get("claimed_at") or "").strip():
        return False
    if (meta.get("recovered_by") or "").strip():
        return False
    try:
        attempts = int((meta.get("attempts") or "0").strip())
    except ValueError:
        attempts = 0
    return attempts > 0


def requeue_failed_wakeups(collab_dir: Path, *, dry_run: bool, now: datetime) -> dict[str, Any]:
    requeued: list[dict[str, str]] = []
    for inbox in sorted(collab_dir.glob("inbox-*.md")):
        try:
            meta, body = parse_frontmatter(inbox.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not should_requeue_failed_wakeup(meta):
            continue
        task_id = meta["task_id"]
        requeued.append({"task_id": task_id, "path": str(inbox)})
        if dry_run:
            continue
        timestamp = isoformat_z(now)
        meta["status"] = "unread"
        meta["attempts"] = "0"
        meta["last_attempt"] = ""
        meta["done_at"] = ""
        meta["updated"] = timestamp
        meta["recovered_by"] = "ai-collab-recover"
        meta["recovered_at"] = timestamp
        meta["recovery_reason"] = "retry-failed-unclaimed-wakeup"
        atomic_write_text(inbox, render_frontmatter(meta, body))
    return {"status": "updated" if requeued else "skipped", "requeued": requeued}


def prune_wakeup_dedupe(task_ids: list[str], state_file: Path, *, dry_run: bool) -> dict[str, Any]:
    if not task_ids:
        return {"status": "skipped", "reason": "no unfinished inbox tasks"}
    state = read_json(state_file, {})
    if not isinstance(state, dict):
        state = {}
    prefixes = tuple(f"{task_id}:" for task_id in task_ids)
    removed = sorted(key for key in state if key.startswith(prefixes))
    if not dry_run and removed:
        for key in removed:
            state.pop(key, None)
        atomic_write_json(state_file, state)
    return {"status": "updated" if removed else "unchanged", "removed": removed, "task_ids": task_ids}


def recover_project(
    root: Path,
    *,
    summary_script: Path,
    state_file: Path,
    max_context_age_seconds: int,
    dry_run: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    collab_dir = root / ".ai-collab"
    result: dict[str, Any] = {
        "root": str(root),
        "updated": isoformat_z(now),
        "context": {"status": "unchanged"},
        "requeued_failed_wakeups": {"status": "skipped"},
        "wakeup_state": {"status": "skipped"},
    }
    if not collab_dir.is_dir():
        result["status"] = "skipped"
        result["reason"] = "no .ai-collab directory"
        return result

    if context_needs_refresh(collab_dir, max_age_seconds=max_context_age_seconds):
        result["context"] = regenerate_context(root, summary_script, dry_run=dry_run)

    result["requeued_failed_wakeups"] = requeue_failed_wakeups(collab_dir, dry_run=dry_run, now=now)
    task_ids = unfinished_task_ids(collab_dir)
    result["wakeup_state"] = prune_wakeup_dedupe(task_ids, state_file, dry_run=dry_run)
    result["status"] = "ok"

    if not dry_run:
        recovery_path = collab_dir / "live" / "recovery.json"
        atomic_write_json(recovery_path, result)
        result["report"] = str(recovery_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover AI Collab context and wakeup state after restart.")
    parser.add_argument("--project", action="append", default=[], help="Project root to recover. Can be repeated.")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory to scan for .ai-collab projects.")
    parser.add_argument("--max-depth", type=int, default=int(os.environ.get("AI_COLLAB_RECOVERY_MAX_DEPTH", "6")))
    parser.add_argument(
        "--max-context-age-seconds",
        type=int,
        default=int(os.environ.get("AI_COLLAB_RECOVERY_CONTEXT_MAX_AGE", "3600")),
    )
    parser.add_argument("--summary-script", default=str(SUMMARY_SCRIPT))
    parser.add_argument("--state-file", default=str(WAKEUP_STATE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    roots = [Path(p).expanduser().resolve() for p in args.project]
    if not roots:
        roots = discover_projects(Path(args.home).expanduser().resolve(), args.max_depth)

    results = [
        recover_project(
            root,
            summary_script=Path(args.summary_script).expanduser(),
            state_file=Path(args.state_file).expanduser(),
            max_context_age_seconds=args.max_context_age_seconds,
            dry_run=args.dry_run,
        )
        for root in roots
    ]
    output = {
        "schema": "ai-collab.recovery.v1",
        "updated": isoformat_z(utc_now()),
        "project_count": len(results),
        "projects": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if all(item.get("status") in {"ok", "skipped"} for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
