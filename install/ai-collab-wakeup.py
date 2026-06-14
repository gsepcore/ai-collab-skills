#!/usr/bin/env python3
"""
Durable inbox wakeup detection for ai-collab.

Turns unread inbox files into durable wake events, then dispatches a wakeup
adapter. CLI execution is opt-in; the default adapter is notify-only.
"""
from __future__ import annotations

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
import urllib.request
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
DEFAULT_CLI_TARGETS = ("codex", "opencode", "claude")
DEFAULT_VISIBLE_TARGETS = ("codex", "opencode")
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
    ".antigravity/extensions/*/bin/*",
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
        "antigravity": ["antigravity"],
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
            for directory in Path.home().glob(pattern):
                fallback = directory / candidate
                if fallback.exists() and os.access(fallback, os.X_OK):
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
    if target == "claude":
        return [exe, "-p", "--permission-mode", "acceptEdits", "--add-dir", project_path, prompt]
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


def post_json(url: str, payload: dict[str, Any], *, timeout: int) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
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


def opencode_port_project(port: int, *, timeout: int, getter=None) -> str | None:
    getter = getter or get_json
    status, body = getter(f"http://127.0.0.1:{port}/project/current", timeout=timeout)
    if not (200 <= status < 300) or not isinstance(body, dict):
        return None
    worktree = body.get("worktree")
    return worktree if isinstance(worktree, str) else None


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
    # Prefer ports whose /project/current matches the inbox's project_path so
    # the synthetic prompt lands in the user's visible tab, not a tab open on
    # another project.
    project_ports: list[int] = []
    other_ports: list[int] = []
    for port in ports:
        port_project = opencode_port_project(port, timeout=fast_timeout, getter=getter)
        if project_path and port_project:
            try:
                same = Path(port_project).resolve() == Path(project_path).resolve()
            except OSError:
                same = port_project == project_path
            if same:
                project_ports.append(port)
                continue
        other_ports.append(port)
    # Hardening (per Cody's review 2026-05-13): once any port reports a
    # matching project, never fall back to non-matching ports — that path
    # could re-introduce cross-project delivery. Only use other_ports when
    # no port can be confirmed for the target project at all.
    ordered_ports = project_ports if project_ports else other_ports

    for port in ordered_ports:
        session_id = discover_opencode_active_session(
            port,
            timeout=fast_timeout,
            getter=getter,
            project_path=project_path if port in project_ports else None,
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
        "message": last_error or "visible OpenCode TUI did not accept synthetic prompt",
        "adapter_name": "opencode-visible",
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


def build_codex_acp_messages(input_data: dict[str, str]) -> list[dict[str, Any]]:
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


def run_visible_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    target = input_data["target_slug"]
    if target == "opencode":
        return run_opencode_visible_adapter(input_data, timeout=timeout, runner=runner)
    if target in {"codex", "antigravity"}:
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    return {
        "status": "failed",
        "message": f"visible adapter has no implementation for target {target}",
        "adapter_name": "visible",
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
    if mode == "antigravity-chat":
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "codex-acp":
        return run_codex_acp_adapter(input_data, timeout=timeout)
    if mode != "cli":
        return {"status": "failed", "message": f"unknown adapter mode: {mode}", "adapter_name": mode}

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
    inbox_path = str(thread_path.parent / inbox_name) if inbox_name else ""
    msg_hash = message_hash(message)
    timestamp = isoformat_z(now)
    state = load_json(state_file, {})
    if not isinstance(state, dict):
        state = {}

    results = []
    for target_slug in targets:
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
            "project_path": str(thread_path.parent.parent),
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
    path = Path(argv[2])
    if path.name.startswith("thread-"):
        result = process_thread(path, project)
    else:
        result = process_inbox(path, project)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
