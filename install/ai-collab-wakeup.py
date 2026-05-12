#!/usr/bin/env python3
"""
Durable inbox wakeup detection for ai-collab.

Phase B owns detection only: it turns unread inbox files into durable wake
events with retry/backoff. Real wakeup adapters are Phase C.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (5, 25, 125)
DEFAULT_EVENTS_FILE = Path.home() / ".ai-collab-wakeup-events.json"
DEFAULT_STATE_FILE = Path.home() / ".ai-collab-wakeup-state.json"
DEFAULT_LOG_FILE = Path("/tmp/ai-collab-wakeup.log")
MAX_EVENTS = 200


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
        key = key.strip()
        if key:
            meta[key] = value.strip()
    return meta, text[body_start:]


def render_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def coerce_int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except ValueError:
        return default


def max_attempts_from_env() -> int:
    return max(1, coerce_int(os.environ.get("AI_COLLAB_WAKEUP_MAX_ATTEMPTS"), DEFAULT_MAX_ATTEMPTS))


def backoff_for_attempts(attempts: int) -> int:
    if attempts <= 0:
        return 0
    return DEFAULT_BACKOFF_SECONDS[min(attempts - 1, len(DEFAULT_BACKOFF_SECONDS) - 1)]


def log(message: str, log_file: Path = DEFAULT_LOG_FILE) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"[AI-COLLAB-WAKEUP] {isoformat_z(utc_now())} {message}\n")


def append_event(events_file: Path, event: dict[str, Any]) -> None:
    events = load_json(events_file, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    if len(events) > MAX_EVENTS:
        events = events[-MAX_EVENTS:]
    write_json(events_file, events)


def update_inbox(path: Path, meta: dict[str, str], body: str) -> None:
    meta["updated"] = isoformat_z(utc_now())
    atomic_write(path, render_frontmatter(meta, body))


def process_inbox(
    inbox_path: Path,
    project: str,
    *,
    now: datetime | None = None,
    events_file: Path = DEFAULT_EVENTS_FILE,
    state_file: Path = DEFAULT_STATE_FILE,
    log_file: Path = DEFAULT_LOG_FILE,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    max_attempts = max_attempts or max_attempts_from_env()

    try:
        text = inbox_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"action": "missing", "path": str(inbox_path)}

    meta, body = parse_frontmatter(text)
    if meta.get("status") != "unread":
        return {"action": "ignored", "reason": "status", "status": meta.get("status", "")}

    task_id = meta.get("task_id") or f"{project}:{inbox_path.name}"
    target_slug = (meta.get("to") or inbox_path.stem.replace("inbox-", "")).strip()
    attempts = coerce_int(meta.get("attempts"), 0)
    last_attempt = parse_iso(meta.get("last_attempt"))

    if attempts >= max_attempts:
        meta["status"] = "failed"
        meta["done_at"] = isoformat_z(now)
        update_inbox(inbox_path, meta, body)
        log(f"FAILED max_attempts task_id={task_id} inbox={inbox_path}", log_file)
        return {"action": "failed", "task_id": task_id, "attempts": attempts}

    wait_seconds = backoff_for_attempts(attempts)
    if last_attempt and wait_seconds:
        elapsed = (now - last_attempt).total_seconds()
        if elapsed < wait_seconds:
            return {
                "action": "backoff",
                "task_id": task_id,
                "attempts": attempts,
                "wait_seconds": wait_seconds,
                "elapsed_seconds": elapsed,
            }

    mtime = int(inbox_path.stat().st_mtime)
    state = load_json(state_file, {})
    if not isinstance(state, dict):
        state = {}

    state_key = f"{task_id}:{mtime}:{attempts}"
    if state.get(state_key):
        return {"action": "deduped", "task_id": task_id, "attempts": attempts}

    next_attempts = attempts + 1
    timestamp = isoformat_z(now)
    event = {
        "task_id": task_id,
        "project": project,
        "target_slug": target_slug,
        "inbox_path": str(inbox_path),
        "attempt": next_attempts,
        "timestamp": timestamp,
        "synthetic_prompt": (
            f"You have an unread task in {inbox_path}. "
            "Read it, execute it, mark it status: done, and update your log."
        ),
    }
    append_event(events_file, event)

    meta["attempts"] = str(next_attempts)
    meta["last_attempt"] = timestamp
    if next_attempts >= max_attempts:
        meta["status"] = "failed"
        meta["done_at"] = timestamp
        action = "failed"
    else:
        action = "event"
    update_inbox(inbox_path, meta, body)

    state[state_key] = timestamp
    if len(state) > MAX_EVENTS:
        state = dict(list(state.items())[-MAX_EVENTS:])
    write_json(state_file, state)

    log(f"WAKE action={action} task_id={task_id} target={target_slug} attempt={next_attempts} inbox={inbox_path}", log_file)
    return {"action": action, "task_id": task_id, "attempts": next_attempts, "event": event}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: ai-collab-wakeup.py <project> <inbox.md>", file=sys.stderr)
        return 2

    project = argv[1]
    inbox_path = Path(argv[2])
    result = process_inbox(inbox_path, project)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
