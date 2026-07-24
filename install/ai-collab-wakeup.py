#!/usr/bin/env python3
"""
Durable inbox wakeup detection for ai-collab.

Turns unread inbox files into durable wake events, then dispatches a wakeup
adapter. CLI execution is opt-in; the default adapter is notify-only.
"""
from __future__ import annotations

import glob
import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (5, 25, 125)
DEFAULT_EVENTS_FILE = Path.home() / ".ai-collab-wakeup-events.json"
DEFAULT_STATE_FILE = Path.home() / ".ai-collab-wakeup-state.json"
DEFAULT_LOG_FILE = Path("/tmp/ai-collab-wakeup.log")
DEFAULT_ADAPTER = "visible"
DEFAULT_ADAPTER_TIMEOUT_SECONDS = 120
DEFAULT_CLI_TARGETS = ("codex", "opencode", "claude", "claude-code", "hermes", "kimi", "kilo")
DEFAULT_VISIBLE_TARGETS = ("codex", "opencode", "kilo", "hermes")
OPENCODE_SYNTHETIC_ENV = "AI_COLLAB_OPENCODE_SYNTHETIC"
MAX_EVENTS = 200
MENTION_RE = re.compile(r"(?<![\w.-])@([a-z][a-z0-9_-]{1,40})\b")
FALLBACK_BIN_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path("/usr/bin"),
    Path("/bin"),
    Path.home() / ".local/bin",
    Path.home() / ".npm-global/bin",
)
FALLBACK_BIN_GLOBS = (
    ".nvm/versions/*/*/bin",
    ".antigravity/extensions/*/bin",
    ".antigravity/extensions/*/bin/*",
    ".antigravity-ide/extensions/*/bin",
    ".antigravity-ide/extensions/*/bin/*",
)


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


def parse_csv_value(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().strip("[]") for item in value.split(",") if item.strip().strip("[]")]


def render_csv_value(items: list[str]) -> str:
    return ", ".join(sorted(dict.fromkeys(items)))


def thread_id_from_path(thread_path: Path) -> str:
    stem = thread_path.stem
    if stem.startswith("thread-"):
        return stem[len("thread-") :]
    return stem


def collab_root_for_path(path: Path) -> Path:
    current = path.parent
    while current != current.parent:
        if current.name == ".ai-collab":
            return current
        current = current.parent
    return path.parent


def project_root_for_path(path: Path) -> Path:
    collab_root = collab_root_for_path(path)
    return collab_root.parent if collab_root.name == ".ai-collab" else path.parent.parent


def is_thread_file(path: Path) -> bool:
    return path.name.startswith("thread-") or path.parent.name == "discussions"


def project_agent_known(project_root: Path, target_slug: str) -> bool:
    collab = project_root / ".ai-collab"
    target_slug = target_slug.strip().lower()
    if not target_slug:
        return False

    agents = load_json(collab / "agents.json", {})
    if isinstance(agents, dict):
        roster = agents.get("agents", agents.get("roster", []))
        if isinstance(roster, list):
            for item in roster:
                if isinstance(item, str) and item.lower() == target_slug:
                    return True
                if isinstance(item, dict) and str(item.get("agent") or item.get("slug") or "").lower() == target_slug:
                    return True
        if target_slug in {str(key).lower() for key in agents.keys()}:
            return True

    try:
        team_text = (collab / "TEAM.md").read_text(encoding="utf-8").lower()
    except FileNotFoundError:
        team_text = ""
    if re.search(rf"(?m)^\s*-\s+{re.escape(target_slug)}(?:\s|\(|$)", team_text):
        return True
    if re.search(rf"(?m)^\|\s*{re.escape(target_slug)}\s*\|", team_text):
        return True

    if (collab / f"inbox-{target_slug}.md").exists():
        return True
    if any(collab.glob(f"{target_slug}-*.md")):
        return True
    return False


def find_mentions(message: str) -> list[str]:
    return sorted(dict.fromkeys(match.group(1).lower() for match in MENTION_RE.finditer(message)))


def latest_thread_message(body: str) -> dict[str, str] | None:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s+(?:--|—)\s+([a-zA-Z0-9_-]+)\s*$", body))
    if not matches:
        return None

    start = matches[-1].end()
    end_match = re.search(r"(?m)^---\s*$", body[start:])
    end = start + end_match.start() if end_match else len(body)
    content = body[start:end].strip()
    return {
        "timestamp": matches[-1].group(1).strip(),
        "author_slug": matches[-1].group(2).strip().lower(),
        "content": content,
    }


def message_hash(message: dict[str, str]) -> str:
    source = "\n".join([message.get("timestamp", ""), message.get("author_slug", ""), message.get("content", "")])
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def with_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    return lock_file


def append_thread_message(
    thread_path: Path,
    *,
    task_id: str,
    project: str,
    inbox_name: str,
    author_slug: str,
    message: str,
    now: datetime | None = None,
    close_thread: bool = False,
) -> None:
    now = now or utc_now()
    timestamp = isoformat_z(now)
    lock_file = with_lock(thread_path)
    try:
        try:
            text = thread_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""

        meta, body = parse_frontmatter(text)
        if meta.get("status") == "closed" and not close_thread:
            raise RuntimeError(f"thread is closed: {thread_path}")

        participants = parse_csv_value(meta.get("participants"))
        if author_slug not in participants:
            participants.append(author_slug)

        if not meta:
            meta = {
                "thread": task_id,
                "project": project,
                "inbox": inbox_name,
                "created": timestamp,
                "updated": timestamp,
                "participants": render_csv_value(participants),
                "status": "open",
            }
        else:
            meta.setdefault("thread", task_id)
            meta.setdefault("project", project)
            meta.setdefault("inbox", inbox_name)
            meta.setdefault("created", timestamp)
            meta.setdefault("status", "open")
            meta["updated"] = timestamp
            meta["participants"] = render_csv_value(participants)

        if close_thread:
            meta["status"] = "closed"

        clean_body = body.rstrip()
        section = f"## {timestamp} -- {author_slug}\n\n{message.strip()}\n\n---\n"
        new_body = f"{clean_body}\n\n{section}" if clean_body else section
        atomic_write(thread_path, render_frontmatter(meta, new_body))
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def write_agent_live_state(
    project_root: Path,
    *,
    agent_slug: str,
    phase: str,
    summary: str,
    task_id: str = "",
    now: datetime | None = None,
) -> Path:
    now = now or utc_now()
    live_path = project_root / ".ai-collab" / "live" / f"{agent_slug}.agent.json"
    payload = {
        "agent": agent_slug,
        "project": project_root.name,
        "updated": isoformat_z(now),
        "phase": phase,
        "summary": summary,
    }
    if task_id:
        payload["task_id"] = task_id
    write_json(live_path, payload)
    return live_path


def write_codex_session_log(
    project_root: Path,
    *,
    working_on: str,
    files_read: list[str],
    files_modified: list[str],
    decisions: list[str],
    issues: list[str],
    handoff: str,
    now: datetime | None = None,
) -> Path:
    now = now or utc_now()
    session = now.strftime("%Y%m%d-%H%M%S")
    timestamp = isoformat_z(now)
    log_path = project_root / ".ai-collab" / f"codex-{session}.md"

    def bullet(items: list[str]) -> str:
        return "\n".join(f"- `{item}`" if item.startswith(".") or item.startswith("/") else f"- {item}" for item in items)

    sections = [
        "---",
        "ai: Codex (filesystem wake adapter)",
        "agent: codex",
        "container: background",
        "model: deterministic-filesystem",
        f"session: {session}",
        f"project: {project_root.name}",
        f"updated: {timestamp}",
        "---",
        "",
        "## Working On",
        working_on,
        "",
        "## Files Read This Session",
        bullet(files_read) or "- None",
        "",
        "## Files Modified This Session",
        bullet(files_modified) or "- None",
        "",
        "## Decisions Made",
        "\n".join(f"- {item}" for item in decisions) or "- None",
        "",
        "## Issues Identified",
        "\n".join(f"- {item}" for item in issues) or "- None",
        "",
        "## Still In Progress",
        "- Nothing pending from this automatic wake response.",
        "",
        "## Do Not Touch (Avoid Conflicts)",
        "- None",
        "",
        "## Handoff Note",
        handoff,
        "",
    ]
    atomic_write(log_path, "\n".join(sections))
    return log_path


def referenced_thread_paths(project_root: Path, text: str) -> list[Path]:
    paths: list[Path] = []
    for match in re.finditer(r"`?(\.ai-collab/(?:thread-[^`\s]+|discussions/[^`\s]+\.md))`?", text):
        raw = match.group(1).rstrip(".,)")
        path = (project_root / raw).resolve()
        try:
            path.relative_to(project_root.resolve())
        except ValueError:
            continue
        if path.exists() and path.suffix == ".md":
            paths.append(path)
    return list(dict.fromkeys(paths))


def run_codex_filesystem_adapter(input_data: dict[str, str], *, now: datetime | None = None) -> dict[str, str]:
    """Deterministic Codex wake receipt path through collab files.

    This is intentionally modest: it does not pretend to be the visible Codex UI
    or an LLM worker. It records a verifiable receipt by writing collab artifacts,
    but returns degraded so callers do not mark the event as a completed wake.
    """
    now = now or utc_now()
    if input_data["target_slug"] != "codex":
        return {
            "status": "failed",
            "message": "codex-filesystem adapter only supports target codex",
            "adapter_name": "codex-filesystem",
        }
    project_root = Path(input_data["project_path"]).expanduser().resolve()
    if not (project_root / ".ai-collab").is_dir():
        return {
            "status": "failed",
            "message": "project has no .ai-collab directory",
            "adapter_name": "codex-filesystem",
        }

    task_id = input_data.get("task_id", "codex-wake")
    source_type = input_data.get("source_type", "")
    source_path = Path(input_data.get("source_path") or input_data.get("inbox_path") or "").expanduser()
    if not source_path.is_absolute():
        source_path = (project_root / source_path).resolve()
    files_read: list[str] = []
    files_modified: list[str] = []
    reply_targets: list[Path] = []

    if source_path.exists():
        files_read.append(str(source_path))

    if source_type == "thread" or source_path.parent.name == "discussions" or source_path.name.startswith("thread-"):
        reply_targets.append(source_path)
    else:
        try:
            inbox_text = source_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            inbox_text = ""
        for path in referenced_thread_paths(project_root, inbox_text):
            reply_targets.append(path)

    reply = (
        "type: answer\n"
        "to: all\n"
        "tags: wakeup, codex-filesystem\n\n"
        "@opencode Codex filesystem wake receipt was recorded automatically. "
        "This proves the collab files were reachable, but it did not wake a visible Codex session or run an LLM turn. "
        "If you need Codex to act, keep the task unread/retryable for a real adapter or wait for Codex's next preflight."
    )
    for target in list(dict.fromkeys(reply_targets)):
        append_thread_message(
            target,
            task_id=thread_id_from_path(target),
            project=project_root.name,
            inbox_name="",
            author_slug="codex-filesystem",
            message=reply,
            now=now,
        )
        files_modified.append(str(target))

    live_path = write_agent_live_state(
        project_root,
        agent_slug="codex",
        phase="done",
        summary="Recorded a Codex filesystem wake receipt; no visible Codex session was activated.",
        task_id=task_id,
        now=now,
    )
    files_modified.append(str(live_path))
    log_path = write_codex_session_log(
        project_root,
        working_on="Recorded a deterministic Codex wake receipt from AI Collab.",
        files_read=files_read,
        files_modified=files_modified,
        decisions=[
            "Used deterministic filesystem receipt because no public visible Codex panel inbound API is available.",
            "Returned degraded so wake retry/notified semantics stay honest.",
        ],
        issues=[],
        handoff="Codex wake receipt was verified through collab files. This does not imply visible panel injection or an LLM turn.",
        now=now,
    )
    files_modified.append(str(log_path))
    return {
        "status": "degraded",
        "message": "codex-filesystem recorded a wake receipt, but did not activate a visible Codex session",
        "adapter_name": "codex-filesystem",
    }


def adapter_mode_from_env() -> str:
    return os.environ.get("AI_COLLAB_WAKEUP_ADAPTER", DEFAULT_ADAPTER).strip() or DEFAULT_ADAPTER


def adapter_timeout_from_env() -> int:
    return max(1, coerce_int(os.environ.get("AI_COLLAB_WAKEUP_ADAPTER_TIMEOUT"), DEFAULT_ADAPTER_TIMEOUT_SECONDS))


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def path_matches(value: str, allowed: str) -> bool:
    if allowed == "*":
        return True
    path = Path(value).expanduser()
    allowed_path = Path(allowed).expanduser()
    if path.name == allowed:
        return True
    try:
        return path.resolve() == allowed_path.resolve()
    except OSError:
        return str(path) == str(allowed_path)


def same_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return left == right


def cli_project_allowed(project_path: str) -> bool:
    allowed = csv_env("AI_COLLAB_WAKEUP_CLI_PROJECTS")
    if not allowed:
        # Default: allow any project that has a .ai-collab/ directory. This is
        # the user-explicit signal that they want collab on this project.
        # Operators can still restrict with AI_COLLAB_WAKEUP_CLI_PROJECTS.
        return True
    return any(path_matches(project_path, item) for item in allowed)


def cli_target_allowed(target_slug: str) -> bool:
    allowed = csv_env("AI_COLLAB_WAKEUP_CLI_TARGETS")
    if not allowed:
        allowed = list(DEFAULT_CLI_TARGETS)
    return "*" in allowed or target_slug in allowed


def visible_target_allowed(target_slug: str) -> bool:
    allowed = csv_env("AI_COLLAB_WAKEUP_VISIBLE_TARGETS")
    if not allowed:
        allowed = csv_env("AI_COLLAB_WAKEUP_CLI_TARGETS")
    if not allowed:
        allowed = list(DEFAULT_VISIBLE_TARGETS)
    return "*" in allowed or target_slug in allowed


def visible_guardrail(input_data: dict[str, str], adapter_name: str) -> dict[str, str] | None:
    if truthy_env("AI_COLLAB_WAKEUP_DRY_RUN"):
        return {
            "status": "degraded",
            "message": f"{adapter_name} dry-run: prompt not sent",
            "adapter_name": f"{adapter_name}-dry-run",
        }
    if not cli_project_allowed(input_data["project_path"]):
        return {
            "status": "degraded",
            "message": f"{adapter_name} blocked: project is not in AI_COLLAB_WAKEUP_CLI_PROJECTS",
            "adapter_name": f"{adapter_name}-guardrail",
        }
    if not visible_target_allowed(input_data["target_slug"]):
        return {
            "status": "degraded",
            "message": f"{adapter_name} blocked: target is not in AI_COLLAB_WAKEUP_VISIBLE_TARGETS",
            "adapter_name": f"{adapter_name}-guardrail",
        }
    return None


def executable_for(target_slug: str) -> str | None:
    env_key = f"AI_COLLAB_{target_slug.upper().replace('-', '_')}_BIN"
    configured = os.environ.get(env_key)
    if configured:
        return configured

    candidates = {
        "codex": ["codex"],
        "opencode": ["opencode"],
        "claude": ["claude"],
        "claude-code": ["claude"],
        "antigravity": ["antigravity"],
        "hermes": ["hermes"],
        "kimi": ["kimi", "kimi-cli"],
        "kilo": ["kilo"],
    }.get(target_slug, [target_slug])

    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
        for directory in FALLBACK_BIN_DIRS:
            fallback = directory / candidate
            if fallback.exists() and os.access(fallback, os.X_OK):
                return str(fallback)
        for pattern in FALLBACK_BIN_GLOBS:
            matches = glob.glob(pattern) if Path(pattern).is_absolute() else [str(path) for path in Path.home().glob(pattern)]
            for match in matches:
                directory = Path(match)
                if directory.name == candidate and directory.exists() and os.access(directory, os.X_OK) and directory.is_file():
                    return str(directory)
                fallback = directory / candidate
                if fallback.exists() and os.access(fallback, os.X_OK) and fallback.is_file():
                    return str(fallback)
    return None


def build_cli_command(input_data: dict[str, str]) -> list[str] | None:
    target = input_data["target_slug"]
    exe = executable_for(target)
    if not exe:
        return None

    project_path = input_data["project_path"]
    inbox_path = input_data["inbox_path"]
    prompt = input_data["synthetic_prompt"]

    if target == "codex":
        return [
            exe,
            "--cd",
            project_path,
            "--ask-for-approval",
            "never",
            "--sandbox",
            "workspace-write",
            "exec",
            "--skip-git-repo-check",
            prompt,
        ]
    if target == "opencode":
        return [exe, "run", prompt, "--dir", project_path, "--file", inbox_path]
    if target in {"claude", "claude-code"}:
        return [exe, "-p", "--permission-mode", "acceptEdits", "--add-dir", project_path, prompt]
    if target == "kimi":
        return [exe, prompt]
    if target == "kilo":
        return [exe, "run", prompt, project_path]
    return [exe, prompt]


def ps_commands(runner=subprocess.run) -> str:
    try:
        completed = runner(
            ["ps", "ax", "-o", "command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def opencode_processes(runner=subprocess.run) -> list[dict[str, int]]:
    try:
        completed = runner(
            ["ps", "ax", "-o", "pid=,command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    processes: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for line in (completed.stdout or "").splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        command = match.group(2)
        port_match = re.search(r"\bopencode\b.*?(?:--port(?:=|\s+))(\d+)", command)
        if not port_match:
            continue
        port = int(port_match.group(1))
        marker = (pid, port)
        if marker not in seen and 0 < port < 65536:
            seen.add(marker)
            processes.append({"pid": pid, "port": port})
    return processes


def process_cwd(pid: int, runner=subprocess.run) -> str | None:
    proc_cwd = Path(f"/proc/{pid}/cwd")
    try:
        if proc_cwd.exists():
            return os.readlink(proc_cwd)
    except OSError:
        pass

    try:
        completed = runner(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in (completed.stdout or "").splitlines():
        if line.startswith("n") and len(line) > 1:
            return line[1:]
    return None


def opencode_processes_by_port(runner=subprocess.run) -> dict[int, list[dict[str, Any]]]:
    by_port: dict[int, list[dict[str, Any]]] = {}
    for process in opencode_processes(runner=runner):
        pid = process["pid"]
        port = process["port"]
        cwd = process_cwd(pid, runner=runner)
        by_port.setdefault(port, []).append({"pid": pid, "cwd": cwd})
    return by_port


def discover_opencode_ports(runner=subprocess.run) -> list[int]:
    ports: list[int] = []
    for value in csv_env("AI_COLLAB_OPENCODE_PORTS"):
        try:
            ports.append(int(value))
        except ValueError:
            continue

    commands = ps_commands(runner=runner)
    for match in re.finditer(r"\bopencode\b.*?(?:--port(?:=|\s+))(\d+)", commands):
        ports.append(int(match.group(1)))

    deduped: list[int] = []
    for port in ports:
        if port not in deduped and 0 < port < 65536:
            deduped.append(port)
    return deduped


def discover_kilo_ports(runner=subprocess.run) -> list[int]:
    ports: list[int] = []
    for value in csv_env("AI_COLLAB_KILO_PORTS"):
        try:
            ports.append(int(value))
        except ValueError:
            continue

    commands = ps_commands(runner=runner)
    for match in re.finditer(r"\bkilo\b.*?(?:--port(?:=|\s+))(\d+)", commands):
        ports.append(int(match.group(1)))

    deduped: list[int] = []
    for port in ports:
        if port not in deduped and 0 < port < 65536:
            deduped.append(port)
    return deduped


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(400).decode("utf-8", errors="replace")
            return response.status, text
    except urllib.error.HTTPError as exc:
        text = exc.read(400).decode("utf-8", errors="replace")
        return exc.code, text
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def get_json(url: str, *, timeout: int) -> tuple[int, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as exc:
        text = exc.read(400).decode("utf-8", errors="replace")
        return exc.code, text
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def auth_headers_from_env(prefix: str) -> dict[str, str]:
    bearer = os.environ.get(f"AI_COLLAB_{prefix}_BEARER_TOKEN")
    if bearer:
        return {"Authorization": f"Bearer {bearer}"}
    basic = os.environ.get(f"AI_COLLAB_{prefix}_BASIC_AUTH")
    if basic:
        encoded = base64.b64encode(basic.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


def opencode_port_project_candidates(port: int, *, timeout: int, getter=None) -> list[dict[str, str]]:
    getter = getter or get_json
    candidates: list[dict[str, str]] = []
    status, body = getter(f"http://127.0.0.1:{port}/project/current", timeout=timeout)
    if 200 <= status < 300 and isinstance(body, dict):
        for key in ("worktree", "directory", "path", "root"):
            value = body.get(key)
            if isinstance(value, str) and value:
                candidates.append({"source": f"project/current.{key}", "path": value})

    status, body = getter(f"http://127.0.0.1:{port}/session", timeout=timeout)
    if 200 <= status < 300 and isinstance(body, list):
        for session in body:
            if not isinstance(session, dict):
                continue
            for key in ("directory", "path"):
                value = session.get(key)
                if isinstance(value, str) and value:
                    candidates.append({"source": f"session.{key}", "path": value})

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        marker = (item["source"], item["path"])
        if marker not in seen:
            seen.add(marker)
            deduped.append(item)
    return deduped


def opencode_port_project(port: int, *, timeout: int, getter=None) -> str | None:
    for item in opencode_port_project_candidates(port, timeout=timeout, getter=getter):
        if item["source"].startswith("project/current."):
            return item["path"]
    return None


def opencode_port_matches_project(port: int, project_path: str, *, timeout: int, getter=None) -> bool:
    return any(same_path(item["path"], project_path) for item in opencode_port_project_candidates(port, timeout=timeout, getter=getter))


def opencode_api_allows_cwd_fallback(port: int, *, timeout: int, getter=None) -> bool:
    current_paths = [
        item["path"]
        for item in opencode_port_project_candidates(port, timeout=timeout, getter=getter)
        if item["source"].startswith("project/current.")
    ]
    if not current_paths:
        return True
    return all(same_path(path, "/") for path in current_paths)


def opencode_port_process_matches_project(
    port: int,
    project_path: str,
    *,
    processes_by_port: dict[int, list[dict[str, Any]]],
) -> bool:
    matches = [
        process
        for process in processes_by_port.get(port, [])
        if process.get("cwd") and same_path(str(process["cwd"]), project_path)
    ]
    return len(matches) == 1


def opencode_port_diagnostics(
    ports: list[int],
    *,
    timeout: int,
    getter=None,
    processes_by_port: dict[int, list[dict[str, Any]]] | None = None,
) -> str:
    parts: list[str] = []
    processes_by_port = processes_by_port or {}
    for port in ports:
        candidates = opencode_port_project_candidates(port, timeout=timeout, getter=getter)
        process_details = processes_by_port.get(port, [])
        process_text = ", ".join(
            f"process.pid={item.get('pid')} process.cwd={item.get('cwd') or 'unknown'}"
            for item in process_details
        )
        if not candidates:
            suffix = f", {process_text}" if process_text else ""
            parts.append(f"{port}: no project/session metadata{suffix}")
            continue
        paths = ", ".join(f"{item['source']}={item['path']}" for item in candidates[:4])
        if len(candidates) > 4:
            paths += f", +{len(candidates) - 4} more"
        if process_text:
            paths += f", {process_text}"
        parts.append(f"{port}: {paths}")
    return "; ".join(parts)


def discover_opencode_active_session(
    port: int,
    *,
    timeout: int,
    getter=None,
    project_path: str | None = None,
) -> str | None:
    getter = getter or get_json
    status, body = getter(f"http://127.0.0.1:{port}/session", timeout=timeout)
    if not (200 <= status < 300) or not isinstance(body, list) or not body:
        return None

    def matches_project(session: dict) -> bool:
        if not project_path:
            return True
        directory = session.get("directory") or ""
        try:
            return Path(directory).resolve() == Path(project_path).resolve()
        except OSError:
            return directory == project_path

    candidates = [s for s in body if isinstance(s, dict) and s.get("id") and matches_project(s)]
    if not candidates:
        return None

    def updated_at(session: dict) -> int:
        time = session.get("time") or {}
        return int(time.get("updated") or time.get("created") or 0)

    candidates.sort(key=updated_at, reverse=True)
    return candidates[0]["id"]


def run_opencode_visible_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
    poster=None,
    getter=None,
) -> dict[str, str]:
    poster = poster or post_json
    getter = getter or get_json
    guardrail = visible_guardrail(input_data, "opencode-visible")
    if guardrail:
        return guardrail
    if input_data["target_slug"] != "opencode":
        return {
            "status": "failed",
            "message": "opencode-visible adapter only supports target opencode",
            "adapter_name": "opencode-visible",
        }

    ports = discover_opencode_ports(runner=runner)
    if not ports:
        return {
            "status": "failed",
            "message": "no visible OpenCode TUI port found; open the OpenCode panel first",
            "adapter_name": "opencode-visible",
        }

    prompt = input_data["synthetic_prompt"]
    synthetic = truthy_env(OPENCODE_SYNTHETIC_ENV)
    adapter_name = "opencode-synthetic" if synthetic else "opencode-visible"
    project_path = input_data.get("project_path")
    last_error = ""
    fast_timeout = min(timeout, 10)
    # Only deliver visible prompts to a TUI whose project matches the inbox's
    # project_path. Falling back to any other OpenCode port can wake a different
    # project, which is worse than leaving the event retryable. Non-git
    # workspaces are reported by OpenCode as the global "/" project, so those
    # ports may fall back to an exact PID -> cwd match.
    processes_by_port = opencode_processes_by_port(runner=runner)
    api_project_ports: list[int] = []
    cwd_project_ports: list[int] = []
    for port in ports:
        if project_path and opencode_port_matches_project(port, project_path, timeout=fast_timeout, getter=getter):
            api_project_ports.append(port)
            continue
        if (
            project_path
            and not synthetic
            and opencode_api_allows_cwd_fallback(port, timeout=fast_timeout, getter=getter)
            and opencode_port_process_matches_project(
                port,
                project_path,
                processes_by_port=processes_by_port,
            )
        ):
            cwd_project_ports.append(port)

    if len(api_project_ports) > 1:
        diagnostics = opencode_port_diagnostics(
            ports,
            timeout=fast_timeout,
            getter=getter,
            processes_by_port=processes_by_port,
        )
        return {
            "status": "failed",
            "message": f"multiple OpenCode ports matched project metadata; refusing ambiguous wakeup. candidates: {diagnostics}",
            "adapter_name": "opencode-visible",
        }
    if api_project_ports:
        ordered_ports = api_project_ports
        cwd_fallback_port = None
    elif len(cwd_project_ports) > 1:
        diagnostics = opencode_port_diagnostics(
            ports,
            timeout=fast_timeout,
            getter=getter,
            processes_by_port=processes_by_port,
        )
        return {
            "status": "failed",
            "message": f"multiple OpenCode process cwd values matched project; refusing ambiguous wakeup. candidates: {diagnostics}",
            "adapter_name": "opencode-visible",
        }
    elif cwd_project_ports:
        ordered_ports = cwd_project_ports
        cwd_fallback_port = cwd_project_ports[0]
    elif project_path:
        diagnostics = opencode_port_diagnostics(
            ports,
            timeout=fast_timeout,
            getter=getter,
            processes_by_port=processes_by_port,
        )
        return {
            "status": "failed",
            "message": (
                "no visible OpenCode TUI port matched project "
                f"{project_path}; refusing cross-project wakeup. candidates: {diagnostics}"
            ),
            "adapter_name": "opencode-visible",
        }
    else:
        ordered_ports = ports
        cwd_fallback_port = None

    def tui_url(port: int, path: str) -> str:
        query = {}
        if project_path:
            query["directory"] = project_path
        encoded = urllib.parse.urlencode(query)
        suffix = f"?{encoded}" if encoded else ""
        return f"http://127.0.0.1:{port}{path}{suffix}"

    for port in ordered_ports:
        if port == cwd_fallback_port:
            refreshed_processes = opencode_processes_by_port(runner=runner)
            if not opencode_port_process_matches_project(
                port,
                project_path,
                processes_by_port=refreshed_processes,
            ):
                last_error = f"port {port}: OpenCode process cwd changed before wakeup; refusing stale target"
                continue
        if not synthetic:
            # True visible mode targets the active TUI prompt box, then submits
            # it. Posting to /session/{id}/prompt_async can execute in a
            # session that is not the one the user is currently viewing.
            clear_status, clear_text = poster(
                tui_url(port, "/tui/clear-prompt"),
                {},
                timeout=fast_timeout,
            )
            if not (200 <= clear_status < 300):
                last_error = f"port {port} clear-prompt returned {clear_status}: {clear_text}".strip()
                continue
            append_status, append_text = poster(
                tui_url(port, "/tui/append-prompt"),
                {"text": prompt},
                timeout=fast_timeout,
            )
            if not (200 <= append_status < 300):
                last_error = f"port {port} append-prompt returned {append_status}: {append_text}".strip()
                continue
            submit_status, submit_text = poster(
                tui_url(port, "/tui/submit-prompt"),
                {},
                timeout=fast_timeout,
            )
            if 200 <= submit_status < 300:
                return {
                    "status": "success",
                    "message": f"visible prompt submitted to OpenCode TUI on port {port}",
                    "adapter_name": adapter_name,
                }
            last_error = f"port {port} submit-prompt returned {submit_status}: {submit_text}".strip()
            continue

        session_id = discover_opencode_active_session(
            port,
            timeout=fast_timeout,
            getter=getter,
            project_path=project_path if port in api_project_ports else None,
        )
        if not session_id:
            last_error = f"port {port}: no matching session found"
            continue
        status, text = poster(
            f"http://127.0.0.1:{port}/session/{session_id}/prompt_async",
            {
                "parts": [
                    {
                        "type": "text",
                        "text": prompt,
                        "synthetic": synthetic,
                    }
                ]
            },
            timeout=fast_timeout,
        )
        if 200 <= status < 300:
            return {
                "status": "success",
                "message": (
                    f"{'synthetic' if synthetic else 'visible'} prompt delivered to "
                    f"OpenCode session {session_id} on port {port}"
                ),
                "adapter_name": adapter_name,
            }
        last_error = f"port {port} session {session_id} returned {status}: {text}".strip()

    return {
        "status": "failed",
        "message": last_error or "visible OpenCode TUI did not accept wakeup prompt",
        "adapter_name": "opencode-visible",
    }


def run_opencode_auto_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    visible_result = run_opencode_visible_adapter(input_data, timeout=timeout, runner=runner)
    if visible_result.get("status") == "success":
        return visible_result

    cli_result = run_cli_adapter(input_data, timeout=timeout, runner=runner)
    cli_result["fallback_from"] = visible_result.get("adapter_name", "opencode-visible")
    cli_result["fallback_reason"] = visible_result.get("message", "")
    return cli_result


def run_kilo_visible_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
    poster=None,
) -> dict[str, str]:
    poster = poster or post_json
    guardrail = visible_guardrail(input_data, "kilo-visible")
    if guardrail:
        return guardrail
    if input_data["target_slug"] != "kilo":
        return {
            "status": "failed",
            "message": "kilo-visible adapter only supports target kilo",
            "adapter_name": "kilo-visible",
        }

    ports = discover_kilo_ports(runner=runner)
    if not ports:
        return {
            "status": "failed",
            "message": "no visible Kilo server port found; open the Kilo panel first",
            "adapter_name": "kilo-visible",
        }

    prompt = input_data["synthetic_prompt"]
    project_path = input_data.get("project_path")
    fast_timeout = min(timeout, 10)
    headers = auth_headers_from_env("KILO")
    last_error = ""

    def tui_url(port: int, path: str) -> str:
        query = {}
        if project_path:
            query["directory"] = project_path
        encoded = urllib.parse.urlencode(query)
        suffix = f"?{encoded}" if encoded else ""
        return f"http://127.0.0.1:{port}{path}{suffix}"

    for port in ports:
        clear_status, clear_text = poster(
            tui_url(port, "/tui/clear-prompt"),
            {},
            timeout=fast_timeout,
            headers=headers,
        )
        if clear_status == 401:
            last_error = (
                f"port {port} requires auth; set AI_COLLAB_KILO_BASIC_AUTH or "
                "AI_COLLAB_KILO_BEARER_TOKEN"
            )
            continue
        if not (200 <= clear_status < 300):
            last_error = f"port {port} clear-prompt returned {clear_status}: {clear_text}".strip()
            continue
        append_status, append_text = poster(
            tui_url(port, "/tui/append-prompt"),
            {"text": prompt},
            timeout=fast_timeout,
            headers=headers,
        )
        if not (200 <= append_status < 300):
            last_error = f"port {port} append-prompt returned {append_status}: {append_text}".strip()
            continue
        submit_status, submit_text = poster(
            tui_url(port, "/tui/submit-prompt"),
            {},
            timeout=fast_timeout,
            headers=headers,
        )
        if 200 <= submit_status < 300:
            return {
                "status": "success",
                "message": f"visible prompt submitted to Kilo TUI on port {port}",
                "adapter_name": "kilo-visible",
            }
        last_error = f"port {port} submit-prompt returned {submit_status}: {submit_text}".strip()

    return {
        "status": "failed",
        "message": last_error or "visible Kilo TUI did not accept wakeup prompt",
        "adapter_name": "kilo-visible",
    }


def antigravity_executable() -> str | None:
    configured = os.environ.get("AI_COLLAB_ANTIGRAVITY_BIN")
    if configured:
        return configured
    found = executable_for("antigravity")
    if found:
        return found
    fallback = Path.home() / ".antigravity/antigravity/bin/antigravity"
    if fallback.exists() and os.access(fallback, os.X_OK):
        return str(fallback)
    return None


def build_antigravity_chat_command(input_data: dict[str, str]) -> list[str] | None:
    exe = antigravity_executable()
    if not exe:
        return None
    mode = os.environ.get("AI_COLLAB_ANTIGRAVITY_MODE", "agent").strip() or "agent"
    command = [exe, "chat", "--mode", mode, "--reuse-window"]
    source = input_data.get("inbox_path") or input_data.get("source_path")
    if source:
        command.extend(["--add-file", source])
    command.append(input_data["synthetic_prompt"])
    return command


def run_antigravity_chat_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    guardrail = visible_guardrail(input_data, "antigravity-chat")
    if guardrail:
        return guardrail
    if input_data["target_slug"] not in {"codex", "antigravity"}:
        return {
            "status": "failed",
            "message": "antigravity-chat adapter supports target codex/antigravity",
            "adapter_name": "antigravity-chat",
        }

    command = build_antigravity_chat_command(input_data)
    if not command:
        return {
            "status": "failed",
            "message": "no antigravity executable found",
            "adapter_name": "antigravity-chat",
        }

    try:
        completed = runner(
            command,
            cwd=input_data["project_path"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"antigravity chat timed out after {timeout}s", "adapter_name": "antigravity-chat"}
    except OSError as exc:
        return {"status": "failed", "message": f"antigravity chat failed to start: {exc}", "adapter_name": "antigravity-chat"}

    if completed.returncode == 0:
        return {
            "status": "degraded",
            "message": "prompt sent to Antigravity chat with --reuse-window",
            "adapter_name": "antigravity-chat",
        }

    stderr = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
    if len(stderr) > 300:
        stderr = stderr[:300] + "...[truncated]"
    return {
        "status": "failed",
        "message": f"antigravity chat exited {completed.returncode}: {stderr}",
        "adapter_name": "antigravity-chat",
    }


def codex_acp_command() -> list[str]:
    configured = os.environ.get("AI_COLLAB_CODEX_ACP_COMMAND")
    if configured:
        return shlex.split(configured)

    exe = executable_for("codex-acp")
    if exe:
        return [exe]

    npx = executable_for("npx") or "npx"
    return [npx, "-y", "@zed-industries/codex-acp@latest"]


def acp_command_for(target_slug: str) -> list[str] | None:
    configured = os.environ.get(f"AI_COLLAB_{target_slug.upper().replace('-', '_')}_ACP_COMMAND")
    if configured:
        return shlex.split(configured)
    if target_slug == "codex":
        return codex_acp_command()
    exe = executable_for(target_slug)
    if not exe:
        return None
    if target_slug in {"hermes", "kimi", "kilo"}:
        return [exe, "acp"]
    return None


def build_acp_messages(input_data: dict[str, str]) -> list[dict[str, Any]]:
    prompt = (
        f"{input_data['synthetic_prompt']}\n\n"
        f"Project path: {input_data['project_path']}\n"
        f"Inbox path: {input_data['inbox_path']}\n"
        f"Task id: {input_data['task_id']}\n"
    )
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientInfo": {"name": "ai-collab-wakeup", "version": "0.1.0"},
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": input_data["project_path"], "mcpServers": []},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": "$SESSION_ID",
                "prompt": [{"type": "text", "text": prompt}],
            },
        },
    ]


def build_codex_acp_messages(input_data: dict[str, str]) -> list[dict[str, Any]]:
    return build_acp_messages(input_data)


def send_acp_message(process, message: dict[str, Any]) -> None:
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def read_acp_response(process, response_id: int, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"codex-acp exited before response id={response_id}")

        remaining = max(0.05, min(0.25, deadline - time.monotonic()))
        ready, _write, _err = select.select([process.stdout], [], [], remaining)
        if not ready:
            continue

        line = process.stdout.readline()
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != response_id:
            continue
        if "error" in message:
            raise RuntimeError(json.dumps(message["error"], sort_keys=True))
        return message.get("result", {})

    raise TimeoutError(f"timed out waiting for codex-acp response id={response_id}")


def run_codex_acp_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    popen=subprocess.Popen,
) -> dict[str, str]:
    if truthy_env("AI_COLLAB_WAKEUP_DRY_RUN"):
        return {
            "status": "degraded",
            "message": "codex-acp dry-run: ACP agent not started",
            "adapter_name": "codex-acp-dry-run",
        }
    if not cli_project_allowed(input_data["project_path"]):
        return {
            "status": "degraded",
            "message": "codex-acp blocked: project is not in AI_COLLAB_WAKEUP_CLI_PROJECTS",
            "adapter_name": "codex-acp-guardrail",
        }
    if input_data["target_slug"] != "codex":
        return {
            "status": "failed",
            "message": "codex-acp adapter only supports target codex",
            "adapter_name": "codex-acp",
        }

    command = codex_acp_command()
    messages = build_codex_acp_messages(input_data)
    process = None
    try:
        process = popen(
            command,
            cwd=input_data["project_path"],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        deadline = time.monotonic() + timeout
        send_acp_message(process, messages[0])
        read_acp_response(process, 1, deadline)
        send_acp_message(process, messages[1])
        new_session = read_acp_response(process, 2, deadline)
        session_id = new_session.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return {
                "status": "failed",
                "message": "codex-acp did not return a sessionId",
                "adapter_name": "codex-acp",
            }
        messages[2]["params"]["sessionId"] = session_id
        send_acp_message(process, messages[2])
        read_acp_response(process, 3, deadline)
        return {
            "status": "success",
            "message": f"task processed by invisible Codex ACP session {session_id}",
            "adapter_name": "codex-acp",
        }
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"codex-acp timed out after {timeout}s", "adapter_name": "codex-acp"}
    except (OSError, RuntimeError, TimeoutError) as exc:
        return {"status": "failed", "message": f"codex-acp failed: {exc}", "adapter_name": "codex-acp"}
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()


def run_acp_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    popen=subprocess.Popen,
) -> dict[str, str]:
    target = input_data["target_slug"]
    if target == "codex":
        return run_codex_acp_adapter(input_data, timeout=timeout, popen=popen)
    if truthy_env("AI_COLLAB_WAKEUP_DRY_RUN"):
        return {
            "status": "degraded",
            "message": f"{target}-acp dry-run: ACP agent not started",
            "adapter_name": f"{target}-acp-dry-run",
        }
    if not cli_project_allowed(input_data["project_path"]):
        return {
            "status": "degraded",
            "message": f"{target}-acp blocked: project is not in AI_COLLAB_WAKEUP_CLI_PROJECTS",
            "adapter_name": f"{target}-acp-guardrail",
        }
    if target not in {"hermes", "kimi", "kilo"}:
        return {
            "status": "failed",
            "message": f"ACP adapter has no implementation for target {target}",
            "adapter_name": "acp",
        }

    command = acp_command_for(target)
    if not command:
        return {
            "status": "failed",
            "message": f"no ACP command found for target {target}",
            "adapter_name": f"{target}-acp",
        }

    messages = build_acp_messages(input_data)
    process = None
    try:
        process = popen(
            command,
            cwd=input_data["project_path"],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        deadline = time.monotonic() + timeout
        send_acp_message(process, messages[0])
        read_acp_response(process, 1, deadline)
        send_acp_message(process, messages[1])
        new_session = read_acp_response(process, 2, deadline)
        session_id = new_session.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return {
                "status": "failed",
                "message": f"{target}-acp did not return a sessionId",
                "adapter_name": f"{target}-acp",
            }
        messages[2]["params"]["sessionId"] = session_id
        send_acp_message(process, messages[2])
        read_acp_response(process, 3, deadline)
        return {
            "status": "success",
            "message": f"task processed by {target} ACP session {session_id}",
            "adapter_name": f"{target}-acp",
        }
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"{target}-acp timed out after {timeout}s", "adapter_name": f"{target}-acp"}
    except (OSError, RuntimeError, TimeoutError) as exc:
        return {"status": "failed", "message": f"{target}-acp failed: {exc}", "adapter_name": f"{target}-acp"}
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()


def run_hermes_uri_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    guardrail = visible_guardrail(input_data, "hermes-uri")
    if guardrail:
        return guardrail
    if input_data["target_slug"] != "hermes":
        return {
            "status": "failed",
            "message": "hermes-uri adapter only supports target hermes",
            "adapter_name": "hermes-uri",
        }

    template = os.environ.get(
        "AI_COLLAB_HERMES_URI_TEMPLATE",
        "vscode://layerdynamics.hermes-vscode?prompt={prompt}",
    )
    uri = template.format(prompt=urllib.parse.quote(input_data["synthetic_prompt"], safe=""))
    try:
        completed = runner(
            ["open", uri],
            cwd=input_data["project_path"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"hermes URI open timed out after {timeout}s", "adapter_name": "hermes-uri"}
    except OSError as exc:
        return {"status": "failed", "message": f"hermes URI open failed: {exc}", "adapter_name": "hermes-uri"}

    if completed.returncode == 0:
        return {
            "status": "degraded",
            "message": "Hermes chat opened/prefilled via URI; user may need to press send",
            "adapter_name": "hermes-uri",
        }
    stderr = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
    if len(stderr) > 300:
        stderr = stderr[:300] + "...[truncated]"
    return {"status": "failed", "message": f"hermes URI open exited {completed.returncode}: {stderr}", "adapter_name": "hermes-uri"}


def run_visible_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    target = input_data["target_slug"]
    if target == "opencode":
        return run_opencode_visible_adapter(input_data, timeout=timeout, runner=runner)
    if target == "kilo":
        return run_kilo_visible_adapter(input_data, timeout=timeout, runner=runner)
    if target == "hermes":
        return run_hermes_uri_adapter(input_data, timeout=timeout, runner=runner)
    if target in {"codex", "antigravity"}:
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    return {
        "status": "failed",
        "message": f"visible adapter has no implementation for target {target}",
        "adapter_name": "visible",
    }


def run_cli_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    if truthy_env("AI_COLLAB_WAKEUP_DRY_RUN"):
        return {
            "status": "degraded",
            "message": "CLI adapter dry-run: command not executed",
            "adapter_name": "cli-dry-run",
        }
    if not cli_project_allowed(input_data["project_path"]):
        return {
            "status": "degraded",
            "message": "CLI adapter blocked: project is not in AI_COLLAB_WAKEUP_CLI_PROJECTS",
            "adapter_name": "cli-guardrail",
        }
    if not cli_target_allowed(input_data["target_slug"]):
        return {
            "status": "degraded",
            "message": "CLI adapter blocked: target is not in AI_COLLAB_WAKEUP_CLI_TARGETS",
            "adapter_name": "cli-guardrail",
        }

    command = build_cli_command(input_data)
    if not command:
        return {
            "status": "failed",
            "message": f"no CLI executable found for target {input_data['target_slug']}",
            "adapter_name": "cli",
        }

    try:
        completed = runner(
            command,
            cwd=input_data["project_path"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "message": f"CLI adapter timed out after {timeout}s", "adapter_name": "cli"}
    except OSError as exc:
        return {"status": "failed", "message": f"CLI adapter failed to start: {exc}", "adapter_name": "cli"}

    if completed.returncode == 0:
        return {"status": "success", "message": "CLI adapter accepted task", "adapter_name": "cli"}

    stderr = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")
    if len(stderr) > 300:
        stderr = stderr[:300] + "...[truncated]"
    return {
        "status": "failed",
        "message": f"CLI adapter exited {completed.returncode}: {stderr}",
        "adapter_name": "cli",
    }


def run_wakeup_adapter(
    input_data: dict[str, str],
    *,
    mode: str | None = None,
    timeout: int | None = None,
    runner=subprocess.run,
) -> dict[str, str]:
    mode = mode or adapter_mode_from_env()
    timeout = timeout or adapter_timeout_from_env()

    if mode == "mock-success":
        return {"status": "success", "message": "mock adapter accepted task", "adapter_name": "mock-success"}
    if mode == "mock-failed":
        return {"status": "failed", "message": "mock adapter failed task", "adapter_name": "mock-failed"}
    if mode == "notify-only":
        return {
            "status": "degraded",
            "message": "wake event recorded; no active execution adapter configured",
            "adapter_name": "notify-only",
        }
    if mode == "visible":
        return run_visible_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "opencode-visible":
        return run_opencode_visible_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "opencode-auto":
        return run_opencode_auto_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "antigravity-chat":
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "codex-filesystem":
        return run_codex_filesystem_adapter(input_data)
    if mode == "codex-auto":
        acp_result = run_codex_acp_adapter(input_data, timeout=timeout)
        if acp_result.get("status") == "success":
            return acp_result
        cli_result = run_cli_adapter(input_data, timeout=timeout, runner=runner)
        if cli_result.get("status") == "success":
            cli_result["fallback_from"] = acp_result.get("adapter_name", "codex-acp")
            cli_result["fallback_reason"] = acp_result.get("message", "")
            return cli_result
        filesystem_result = run_codex_filesystem_adapter(input_data)
        filesystem_result["fallback_from"] = cli_result.get("adapter_name", "cli")
        filesystem_result["fallback_reason"] = cli_result.get("message", "")
        filesystem_result["primary_failure"] = acp_result.get("message", "")
        return filesystem_result
    if mode == "kilo-visible":
        return run_kilo_visible_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "hermes-uri":
        return run_hermes_uri_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "codex-acp":
        return run_codex_acp_adapter(input_data, timeout=timeout)
    if mode == "acp":
        return run_acp_adapter(input_data, timeout=timeout)
    if mode in {"hermes-acp", "kimi-acp", "kilo-acp"}:
        expected = mode.removesuffix("-acp")
        if input_data["target_slug"] != expected:
            return {"status": "failed", "message": f"{mode} only supports target {expected}", "adapter_name": mode}
        return run_acp_adapter(input_data, timeout=timeout)
    if mode == "cli":
        return run_cli_adapter(input_data, timeout=timeout, runner=runner)
    return {"status": "failed", "message": f"unknown adapter mode: {mode}", "adapter_name": mode}


def dispatch_wake_event(
    event: dict[str, Any],
    *,
    events_file: Path,
    adapter_mode: str | None = None,
    adapter_runner=subprocess.run,
) -> dict[str, Any]:
    append_event(events_file, event)
    adapter_input = {
        "project_path": event["project_path"],
        "target_slug": event["target_slug"],
        "inbox_path": event.get("inbox_path") or event["source_path"],
        "source_path": event.get("source_path", ""),
        "source_type": event.get("source_type", ""),
        "thread_path": event.get("thread_path", ""),
        "task_id": event["task_id"],
        "synthetic_prompt": event["synthetic_prompt"],
    }
    adapter_result = run_wakeup_adapter(adapter_input, mode=adapter_mode, runner=adapter_runner)
    event["adapter_result"] = adapter_result
    append_event(events_file, {**event, "event_type": "adapter_result"})
    return adapter_result


def close_thread_for_inbox(
    inbox_path: Path,
    project: str,
    meta: dict[str, str],
    *,
    now: datetime | None = None,
    log_file: Path = DEFAULT_LOG_FILE,
) -> bool:
    status = meta.get("status", "")
    if status not in {"done", "failed"}:
        return False

    task_id = meta.get("task_id") or f"{project}:{inbox_path.name}"
    thread_path = inbox_path.parent / f"thread-{task_id}.md"
    if not thread_path.exists():
        return False

    thread_meta, _body = parse_frontmatter(thread_path.read_text(encoding="utf-8"))
    if thread_meta.get("status") == "closed":
        return False

    attempts = meta.get("attempts", "")
    append_thread_message(
        thread_path,
        task_id=task_id,
        project=project,
        inbox_name=inbox_path.name,
        author_slug="daemon",
        message=f"Task closed: status={status}. attempts={attempts}.",
        now=now,
        close_thread=True,
    )
    log(f"THREAD action=closed task_id={task_id} status={status} thread={thread_path}", log_file)
    return True


def process_thread(
    thread_path: Path,
    project: str,
    *,
    now: datetime | None = None,
    events_file: Path = DEFAULT_EVENTS_FILE,
    state_file: Path = DEFAULT_STATE_FILE,
    log_file: Path = DEFAULT_LOG_FILE,
    max_attempts: int | None = None,
    adapter_mode: str | None = None,
    adapter_runner=subprocess.run,
) -> dict[str, Any]:
    now = now or utc_now()
    max_attempts = max_attempts or max_attempts_from_env()

    try:
        text = thread_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"action": "missing", "path": str(thread_path)}

    meta, body = parse_frontmatter(text)
    if meta.get("status") == "closed":
        return {"action": "ignored", "reason": "closed"}

    message = latest_thread_message(body)
    if not message:
        return {"action": "ignored", "reason": "no-message"}

    author_slug = message["author_slug"]
    targets = [slug for slug in find_mentions(message["content"]) if slug != author_slug]
    if not targets:
        return {"action": "ignored", "reason": "no-mentions"}

    task_id = meta.get("thread") or thread_id_from_path(thread_path)
    inbox_name = meta.get("inbox", "")
    collab_root = collab_root_for_path(thread_path)
    project_root = project_root_for_path(thread_path)
    inbox_path = str(collab_root / inbox_name) if inbox_name else ""
    msg_hash = message_hash(message)
    timestamp = isoformat_z(now)
    state = load_json(state_file, {})
    if not isinstance(state, dict):
        state = {}

    results = []
    for target_slug in targets:
        if not project_agent_known(project_root, target_slug):
            results.append({"target_slug": target_slug, "action": "skipped", "reason": "agent-not-in-project"})
            log(
                f"THREAD action=skipped task_id={task_id} target={target_slug} "
                f"reason=agent-not-in-project project={project_root} thread={thread_path}",
                log_file,
            )
            continue

        state_key = f"thread:{task_id}:{msg_hash}:{target_slug}"
        entry = state.get(state_key, {})
        if entry is True:
            entry = {"seen": True, "attempts": 0}
        if not isinstance(entry, dict):
            entry = {}

        attempts = coerce_int(str(entry.get("attempts", "0")), 0)
        last_attempt = parse_iso(str(entry.get("last_attempt", "")))
        if entry.get("done") or entry.get("seen"):
            results.append({"target_slug": target_slug, "action": "deduped"})
            continue
        if attempts >= max_attempts:
            results.append({"target_slug": target_slug, "action": "failed", "attempts": attempts})
            continue

        wait_seconds = backoff_for_attempts(attempts)
        if last_attempt and wait_seconds:
            elapsed = (now - last_attempt).total_seconds()
            if elapsed < wait_seconds:
                results.append(
                    {
                        "target_slug": target_slug,
                        "action": "backoff",
                        "attempts": attempts,
                        "wait_seconds": wait_seconds,
                    }
                )
                continue

        event = {
            "task_id": task_id,
            "project": project,
            "project_path": str(project_root),
            "target_slug": target_slug,
            "source_type": "thread",
            "source_path": str(thread_path),
            "thread_path": str(thread_path),
            "inbox_path": inbox_path,
            "reason": "thread-mention",
            "message_hash": msg_hash,
            "timestamp": timestamp,
            "synthetic_prompt": (
                f"You were mentioned in {thread_path} by @{author_slug}. "
                "Read the latest thread message, respond or act if needed, and update your log."
            ),
        }
        adapter_result = dispatch_wake_event(
            event,
            events_file=events_file,
            adapter_mode=adapter_mode,
            adapter_runner=adapter_runner,
        )

        action = "notified"
        if adapter_result["status"] == "success":
            entry = {"done": True, "attempts": attempts, "last_attempt": timestamp}
            action = "dispatched"
        elif adapter_result["status"] == "degraded":
            entry = {"seen": True, "attempts": attempts, "last_attempt": timestamp}
        else:
            attempts += 1
            entry = {"attempts": attempts, "last_attempt": timestamp}
            action = "failed" if attempts >= max_attempts else "retryable"

        state[state_key] = entry
        results.append(
            {
                "target_slug": target_slug,
                "action": action,
                "attempts": attempts,
                "adapter_result": adapter_result,
            }
        )
        log(
            "THREAD "
            f"action={action} task_id={task_id} target={target_slug} "
            f"adapter={adapter_result['adapter_name']} adapter_status={adapter_result['status']} "
            f"thread={thread_path}",
            log_file,
        )

    if len(state) > MAX_EVENTS:
        state = dict(list(state.items())[-MAX_EVENTS:])
    write_json(state_file, state)

    return {"action": "thread-mentions", "task_id": task_id, "message_hash": msg_hash, "results": results}


def process_inbox(
    inbox_path: Path,
    project: str,
    *,
    now: datetime | None = None,
    events_file: Path = DEFAULT_EVENTS_FILE,
    state_file: Path = DEFAULT_STATE_FILE,
    log_file: Path = DEFAULT_LOG_FILE,
    max_attempts: int | None = None,
    adapter_mode: str | None = None,
    adapter_runner=subprocess.run,
) -> dict[str, Any]:
    now = now or utc_now()
    max_attempts = max_attempts or max_attempts_from_env()

    try:
        text = inbox_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"action": "missing", "path": str(inbox_path)}

    meta, body = parse_frontmatter(text)
    if meta.get("status") in {"done", "failed"}:
        closed = close_thread_for_inbox(inbox_path, project, meta, now=now, log_file=log_file)
        return {"action": "closed-thread" if closed else "ignored", "reason": "status", "status": meta.get("status", "")}
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
        close_thread_for_inbox(inbox_path, project, meta, now=now, log_file=log_file)
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
        "project_path": str(inbox_path.parent.parent),
        "target_slug": target_slug,
        "source_type": "inbox",
        "source_path": str(inbox_path),
        "inbox_path": str(inbox_path),
        "reason": "unread-inbox",
        "attempt": next_attempts,
        "timestamp": timestamp,
        "synthetic_prompt": (
            f"You have an unread task in {inbox_path}. "
            "Read it, execute it, mark it status: done, and update your log."
        ),
    }
    adapter_result = dispatch_wake_event(
        event,
        events_file=events_file,
        adapter_mode=adapter_mode,
        adapter_runner=adapter_runner,
    )

    if adapter_result["status"] == "degraded":
        state[state_key] = timestamp
        if len(state) > MAX_EVENTS:
            state = dict(list(state.items())[-MAX_EVENTS:])
        write_json(state_file, state)
        log(
            "WAKE "
            f"action=notified task_id={task_id} target={target_slug} attempt={attempts} "
            f"adapter={adapter_result['adapter_name']} adapter_status=degraded inbox={inbox_path}",
            log_file,
        )
        return {
            "action": "notified",
            "task_id": task_id,
            "attempts": attempts,
            "event": event,
            "adapter_result": adapter_result,
        }

    if adapter_result["status"] == "success":
        current_meta, current_body = parse_frontmatter(inbox_path.read_text(encoding="utf-8"))
        if current_meta.get("status") and current_meta.get("status") != "unread":
            state[state_key] = timestamp
            if len(state) > MAX_EVENTS:
                state = dict(list(state.items())[-MAX_EVENTS:])
            write_json(state_file, state)
            if current_meta.get("status") in {"done", "failed"}:
                close_thread_for_inbox(inbox_path, project, current_meta, now=now, log_file=log_file)
            log(
                "WAKE "
                f"action=adapter-updated task_id={task_id} target={target_slug} attempt={attempts} "
                f"adapter={adapter_result['adapter_name']} adapter_status=success "
                f"inbox_status={current_meta.get('status')} inbox={inbox_path}",
                log_file,
            )
            return {
                "action": "adapter-updated",
                "task_id": task_id,
                "attempts": attempts,
                "event": event,
                "adapter_result": adapter_result,
                "inbox_status": current_meta.get("status", ""),
            }
        meta, body = current_meta, current_body

    meta["attempts"] = str(next_attempts)
    meta["last_attempt"] = timestamp
    if adapter_result["status"] == "success":
        meta["status"] = "claimed"
        meta["claimed_by"] = adapter_result["adapter_name"]
        meta["claimed_at"] = timestamp
        action = "claimed"
    elif next_attempts >= max_attempts:
        meta["status"] = "failed"
        meta["done_at"] = timestamp
        action = "failed"
    else:
        action = "event"
    update_inbox(inbox_path, meta, body)
    if meta.get("status") in {"done", "failed"}:
        close_thread_for_inbox(inbox_path, project, meta, now=now, log_file=log_file)

    state[state_key] = timestamp
    if len(state) > MAX_EVENTS:
        state = dict(list(state.items())[-MAX_EVENTS:])
    write_json(state_file, state)

    log(
        "WAKE "
        f"action={action} task_id={task_id} target={target_slug} attempt={next_attempts} "
        f"adapter={adapter_result['adapter_name']} adapter_status={adapter_result['status']} inbox={inbox_path}",
        log_file,
    )
    return {
        "action": action,
        "task_id": task_id,
        "attempts": next_attempts,
        "event": event,
        "adapter_result": adapter_result,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: ai-collab-wakeup.py <project> <inbox.md|thread.md>", file=sys.stderr)
        return 2

    project = argv[1]
    path = Path(argv[2]).expanduser().resolve()
    if is_thread_file(path):
        result = process_thread(path, project)
    else:
        result = process_inbox(path, project)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
