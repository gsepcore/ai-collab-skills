#!/usr/bin/env python3
"""
Natural agent-to-agent conversation helper for AI Collab.

This helper writes append-only Markdown conversations that the existing daemon
can wake from `@slug` mentions. Task conversations stay compatible with
`.ai-collab/thread-{task_id}.md`; broader design/review discussions live under
`.ai-collab/discussions/`.
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

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


SCHEMA_VERSION = "ai-collab.thread.v2"
VALID_MESSAGE_TYPES = {"message", "question", "answer", "proposal", "decision", "blocker", "review", "handoff"}
MENTION_RE = re.compile(r"(?<![\w.-])@([a-z][a-z0-9_-]{1,40})\b")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str, fallback: str = "discussion") -> str:
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


def root_from_arg(value: str | None) -> Path:
    if value:
        path = Path(value).expanduser().resolve()
    else:
        path = project_root()
    if path.name == ".ai-collab":
        return path.parent
    return path


def collab_dir(root: Path) -> Path:
    return root / ".ai-collab"


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().strip("[]") for item in value.split(",") if item.strip().strip("[]")]


def render_csv(items: list[str]) -> str:
    return ", ".join(sorted(dict.fromkeys(item for item in items if item)))


def normalize_slug(value: str) -> str:
    return slugify(value.strip().lstrip("@"), "agent")


def normalize_slugs(value: str | None) -> list[str]:
    return [normalize_slug(item) for item in parse_csv(value)]


def find_mentions(message: str) -> list[str]:
    return sorted(dict.fromkeys(match.group(1).lower() for match in MENTION_RE.finditer(message)))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def with_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    return lock_file


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


def latest_message(body: str) -> dict[str, str] | None:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s+(?:--|—)\s+([a-zA-Z0-9_-]+)\s*$", body))
    if not matches:
        return None
    start = matches[-1].end()
    end_match = re.search(r"(?m)^---\s*$", body[start:])
    end = start + end_match.start() if end_match else len(body)
    content = body[start:end].strip()
    return {
        "timestamp": matches[-1].group(1).strip(),
        "author": matches[-1].group(2).strip().lower(),
        "content": content,
    }


def conversation_paths(collab: Path) -> list[Path]:
    paths = list(collab.glob("thread-*.md")) + list((collab / "discussions").glob("*.md"))
    paths = [path for path in paths if path.is_file()]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def resolve_thread(collab: Path, value: str) -> Path:
    raw = value.strip()
    path = Path(raw).expanduser()
    if path.exists():
        return path.resolve()
    if "/" in raw or raw.endswith(".md"):
        candidate = (collab.parent / raw).resolve()
        if candidate.exists():
            return candidate
        candidate = (collab / raw).resolve()
        if candidate.exists():
            return candidate
    candidates = [
        collab / raw,
        collab / f"thread-{raw}.md",
        collab / "discussions" / raw,
        collab / "discussions" / f"{raw}.md",
        collab / "discussions" / f"discussion-{raw}.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    matches = [path for path in conversation_paths(collab) if path.stem == raw or path.stem.endswith(f"-{raw}") or path.name == raw]
    if len(matches) == 1:
        return matches[0].resolve()
    if matches:
        raise SystemExit("Ambiguous conversation id: " + ", ".join(str(path) for path in matches))
    raise SystemExit(f"Conversation not found: {value}")


def task_thread_path(collab: Path, task_id: str) -> Path:
    return collab / f"thread-{slugify(task_id, 'task')}.md"


def discussion_path(collab: Path, topic: str, now: datetime) -> Path:
    slug = slugify(topic, "discussion")
    return collab / "discussions" / f"discussion-{now.strftime('%Y%m%d-%H%M%S')}-{slug}.md"


def prefix_mentions(message: str, recipients: list[str]) -> str:
    clean = message.strip()
    missing = [slug for slug in recipients if f"@{slug}" not in clean]
    if not missing:
        return clean
    return f"{' '.join('@' + slug for slug in missing)} {clean}"


def metadata_lines(message_type: str, recipients: list[str], tags: list[str]) -> list[str]:
    lines = [f"type: {message_type}"]
    if recipients:
        lines.append(f"to: {render_csv(recipients)}")
    if tags:
        lines.append(f"tags: {render_csv(tags)}")
    return lines


def format_message(message: str, message_type: str, recipients: list[str], tags: list[str]) -> str:
    if message_type not in VALID_MESSAGE_TYPES:
        raise SystemExit(f"Invalid message type: {message_type}")
    lines = metadata_lines(message_type, recipients, tags)
    return "\n".join(lines) + "\n\n" + message.strip()


def append_message(
    path: Path,
    *,
    root: Path,
    author: str,
    message: str,
    message_type: str = "message",
    recipients: list[str] | None = None,
    tags: list[str] | None = None,
    kind: str = "discussion",
    topic: str = "",
    task_id: str = "",
    run_id: str = "",
    close: bool = False,
    now: datetime | None = None,
) -> Path:
    now = now or utc_now()
    recipients = recipients or []
    tags = tags or []
    timestamp = isoformat_z(now)
    author = normalize_slug(author)
    lock_file = with_lock(path)
    try:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""

        meta, body = parse_frontmatter(text)
        if meta.get("status") == "closed" and not close:
            raise SystemExit(f"Conversation is closed: {path}")

        if not meta:
            thread_id = task_id or path.stem
            meta = {
                "schema": SCHEMA_VERSION,
                "thread": thread_id,
                "kind": kind,
                "topic": topic or thread_id,
                "project": root.name,
                "created": timestamp,
                "updated": timestamp,
                "participants": "",
                "status": "open",
            }
            if task_id:
                meta["task_id"] = task_id
                meta["inbox"] = f"inbox-{recipients[0]}.md" if recipients else ""
            if run_id:
                meta["run_id"] = run_id
        else:
            meta.setdefault("schema", SCHEMA_VERSION)
            meta.setdefault("thread", task_id or path.stem)
            meta.setdefault("kind", kind)
            meta.setdefault("topic", topic or meta.get("thread", path.stem))
            meta.setdefault("project", root.name)
            meta.setdefault("created", timestamp)
            meta.setdefault("status", "open")
            if task_id:
                meta.setdefault("task_id", task_id)
            if run_id:
                meta.setdefault("run_id", run_id)
            meta["updated"] = timestamp

        mentioned = find_mentions(message)
        participants = parse_csv(meta.get("participants"))
        for slug in [author, *recipients, *mentioned]:
            if slug and slug not in participants:
                participants.append(slug)
        meta["participants"] = render_csv(participants)
        if close:
            meta["status"] = "closed"

        formatted = format_message(message, message_type, recipients, tags)
        section = f"## {timestamp} -- {author}\n\n{formatted}\n\n---\n"
        clean_body = body.rstrip()
        new_body = f"{clean_body}\n\n{section}" if clean_body else section
        atomic_write(path, render_frontmatter(meta, new_body))
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    return path


def start_conversation(args: argparse.Namespace) -> Path:
    root = root_from_arg(args.root)
    collab = collab_dir(root)
    now = utc_now()
    recipients = normalize_slugs(args.to)
    tags = normalize_slugs(args.tags)
    kind = args.kind
    task_id = args.task_id.strip()
    if kind == "task" or task_id:
        if not task_id:
            raise SystemExit("--task-id is required for task conversations")
        path = task_thread_path(collab, task_id)
        kind = "task"
    else:
        path = discussion_path(collab, args.topic, now)
    message = prefix_mentions(args.message, recipients)
    return append_message(
        path,
        root=root,
        author=args.author,
        message=message,
        message_type=args.type,
        recipients=recipients,
        tags=tags,
        kind=kind,
        topic=args.topic,
        task_id=task_id,
        run_id=args.run_id,
        now=now,
    )


def reply_conversation(args: argparse.Namespace, *, message_type: str | None = None, close: bool = False) -> Path:
    root = root_from_arg(args.root)
    collab = collab_dir(root)
    path = resolve_thread(collab, args.thread)
    recipients = normalize_slugs(getattr(args, "to", ""))
    tags = normalize_slugs(getattr(args, "tags", ""))
    message = prefix_mentions(args.message, recipients)
    meta, _body = parse_frontmatter(path.read_text(encoding="utf-8") if path.exists() else "")
    return append_message(
        path,
        root=root,
        author=args.author,
        message=message,
        message_type=message_type or getattr(args, "type", "message"),
        recipients=recipients,
        tags=tags,
        kind=meta.get("kind", "discussion"),
        topic=meta.get("topic", ""),
        task_id=meta.get("task_id", ""),
        run_id=meta.get("run_id", ""),
        close=close,
    )


def summarize_thread(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(path), "error": "unreadable"}
    meta, body = parse_frontmatter(text)
    latest = latest_message(body) or {}
    return {
        "path": str(path),
        "thread": meta.get("thread", path.stem),
        "kind": meta.get("kind", "task" if path.name.startswith("thread-") else "discussion"),
        "topic": meta.get("topic", meta.get("thread", path.stem)),
        "status": meta.get("status", "open"),
        "participants": parse_csv(meta.get("participants")),
        "updated": meta.get("updated", ""),
        "latest_author": latest.get("author", ""),
        "latest_excerpt": " ".join(str(latest.get("content", "")).split())[:500],
    }


def cmd_start(args: argparse.Namespace) -> int:
    path = start_conversation(args)
    print(f"[AI-COLLAB] Conversation started: {path}")
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    path = reply_conversation(args)
    print(f"[AI-COLLAB] Conversation updated: {path}")
    return 0


def cmd_typed_reply(args: argparse.Namespace) -> int:
    path = reply_conversation(args, message_type=args.command)
    print(f"[AI-COLLAB] Conversation updated: {path}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    path = reply_conversation(args, message_type="handoff", close=True)
    print(f"[AI-COLLAB] Conversation closed: {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = root_from_arg(args.root)
    collab = collab_dir(root)
    rows = []
    for path in conversation_paths(collab):
        item = summarize_thread(path)
        if args.open and item.get("status") == "closed":
            continue
        rows.append(item)
        if len(rows) >= args.limit:
            break
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=False))
        return 0
    if not rows:
        print("[AI-COLLAB] No conversations found.")
        return 0
    for item in rows:
        print(
            f"{item['status']} {item['kind']} {item['thread']} "
            f"participants={','.join(item['participants']) or '-'} path={item['path']}"
        )
        if item.get("latest_excerpt"):
            print(f"  latest @{item.get('latest_author')}: {item['latest_excerpt']}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    args.json = True
    return cmd_list(args)


def add_common_reply_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--thread", required=True, help="Thread id, task id, filename, or path.")
    parser.add_argument("--author", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--to", default="", help="Comma-separated recipients. Missing @mentions are prepended.")
    parser.add_argument("--tags", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write natural AI Collab agent conversations.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new discussion or task thread.")
    start.add_argument("--topic", required=True)
    start.add_argument("--author", required=True)
    start.add_argument("--message", required=True)
    start.add_argument("--to", default="", help="Comma-separated recipients. Missing @mentions are prepended.")
    start.add_argument("--tags", default="")
    start.add_argument("--kind", choices=("discussion", "task"), default="discussion")
    start.add_argument("--task-id", default="")
    start.add_argument("--run-id", default="")
    start.add_argument("--type", choices=sorted(VALID_MESSAGE_TYPES), default="message")
    start.set_defaults(func=cmd_start)

    reply = sub.add_parser("reply", help="Append a normal message.")
    add_common_reply_args(reply)
    reply.add_argument("--type", choices=sorted(VALID_MESSAGE_TYPES), default="message")
    reply.set_defaults(func=cmd_reply)

    for name in ("question", "answer", "proposal", "decision", "blocker", "review", "handoff"):
        item = sub.add_parser(name, help=f"Append a {name} message.")
        add_common_reply_args(item)
        item.set_defaults(func=cmd_typed_reply)

    close = sub.add_parser("close", help="Append a closing handoff and mark the conversation closed.")
    add_common_reply_args(close)
    close.set_defaults(func=cmd_close)

    list_cmd = sub.add_parser("list", help="List recent conversations.")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--open", action="store_true", help="Only show open conversations.")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    summary = sub.add_parser("summary", help="Print recent conversations as JSON.")
    summary.add_argument("--limit", type=int, default=20)
    summary.add_argument("--open", action="store_true")
    summary.set_defaults(func=cmd_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
