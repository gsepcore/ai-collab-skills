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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


SCHEMA_VERSION = "ai-collab.thread.v2"
VALID_MESSAGE_TYPES = {"message", "question", "answer", "proposal", "decision", "blocker", "review", "handoff"}
VALID_VISUAL_MODES = {"observe", "strict", "off"}
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


def visual_participants(root: Path, author: str, recipients: list[str]) -> list[str]:
    registered = registered_agent_slugs(root)
    candidates = [normalize_slug(author), *recipients]
    if registered:
        candidates = [agent for agent in candidates if agent in registered]
    return list(dict.fromkeys(candidates))


def registered_agent_slugs(root: Path) -> set[str]:
    payload = load_json_file(collab_dir(root) / "agents.json")
    rows = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return set()
    return {
        normalize_slug(str(row.get("agent", "")))
        for row in rows
        if isinstance(row, dict) and str(row.get("agent", "")).strip()
    }


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def capability_for(root: Path, agent: str) -> dict[str, Any]:
    payload = load_json_file(collab_dir(root) / "capabilities.json")
    rows = payload.get("agents") if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and normalize_slug(str(row.get("agent", ""))) == normalize_slug(agent):
            return row
    return {}


def agent_is_active(root: Path, agent: str, threshold_seconds: int) -> bool:
    live_dir = collab_dir(root) / "live"
    for path in (live_dir / f"{agent}.agent.json", live_dir / f"{agent}.json"):
        state = load_json_file(path)
        if not isinstance(state, dict):
            continue
        phase = str(state.get("phase") or state.get("status") or "").lower()
        updated = parse_iso(str(state.get("updated") or ""))
        if updated is None:
            continue
        age = (utc_now() - updated).total_seconds()
        if age <= threshold_seconds and phase in {"command", "editing", "running", "working", "responding", "claimed"}:
            return True
    return False


def internal_grace_seconds(root: Path, agent: str, requested: int) -> int:
    capability = capability_for(root, agent)
    delivery = capability.get("delivery") if isinstance(capability, dict) else {}
    # Codex native chat is the one explicit exception to internal-first delivery.
    # Every other agent gets its durable inbox/thread grace period even if it also
    # exposes a visible adapter. A visible route is fallback, not the primary bus.
    if agent == "codex":
        return 0
    policy = capability.get("wake_policy") if isinstance(capability, dict) else {}
    try:
        configured = int((policy or {}).get("internal_grace_seconds", 15))
    except (TypeError, ValueError):
        configured = requested
    grace = max(0, requested if requested >= 0 else configured)
    return grace


def visual_mode(value: str | None = None) -> str:
    legacy = os.environ.get("AI_COLLAB_VISUAL_PROOF", "1").strip().lower()
    if legacy in {"0", "false", "no", "off"}:
        return "off"
    mode = (value or os.environ.get("AI_COLLAB_VISUAL_MODE", "observe")).strip().lower()
    if mode not in VALID_VISUAL_MODES:
        raise SystemExit(f"Invalid visual mode: {mode}. Expected observe, strict, or off.")
    return mode


def emit_escalation_notice(root: Path, targets: list[str], source: Path, grace_seconds: int) -> None:
    timestamp = isoformat_z(utc_now())
    target_text = ", ".join(targets)
    message = (
        f"No internal response from {target_text} after {grace_seconds}s; "
        "AI Collab is proceeding to the exact visible chat now."
    )
    notice = {
        "ai": "AI Collab delivery supervisor",
        "project": root.name,
        "file": source.name,
        "message": message,
        "timestamp": timestamp,
        "type": "visible-escalation",
        "targets": targets,
    }
    notification_path = Path(os.environ.get("AI_COLLAB_NOTIFICATIONS_FILE", str(Path.home() / ".ai-collab-notifications.json"))).expanduser()
    lock_file = with_lock(notification_path)
    try:
        rows = load_json_file(notification_path)
        if not isinstance(rows, list):
            rows = []
        rows.append(notice)
        atomic_write(notification_path, json.dumps(rows[-50:], indent=2, sort_keys=False) + "\n")
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    events = collab_dir(root) / "live" / "delivery-escalations.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(notice, sort_keys=True) + "\n")


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


def discussion_path(collab: Path, topic: str, now: datetime, discussion_id: str = "") -> Path:
    slug = slugify(topic, "discussion")
    if discussion_id:
        return collab / "discussions" / f"discussion-{slugify(discussion_id, slug)}.md"
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

        registered = registered_agent_slugs(root)
        mentioned = find_mentions(message)
        if registered:
            mentioned = [slug for slug in mentioned if slug in registered]
        participants = parse_csv(meta.get("participants"))
        if registered:
            explicitly_addressed = {author, *recipients}
            participants = [
                slug for slug in participants
                if slug in registered or slug in explicitly_addressed
            ]
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
        path = discussion_path(collab, args.topic, now, args.discussion_id)
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


def wakeup_script_path() -> Path | None:
    configured = os.environ.get("AI_COLLAB_WAKEUP_SCRIPT", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().with_name("ai-collab-wakeup.py"),
        Path.home() / ".claude" / "ai-collab-wakeup.py",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    return None


def observer_script_path() -> Path | None:
    configured = os.environ.get("AI_COLLAB_OBSERVER_SCRIPT", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().with_name("ai-collab-observer.py"),
        Path.home() / ".claude" / "ai-collab-observer.py",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    return None


def visual_proof(root: Path, agents: list[str], stage: str) -> dict[str, Any]:
    if os.environ.get("AI_COLLAB_VISUAL_PROOF", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "skipped": True, "reason": "AI_COLLAB_VISUAL_PROOF=0"}
    script = observer_script_path()
    if not script:
        return {"ok": False, "reason": "ai-collab-observer.py is not installed"}
    required = render_csv([normalize_slug(agent) for agent in agents])
    try:
        completed = subprocess.run(
            [sys.executable, str(script), str(root), "--visual-proof", "--agents", required, "--tag", stage],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=max(45, int(os.environ.get("AI_COLLAB_VISUAL_TIMEOUT", "75"))),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"visual proof command failed: {exc}"}
    try:
        payload = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError:
        return {
            "ok": False,
            "reason": f"invalid visual proof response: {(completed.stdout or completed.stderr).strip()[:500]}",
        }
    return {
        "ok": completed.returncode == 0 and payload.get("status") == "verified",
        "result": payload,
        "reason": "" if completed.returncode == 0 else f"visual proof failed for: {', '.join(payload.get('missing_or_unverified', []))}",
    }


def visual_context(message: str, proof: dict[str, Any], participants: list[str]) -> str:
    result = proof.get("result") if isinstance(proof.get("result"), dict) else {}
    screenshot = result.get("screenshot") if isinstance(result.get("screenshot"), dict) else {}
    roster_path = result.get("visual_roster", "")
    screenshot_path = screenshot.get("path", "")
    project_path = result.get("project_path", "<project-root>")
    peers = render_csv(participants)
    return (
        message.rstrip()
        + "\n\nVisual evidence is mandatory for this turn. Before answering, inspect the actual image "
        + f"`{screenshot_path}` with your image/vision tool and read `{roster_path}`. "
        + f"Confirm your own surface and the visible peer surfaces ({peers}); cross-check their PID/TTY/port ownership and recent logs. "
        + "Use the roster's surface-specific standard: terminal/TUI agents need their exact PID/TTY and owned port, while an IDE-native chat requires the captured host PID to be an ancestor of the exact project bridge plus a position-bound top-band label and actual pane pixels; it must not be assigned a fake child PID or port. "
        + "If your model cannot accept image input natively, you must still inspect the actual pixels by running "
        + f"`python3 ~/.claude/ai-collab-see.py --root \"{project_path}\" --image \"{screenshot_path}\" --agents \"{peers}\"`; "
        + "cite its `direct-pixel-ocr` method and SHA-256 instead of treating a prewritten sidecar as sight. "
        + "In your shared-thread reply include `visual_evidence:` with the screenshot path and `visible_peers:` with the slugs you actually saw. "
        + "If you cannot inspect the image or any identity disagrees, append a blocker instead of claiming success."
    )


def visual_observation_context(message: str, proof: dict[str, Any], participants: list[str]) -> str:
    result = proof.get("result") if isinstance(proof.get("result"), dict) else {}
    screenshot = result.get("screenshot") if isinstance(result.get("screenshot"), dict) else {}
    roster_path = result.get("visual_roster", "")
    screenshot_path = screenshot.get("path", "")
    peers = render_csv(participants)
    evidence = []
    if screenshot_path:
        evidence.append(f"screenshot `{screenshot_path}`")
    if roster_path:
        evidence.append(f"roster `{roster_path}`")
    suffix = ", ".join(evidence) or "the live visual observer"
    return (
        message.rstrip()
        + f"\n\nVisual observation is available from {suffix} for peers ({peers}). "
        + "Use it as situational evidence when useful. A visual ambiguity does not invalidate "
        + "this durable thread or an agent-authored reply; strict visual audit was not requested."
    )


def dispatch_visible(path: Path, root: Path, targets: list[str] | None = None) -> dict[str, Any]:
    script = wakeup_script_path()
    if not script:
        return {"ok": False, "reason": "ai-collab-wakeup.py is not installed"}
    env = os.environ.copy()
    env["AI_COLLAB_WAKEUP_ADAPTER"] = "visible"
    env["AI_COLLAB_FORCE_VISIBLE"] = "1"
    env["AI_COLLAB_ESCALATION_NOTIFIED"] = "1"
    if targets:
        env["AI_COLLAB_WAKE_TARGETS"] = ",".join(targets)
    try:
        completed = subprocess.run(
            [sys.executable, str(script), root.name, str(path)],
            cwd=str(root),
            env=env,
            text=True,
            capture_output=True,
            timeout=max(15, int(os.environ.get("AI_COLLAB_CONVERSE_DISPATCH_TIMEOUT", "45"))),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"visible wake command failed: {exc}"}
    output = (completed.stdout or "").strip().splitlines()
    try:
        result = json.loads(output[-1]) if output else {}
    except json.JSONDecodeError:
        return {"ok": False, "reason": f"invalid wake response: {(completed.stdout or completed.stderr).strip()[:500]}"}
    rows = result.get("results") if isinstance(result, dict) else None
    if result.get("action") == "ignored" and result.get("reason") == "no-mentions":
        return {"ok": True, "result": result, "reason": "conversation has no mentioned recipients"}
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "result": result, "reason": "no visible recipients were dispatched"}
    failed = [
        row for row in rows
        if not isinstance(row, dict) or row.get("action") not in {"dispatched", "deduped"}
    ]
    return {
        "ok": not failed,
        "result": result,
        "failed": failed,
        "reason": "" if not failed else "one or more visible agent interfaces rejected the message",
    }


def prepare_visible_surfaces(root: Path, targets: list[str]) -> dict[str, Any]:
    script = wakeup_script_path()
    if not script:
        return {"ok": False, "reason": "ai-collab-wakeup.py is not installed"}
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--prepare-visible", str(root), ",".join(targets)],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=max(15, int(os.environ.get("AI_COLLAB_CONVERSE_DISPATCH_TIMEOUT", "45"))),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"visible surface preparation failed: {exc}"}
    output = (completed.stdout or "").strip().splitlines()
    try:
        result = json.loads(output[-1]) if output else {}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "reason": f"invalid visible surface preparation response: {(completed.stdout or completed.stderr).strip()[:500]}",
        }
    return {
        "ok": completed.returncode == 0 and result.get("ok") is True,
        "result": result,
        "reason": "" if completed.returncode == 0 else "one or more visible agent surfaces could not be focused",
    }


def message_authors_after(path: Path, timestamp: str) -> set[str]:
    try:
        _meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return set()
    authors: set[str] = set()
    for match in re.finditer(r"(?m)^##\s+(.+?)\s+(?:--|—)\s+([a-zA-Z0-9_-]+)\s*$", body):
        if match.group(1).strip() > timestamp:
            authors.add(normalize_slug(match.group(2)))
    return authors


def messages_after(path: Path, timestamp: str) -> dict[str, str]:
    try:
        _meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s+(?:--|—)\s+([a-zA-Z0-9_-]+)\s*$", body))
    messages: dict[str, str] = {}
    for index, match in enumerate(matches):
        if match.group(1).strip() <= timestamp:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[match.end():end].strip()
        messages[normalize_slug(match.group(2))] = content
    return messages


def missing_visual_attestations(path: Path, recipients: list[str], participants: list[str], after: str) -> list[str]:
    authored = messages_after(path, after)
    missing: list[str] = []
    for recipient in recipients:
        content = authored.get(recipient, "").lower()
        peers = [agent for agent in participants if agent != recipient]
        if "visual_evidence:" not in content or "visible_peers:" not in content:
            missing.append(recipient)
            continue
        if any(peer.lower() not in content for peer in peers):
            missing.append(recipient)
    return missing


def wait_for_replies(path: Path, recipients: list[str], after: str, timeout: int) -> list[str]:
    pending = set(recipients)
    deadline = time.monotonic() + max(0, timeout)
    while pending and time.monotonic() <= deadline:
        pending -= message_authors_after(path, after)
        if pending:
            time.sleep(1)
    return sorted(pending)


def dispatch_and_optionally_wait(
    path: Path,
    *,
    root: Path,
    author: str,
    recipients: list[str],
    kickoff_at: str,
    queue_only: bool,
    internal_wait_seconds: int,
    wait_seconds: int,
    visual_agents: list[str],
    requested_visual_mode: str = "observe",
) -> int:
    if queue_only:
        print("[AI-COLLAB] Conversation queued without visible dispatch (--queue-only).")
        return 0
    mode = visual_mode(requested_visual_mode)
    pending_internal = set(recipients)
    escalated: list[str] = []
    dispatch_failed = False
    started = time.monotonic()
    grace_by_agent = {
        recipient: internal_grace_seconds(root, recipient, internal_wait_seconds)
        for recipient in recipients
    }
    while pending_internal:
        answered = message_authors_after(path, kickoff_at)
        newly_answered = sorted(pending_internal & answered)
        if newly_answered:
            print("[AI-COLLAB] Internal response received from: " + ", ".join(newly_answered))
            pending_internal -= set(newly_answered)
        elapsed = time.monotonic() - started
        due = sorted(agent for agent in pending_internal if elapsed >= grace_by_agent[agent])
        if due:
            grace = max(grace_by_agent[agent] for agent in due)
            print(
                "[AI-COLLAB] NOTICE: no internal response from " + ", ".join(due)
                + f" after {grace}s; proceeding to their visible chats with visual observation."
            )
            emit_escalation_notice(root, due, path, grace)
            prepared = prepare_visible_surfaces(root, due)
            print("[AI-COLLAB] Visible surface preparation: " + json.dumps(prepared, sort_keys=True))
            if not prepared.get("ok"):
                if mode == "strict":
                    print(
                        "[AI-COLLAB] ERROR: strict visual mode refused fallback because the exact "
                        "visible surface could not be prepared.",
                        file=sys.stderr,
                    )
                    return 4
                print(
                    "[AI-COLLAB] WARNING: visible preparation was inconclusive; the durable internal "
                    "message remains valid and the adapter will resolve the current session directly.",
                    file=sys.stderr,
                )
            preparation_result = prepared.get("result") if isinstance(prepared.get("result"), dict) else {}
            preparation_rows = preparation_result.get("results") if isinstance(preparation_result, dict) else []
            if not isinstance(preparation_rows, list):
                preparation_rows = []
            legacy_focus = any(
                isinstance(row, dict) and row.get("status") == "legacy-focus-on-submit"
                for row in preparation_rows
            )
            proof: dict[str, Any] = {"ok": True, "skipped": True, "reason": "visual mode off"}
            if legacy_focus:
                print(
                    "[AI-COLLAB] NOTICE: the installed bridge is legacy; submitting to the exact "
                    "project terminal will focus it, then visual proof and an evidence follow-up are mandatory."
                )
                first_dispatch = dispatch_visible(path, root, due)
                print("[AI-COLLAB] Legacy focus-and-submit: " + json.dumps(first_dispatch, sort_keys=True))
                if not first_dispatch.get("ok"):
                    dispatch_failed = True
                    escalated.extend(due)
                    pending_internal -= set(due)
                    continue
                time.sleep(0.75)
                if mode != "off":
                    proof = visual_proof(root, visual_agents, "after-legacy-focus-submit")
                print("[AI-COLLAB] Immediate post-submit visual proof: " + json.dumps(proof, sort_keys=True))
                if not proof.get("ok") and mode == "strict":
                    print(
                        "[AI-COLLAB] ERROR: the prompt was submitted to the exact terminal, but its "
                        "visible surface could not be verified afterward. Do not claim a response.",
                        file=sys.stderr,
                    )
                    return 4
                if not proof.get("ok"):
                    print(
                        "[AI-COLLAB] WARNING: the eyes recorded an ambiguous post-submit roster; "
                        "delivery continues in observe mode.",
                        file=sys.stderr,
                    )
            else:
                time.sleep(0.75)
                if mode != "off":
                    proof = visual_proof(root, visual_agents, "before-visible-turn")
                print("[AI-COLLAB] Pre-turn visual proof: " + json.dumps(proof, sort_keys=True))
                if not proof.get("ok") and mode == "strict":
                    print(
                        "[AI-COLLAB] ERROR: the internal message remains queued, but visible escalation "
                        "was refused because visual preflight failed.",
                        file=sys.stderr,
                    )
                    return 4
                if not proof.get("ok"):
                    print(
                        "[AI-COLLAB] WARNING: the eyes recorded an ambiguous pre-turn roster; "
                        "the internal thread and current-session delivery remain authoritative.",
                        file=sys.stderr,
                    )
            context_builder = visual_context if mode == "strict" else visual_observation_context
            escalation_message = context_builder(
                "Visible fallback after the internal grace period expired. Read the full thread and reply there.",
                proof,
                visual_agents,
            )
            append_message(
                path,
                root=root,
                author=author,
                message=prefix_mentions(escalation_message, due),
                message_type="handoff",
                recipients=due,
                tags=["visible-escalation"],
            )
            dispatch = dispatch_visible(path, root, due)
            label = "Visible evidence follow-up" if legacy_focus else "Visible escalation"
            print(f"[AI-COLLAB] {label}: " + json.dumps(dispatch, sort_keys=True))
            escalated.extend(due)
            pending_internal -= set(due)
            if not dispatch.get("ok"):
                dispatch_failed = True
        if pending_internal:
            time.sleep(0.25)
    if dispatch_failed:
        print(
            "[AI-COLLAB] ERROR: the internal message exists, but one or more visible escalations failed. "
            "Do not claim that those agents started or replied.",
            file=sys.stderr,
        )
        return 2
    if not escalated:
        print("[AI-COLLAB] All recipients answered through internal collaboration; visible escalation was unnecessary.")
        return 0
    if wait_seconds > 0 and recipients:
        missing = wait_for_replies(path, recipients, kickoff_at, wait_seconds)
        if missing:
            print(
                "[AI-COLLAB] ERROR: visible prompts were submitted but real replies were not observed from: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 3
        print("[AI-COLLAB] Verified real thread replies from: " + ", ".join(recipients))
        missing_visual = missing_visual_attestations(path, recipients, visual_agents, kickoff_at)
        if missing_visual and mode == "strict":
            print(
                "[AI-COLLAB] ERROR: replies lacked required agent-authored visual evidence from: "
                + ", ".join(missing_visual),
                file=sys.stderr,
            )
            return 5
    post = (
        visual_proof(root, visual_agents, "after-visible-turn")
        if mode != "off"
        else {"ok": True, "skipped": True, "reason": "visual mode off"}
    )
    print("[AI-COLLAB] Post-turn visual proof: " + json.dumps(post, sort_keys=True))
    if not post.get("ok") and mode == "strict":
        print("[AI-COLLAB] ERROR: response/delivery exists but post-turn visual proof failed.", file=sys.stderr)
        return 4
    if not post.get("ok"):
        print(
            "[AI-COLLAB] WARNING: post-turn visual observation is ambiguous; responses and durable "
            "delivery evidence remain valid in observe mode.",
            file=sys.stderr,
        )
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    root = root_from_arg(args.root)
    recipients = normalize_slugs(args.to)
    visual_agents = visual_participants(root, args.author, recipients)
    kickoff_at = isoformat_z(utc_now())
    path = start_conversation(args)
    print(f"[AI-COLLAB] Conversation started: {path}")
    return dispatch_and_optionally_wait(
        path,
        root=root,
        author=args.author,
        recipients=recipients,
        kickoff_at=kickoff_at,
        queue_only=args.queue_only,
        internal_wait_seconds=args.internal_wait_seconds,
        wait_seconds=args.wait_seconds,
        visual_agents=visual_agents,
        requested_visual_mode=args.visual_mode,
    )


def cmd_reply(args: argparse.Namespace) -> int:
    root = root_from_arg(args.root)
    recipients = normalize_slugs(args.to)
    visual_agents = visual_participants(root, args.author, recipients)
    kickoff_at = isoformat_z(utc_now())
    path = reply_conversation(args)
    print(f"[AI-COLLAB] Conversation updated: {path}")
    return dispatch_and_optionally_wait(
        path,
        root=root,
        author=args.author,
        recipients=recipients,
        kickoff_at=kickoff_at,
        queue_only=args.queue_only,
        internal_wait_seconds=args.internal_wait_seconds,
        wait_seconds=args.wait_seconds,
        visual_agents=visual_agents,
        requested_visual_mode=args.visual_mode,
    )


def cmd_typed_reply(args: argparse.Namespace) -> int:
    root = root_from_arg(args.root)
    recipients = normalize_slugs(args.to)
    visual_agents = visual_participants(root, args.author, recipients)
    kickoff_at = isoformat_z(utc_now())
    path = reply_conversation(args, message_type=args.command)
    print(f"[AI-COLLAB] Conversation updated: {path}")
    return dispatch_and_optionally_wait(
        path,
        root=root,
        author=args.author,
        recipients=recipients,
        kickoff_at=kickoff_at,
        queue_only=args.queue_only,
        internal_wait_seconds=args.internal_wait_seconds,
        wait_seconds=args.wait_seconds,
        visual_agents=visual_agents,
        requested_visual_mode=args.visual_mode,
    )


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
    parser.add_argument("--queue-only", action="store_true", help="Write without activating visible agent interfaces.")
    parser.add_argument(
        "--internal-wait-seconds",
        type=int,
        default=int(os.environ["AI_COLLAB_INTERNAL_GRACE_SECONDS"]) if os.environ.get("AI_COLLAB_INTERNAL_GRACE_SECONDS") else -1,
        help="Wait for an internal thread/inbox response before notifying and escalating to visible chat.",
    )
    parser.add_argument("--wait-seconds", type=int, default=0, help="Wait for real replies from every --to recipient.")
    parser.add_argument(
        "--visual-mode",
        choices=sorted(VALID_VISUAL_MODES),
        default=os.environ.get("AI_COLLAB_VISUAL_MODE", "observe"),
        help="observe keeps the eyes active without blocking; strict requires verified visual evidence; off skips capture.",
    )


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
    start.add_argument("--discussion-id", default="", help="Stable id used to reuse one discussion across retries.")
    start.add_argument("--type", choices=sorted(VALID_MESSAGE_TYPES), default="message")
    start.add_argument("--queue-only", action="store_true", help="Write without activating visible agent interfaces.")
    start.add_argument(
        "--internal-wait-seconds",
        type=int,
        default=int(os.environ["AI_COLLAB_INTERNAL_GRACE_SECONDS"]) if os.environ.get("AI_COLLAB_INTERNAL_GRACE_SECONDS") else -1,
        help="Wait for an internal response before notifying and escalating to visible chat.",
    )
    start.add_argument("--wait-seconds", type=int, default=0, help="Wait for real replies from every --to recipient.")
    start.add_argument(
        "--visual-mode",
        choices=sorted(VALID_VISUAL_MODES),
        default=os.environ.get("AI_COLLAB_VISUAL_MODE", "observe"),
        help="observe keeps the eyes active without blocking; strict requires verified visual evidence; off skips capture.",
    )
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
