#!/usr/bin/env python3
"""
AI Collab live observer.

The daemon calls this for every project `.ai-collab/` directory. It writes a
machine-readable live view to `.ai-collab/live/` so the director can inspect
what each worker appears to be doing without asking the user for screenshots.

Screenshots are enabled by default and can be disabled with
AI_COLLAB_OBSERVER_SCREENSHOTS=0. Capture frequency and retention are bounded by
environment variables so the live directory does not grow without limit.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "ai-collab.live.v1"
SKIP_MD = {"PROTOCOL.md", "CONTEXT.md", "TEAM.md"}
LOG_RE = re.compile(r"^([a-z][a-z0-9_-]*)-\d{8}-\d{6}\.md$")
MENTION_RE = re.compile(r"(?<![\w.-])@([a-z][a-z0-9_-]{1,40})\b")
DEFAULT_ACTIVE_SECONDS = 300
DEFAULT_STALE_CLAIM_SECONDS = 1800
DEFAULT_SCREENSHOT_INTERVAL_SECONDS = 300
DEFAULT_MAX_EVENTS = 200
DEFAULT_MAX_COMMAND_LENGTH = 500
DEFAULT_MAX_SCREENSHOTS = 20

KNOWN_AGENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "claude-code": (r"\bclaude\b",),
    "opencode": (r"\bopencode\b",),
    "codex": (r"\bcodex\b",),
    "aider": (r"\baider\b",),
    "hermes": (r"\bhermes\b",),
    "kimi": (r"\bkimi(?:-cli)?\b",),
    "kilo": (r"\bkilo\b",),
    "cursor-native": (r"\bCursor(?:\.app)?\b",),
    "windsurf-native": (r"\bWindsurf(?:\.app)?\b",),
    "copilot-chat": (r"\bCode(?:\.app)?\b", r"\bVisual Studio Code\b"),
}


Runner = Callable[..., subprocess.CompletedProcess[str]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def truncate(value: str, limit: int = DEFAULT_MAX_COMMAND_LENGTH) -> str:
    clean = " ".join(value.replace("\x00", " ").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 15].rstrip() + "...[truncated]"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=False) + "\n")


def append_jsonl(path: Path, item: dict[str, Any], max_events: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if max_events is not None and path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        keep = max(0, max_events - 1)
        if keep:
            lines = lines[-keep:]
        else:
            lines = []
    lines.append(json.dumps(item, sort_keys=False))
    atomic_write(path, "\n".join(lines) + "\n")


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content
    end = None
    for idx, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = idx
            break
    if end is None:
        return {}, content
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1 :])


def extract_section(content: str, header: str) -> str:
    pattern = rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def section_lines(content: str, header: str) -> list[str]:
    text = extract_section(content, header)
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"-", "*"}:
            continue
        result.append(stripped)
    return result


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def file_mtime_iso(path: Path) -> str:
    return isoformat_z(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def parse_team_roster(team_md: Path) -> list[str]:
    try:
        text = team_md.read_text(encoding="utf-8")
    except OSError:
        return []
    agents: list[str] = []
    in_roster = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## roster"):
            in_roster = True
            continue
        if in_roster and stripped.startswith("##"):
            break
        if not in_roster or not stripped.startswith("- "):
            continue
        slug = stripped[2:].split()[0].strip("*`")
        if slug and slug not in agents:
            agents.append(slug)
    return agents


def discover_agents(collab_dir: Path) -> list[str]:
    agents: list[str] = []
    manifest = load_json(collab_dir / "agents.json", {})
    for item in manifest.get("agents", []) if isinstance(manifest, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("agent"), str):
            slug = item["agent"].strip()
            if slug and slug not in agents:
                agents.append(slug)
    for slug in parse_team_roster(collab_dir / "TEAM.md"):
        if slug not in agents:
            agents.append(slug)
    for inbox in collab_dir.glob("inbox-*.md"):
        slug = inbox.stem.removeprefix("inbox-")
        if slug and slug != "all" and slug not in agents:
            agents.append(slug)
    for log in collab_dir.glob("*.md"):
        if log.name in SKIP_MD or log.name.startswith("inbox-") or log.name.startswith("thread-"):
            continue
        match = LOG_RE.match(log.name)
        if match and match.group(1) not in agents:
            agents.append(match.group(1))
    return sorted(agents)


def latest_agent_log(collab_dir: Path, agent: str) -> Path | None:
    logs = [
        path
        for path in collab_dir.glob(f"{agent}-*.md")
        if path.name not in SKIP_MD and not path.name.startswith("inbox-") and not path.name.startswith("thread-")
    ]
    if not logs:
        return None
    return max(logs, key=lambda path: path.stat().st_mtime)


def read_latest_log(collab_dir: Path, agent: str) -> dict[str, Any]:
    path = latest_agent_log(collab_dir, agent)
    if not path:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(path), "error": "unreadable"}
    meta, body = parse_frontmatter(text)
    return {
        "path": str(path),
        "mtime": file_mtime_iso(path),
        "meta": meta,
        "working_on": extract_section(body, "Working On"),
        "files_read": section_lines(body, "Files Read This Session"),
        "files_modified": section_lines(body, "Files Modified This Session"),
        "decisions": section_lines(body, "Decisions Made"),
        "issues": section_lines(body, "Issues Identified"),
        "still_in_progress": section_lines(body, "Still In Progress"),
        "do_not_touch": section_lines(body, "Do Not Touch (Avoid Conflicts)"),
        "handoff": extract_section(body, "Handoff Note"),
    }


def read_inbox(collab_dir: Path, agent: str) -> dict[str, Any]:
    path = collab_dir / f"inbox-{agent}.md"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"path": str(path), "error": "unreadable"}
    meta, body = parse_frontmatter(text)
    return {
        "path": str(path),
        "mtime": file_mtime_iso(path),
        "meta": meta,
        "task_excerpt": truncate(extract_section(body, "Task") or body, 1000),
    }


def run_command(command: list[str], cwd: Path, runner: Runner = subprocess.run, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    return runner(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)


def parse_git_status(stdout: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        raw_path = line[3:] if len(line) > 3 else ""
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        files.append({"status": status.strip() or "modified", "path": raw_path})
    return files


def git_state(root: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    state: dict[str, Any] = {"is_git_repo": False, "dirty_files": [], "branch": "", "head": ""}
    try:
        top = run_command(["git", "rev-parse", "--show-toplevel"], root, runner=runner)
    except (OSError, subprocess.TimeoutExpired):
        return state
    if top.returncode != 0 or not top.stdout.strip():
        return state
    state["is_git_repo"] = True
    try:
        branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], root, runner=runner)
        if branch.returncode == 0:
            state["branch"] = branch.stdout.strip()
        head = run_command(["git", "rev-parse", "--short", "HEAD"], root, runner=runner)
        if head.returncode == 0:
            state["head"] = head.stdout.strip()
        status = run_command(["git", "status", "--porcelain=v1", "-uall"], root, runner=runner)
        if status.returncode == 0:
            state["dirty_files"] = parse_git_status(status.stdout)
    except (OSError, subprocess.TimeoutExpired):
        state["error"] = "git command failed"
    return state


def project_signals(root: Path) -> set[str]:
    root = root.resolve()
    normalized_path = re.sub(r"[^A-Za-z0-9]+", "_", str(root)).strip("_")
    normalized_name = re.sub(r"[^A-Za-z0-9]+", "_", root.name).strip("_")
    signals = {
        str(root),
        root.name,
        root.name.replace("-", "_"),
        root.name.replace("-", " "),
        normalized_path,
        f"file_{normalized_path}",
        normalized_name,
    }
    return {signal.lower() for signal in signals if signal}


def command_mentions_project(command: str, root: Path) -> bool:
    lowered = command.lower()
    return any(signal in lowered for signal in project_signals(root))


def get_json(url: str, *, timeout: int = 2) -> tuple[int, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(text)
            except json.JSONDecodeError:
                return response.status, text
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def path_matches_root(value: Any, root: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        candidate = Path(value).expanduser().resolve()
    except OSError:
        return False
    return candidate == root.resolve()


def opencode_process_matches_project(command: str, root: Path, getter=get_json) -> bool:
    match = re.search(r"(?:--port(?:=|\s+))(\d+)", command)
    if not match:
        return False
    port = int(match.group(1))
    if not (0 < port < 65536):
        return False
    status, body = getter(f"http://127.0.0.1:{port}/project/current", timeout=2)
    if status != 200 or not isinstance(body, dict):
        return False
    for key in ("worktree", "directory", "path", "root"):
        if path_matches_root(body.get(key), root):
            return True
    return False


def process_matches_project(agent: str, command: str, root: Path, getter=get_json) -> bool:
    if command_mentions_project(command, root):
        return True
    if agent == "opencode":
        return opencode_process_matches_project(command, root, getter=getter)
    return False


def classify_process(command: str, agents: list[str], root: Path, getter=get_json) -> str | None:
    if "ai-collab-observer.py" in command:
        return None
    for agent in agents:
        patterns = KNOWN_AGENT_PATTERNS.get(agent, (rf"\b{re.escape(agent)}\b",))
        if any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in patterns):
            if not process_matches_project(agent, command, root, getter=getter):
                continue
            return agent
    return None


def process_snapshot(
    root: Path,
    agents: list[str],
    runner: Runner = subprocess.run,
    getter=get_json,
) -> dict[str, list[dict[str, str]]]:
    by_agent = {agent: [] for agent in agents}
    try:
        completed = run_command(["ps", "-axo", "pid=,etime=,command="], root, runner=runner)
    except (OSError, subprocess.TimeoutExpired):
        return by_agent
    if completed.returncode != 0:
        return by_agent
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        pid, elapsed, command = parts
        agent = classify_process(command, agents, root, getter=getter)
        if not agent:
            continue
        by_agent.setdefault(agent, []).append(
            {"pid": pid, "elapsed": elapsed, "command": truncate(command)}
        )
    return by_agent


def read_agent_report(live_dir: Path, agent: str) -> dict[str, Any]:
    data = load_json(live_dir / f"{agent}.agent.json", {})
    return data if isinstance(data, dict) else {}


def latest_thread_mentions(collab_dir: Path, agent: str) -> list[dict[str, str]]:
    mentions: list[dict[str, str]] = []
    for thread in collab_dir.glob("thread-*.md"):
        try:
            text = thread.read_text(encoding="utf-8")
        except OSError:
            continue
        matches = MENTION_RE.findall(text)
        if agent not in matches:
            continue
        excerpt = ""
        for line in reversed(text.splitlines()):
            if f"@{agent}" in line:
                excerpt = truncate(line, 500)
                break
        mentions.append({"path": str(thread), "mtime": file_mtime_iso(thread), "excerpt": excerpt})
    mentions.sort(key=lambda item: item["mtime"], reverse=True)
    return mentions[:5]


def extract_locked_files(logs_by_agent: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    locks: dict[str, list[dict[str, str]]] = {}
    for agent, log in logs_by_agent.items():
        for line in log.get("do_not_touch", []):
            for match in re.findall(r"`([^`]+)`", line):
                locks.setdefault(match, []).append({"agent": agent, "reason": line})
            if "`" not in line:
                candidate = line.lstrip("-* ").split(" -- ", 1)[0].split(" - ", 1)[0].strip()
                if candidate and "/" in candidate:
                    locks.setdefault(candidate, []).append({"agent": agent, "reason": line})
    return locks


def claim_age_seconds(inbox: dict[str, Any], now: datetime) -> int | None:
    meta = inbox.get("meta") if isinstance(inbox, dict) else None
    if not isinstance(meta, dict):
        return None
    claimed_at = parse_iso(meta.get("claimed_at") or meta.get("last_attempt") or meta.get("updated"))
    if not claimed_at:
        return None
    return int(max(0, (now - claimed_at.astimezone(timezone.utc)).total_seconds()))


def is_report_recent(report: dict[str, Any], now: datetime, active_seconds: int) -> bool:
    updated = parse_iso(str(report.get("updated", "")))
    if not updated:
        return False
    return (now - updated.astimezone(timezone.utc)).total_seconds() <= active_seconds


def infer_status(
    *,
    inbox: dict[str, Any],
    latest_log: dict[str, Any],
    report: dict[str, Any],
    processes: list[dict[str, str]],
    now: datetime,
    active_seconds: int,
) -> str:
    inbox_status = str((inbox.get("meta") or {}).get("status", "")).strip().lower()
    phase = str(report.get("phase", "")).strip().lower()
    if inbox_status == "blocked":
        return "blocked"
    if phase in {"running", "command", "editing", "testing", "reviewing"} and is_report_recent(report, now, active_seconds):
        return "running"
    if processes:
        return "running"
    if inbox_status in {"claimed", "running"}:
        return "running"
    if inbox_status == "unread":
        return "waiting"
    if latest_log.get("mtime"):
        updated = parse_iso(latest_log.get("mtime"))
        if updated and (now - updated.astimezone(timezone.utc)).total_seconds() <= active_seconds:
            return "active"
        return "idle"
    if inbox_status == "done":
        return "done"
    return "unknown"


def screenshot_due(live_dir: Path, now: datetime, interval: int) -> bool:
    marker = live_dir / "screenshots" / ".last.json"
    data = load_json(marker, {})
    if not isinstance(data, dict):
        return True
    captured_at = parse_iso(str(data.get("captured_at", "")))
    if not captured_at:
        return True
    return (now - captured_at.astimezone(timezone.utc)).total_seconds() >= interval


def prune_screenshots(screenshots_dir: Path, max_keep: int) -> None:
    shots = sorted(screenshots_dir.glob("*.png"), key=lambda path: path.stat().st_mtime)
    for path in shots[:-max_keep]:
        try:
            path.unlink()
        except OSError:
            pass


def frontmost_rect(root: Path, runner: Runner) -> str | None:
    script = (
        'tell application "System Events" to tell (first process whose frontmost is true) '
        'to get {position, size} of front window'
    )
    try:
        completed = run_command(["osascript", "-e", script], root, runner=runner, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    nums = [int(value) for value in re.findall(r"-?\d+", completed.stdout)]
    if len(nums) < 4:
        return None
    x, y, width, height = nums[:4]
    if width <= 0 or height <= 0:
        return None
    return f"{x},{y},{width},{height}"


def parse_window_rows(text: str) -> list[dict[str, str]]:
    windows: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        app, title, rect = parts
        if re.match(r"^-?\d+,-?\d+,\d+,\d+$", rect):
            windows.append({"app": app, "title": title, "rect": rect})
    return windows


def project_window_rect(root: Path, runner: Runner) -> tuple[str | None, dict[str, str]]:
    script = r'''
tell application "System Events"
  set output to ""
  repeat with proc in (processes whose visible is true)
    try
      set procName to name of proc
      repeat with win in windows of proc
        try
          set winName to name of win
          set {x, y} to position of win
          set {w, h} to size of win
          if w > 0 and h > 0 then
            set output to output & procName & tab & winName & tab & x & "," & y & "," & w & "," & h & linefeed
          end if
        end try
      end repeat
    end try
  end repeat
  return output
end tell
'''
    try:
        completed = run_command(["osascript", "-e", script], root, runner=runner, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None, {}
    if completed.returncode != 0:
        return None, {}
    signals = project_signals(root)
    for window in parse_window_rows(completed.stdout):
        haystack = f"{window['app']} {window['title']}".lower()
        if any(signal in haystack for signal in signals):
            return window["rect"], window
    return None, {}


def maybe_capture_screenshot(
    root: Path,
    live_dir: Path,
    now: datetime,
    active_agents: list[str],
    *,
    runner: Runner = subprocess.run,
    system: str | None = None,
) -> dict[str, Any] | None:
    if not env_bool("AI_COLLAB_OBSERVER_SCREENSHOTS", True):
        return None
    if env_bool("AI_COLLAB_OBSERVER_SCREENSHOT_ACTIVE_ONLY", True) and not active_agents:
        return None
    interval = env_int("AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL", DEFAULT_SCREENSHOT_INTERVAL_SECONDS, 1)
    if not screenshot_due(live_dir, now, interval):
        return None
    current_system = system or platform.system()
    if current_system != "Darwin":
        screenshots_dir = live_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        marker = {
            "captured_at": isoformat_z(now),
            "path": "",
            "mode": "unsupported",
            "rect": "",
            "active_agents": active_agents,
            "status": "skipped",
        }
        write_json(screenshots_dir / ".last.json", marker)
        return {
            "status": "skipped",
            "reason": "screenshots are currently implemented with macOS screencapture only",
        }
    screenshots_dir = live_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("AI_COLLAB_OBSERVER_SCREENSHOT_MODE", "project").strip().lower()
    if mode not in {"project", "frontmost", "screen"}:
        mode = "project"
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    path = screenshots_dir / f"{timestamp}-{mode}.png"
    command = ["screencapture", "-x"]
    rect = None
    window: dict[str, str] = {}
    if mode == "project":
        rect, window = project_window_rect(root, runner)
        if not rect:
            marker = {
                "captured_at": isoformat_z(now),
                "path": "",
                "mode": mode,
                "rect": "",
                "active_agents": active_agents,
                "status": "skipped",
                "reason": f"no visible window matched project {root.name}",
            }
            write_json(screenshots_dir / ".last.json", marker)
            return marker
        command.extend(["-R", rect])
    if mode == "frontmost":
        rect = frontmost_rect(root, runner)
        if rect:
            command.extend(["-R", rect])
    command.append(str(path))
    try:
        completed = run_command(command, root, runner=runner, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "reason": f"screencapture failed: {exc}"}
    if completed.returncode != 0:
        reason = truncate((completed.stderr or completed.stdout or "screencapture failed").strip(), 500)
        return {"status": "failed", "reason": reason}
    if not path.exists():
        # Test runners may report success without creating the file.
        try:
            path.write_bytes(b"")
        except OSError:
            pass
    marker = {
        "captured_at": isoformat_z(now),
        "path": str(path),
        "mode": mode,
        "rect": rect or "",
        "window": window,
        "active_agents": active_agents,
    }
    write_json(screenshots_dir / ".last.json", marker)
    prune_screenshots(screenshots_dir, env_int("AI_COLLAB_OBSERVER_SCREENSHOT_MAX_KEEP", DEFAULT_MAX_SCREENSHOTS, 1))
    return {"status": "captured", **marker}


def build_alerts(
    *,
    agents: list[str],
    inboxes: dict[str, dict[str, Any]],
    logs_by_agent: dict[str, dict[str, Any]],
    git: dict[str, Any],
    now: datetime,
    stale_claim_seconds: int,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for agent in agents:
        inbox = inboxes.get(agent, {})
        status = str((inbox.get("meta") or {}).get("status", "")).lower()
        if status in {"claimed", "running"}:
            age = claim_age_seconds(inbox, now)
            if age is not None and age >= stale_claim_seconds:
                alerts.append(
                    {
                        "type": "stale-claim",
                        "agent": agent,
                        "severity": "warn",
                        "message": f"{agent} has held a {status} inbox for {age}s",
                        "inbox": inbox.get("path", ""),
                    }
                )
    locks = extract_locked_files(logs_by_agent)
    dirty_paths = {item["path"] for item in git.get("dirty_files", []) if isinstance(item, dict)}
    for path in sorted(dirty_paths):
        for lock in locks.get(path, []):
            alerts.append(
                {
                    "type": "dirty-locked-file",
                    "agent": lock["agent"],
                    "severity": "warn",
                    "message": f"{path} is dirty and listed as Do Not Touch by {lock['agent']}",
                    "path": path,
                    "reason": lock["reason"],
                }
            )
    return alerts


def event_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if not previous:
        changes.append({"type": "snapshot-created"})
        return changes
    for key in ("status", "current_task_id"):
        if previous.get(key) != current.get(key):
            changes.append({"type": f"{key}-changed", "from": previous.get(key), "to": current.get(key)})
    previous_log = (previous.get("latest_log") or {}).get("path")
    current_log = (current.get("latest_log") or {}).get("path")
    if previous_log != current_log:
        changes.append({"type": "latest-log-changed", "from": previous_log, "to": current_log})
    prev_dirty = sorted(item.get("path", "") for item in (previous.get("git") or {}).get("dirty_files", []))
    curr_dirty = sorted(item.get("path", "") for item in (current.get("git") or {}).get("dirty_files", []))
    if prev_dirty != curr_dirty:
        changes.append({"type": "dirty-files-changed", "from": prev_dirty, "to": curr_dirty})
    prev_pids = sorted(item.get("pid", "") for item in previous.get("processes", []))
    curr_pids = sorted(item.get("pid", "") for item in current.get("processes", []))
    if prev_pids != curr_pids:
        changes.append({"type": "processes-changed", "from": prev_pids, "to": curr_pids})
    return changes


def observe_project(
    collab_dir: Path,
    *,
    now: datetime | None = None,
    runner: Runner = subprocess.run,
    system: str | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    collab_dir = collab_dir.resolve()
    root = collab_dir.parent if collab_dir.name == ".ai-collab" else collab_dir
    if collab_dir.name != ".ai-collab":
        collab_dir = root / ".ai-collab"
    live_dir = collab_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    agents = discover_agents(collab_dir)
    active_seconds = env_int("AI_COLLAB_OBSERVER_ACTIVE_SECONDS", DEFAULT_ACTIVE_SECONDS, 1)
    stale_claim_seconds = env_int("AI_COLLAB_OBSERVER_STALE_CLAIM_SECONDS", DEFAULT_STALE_CLAIM_SECONDS, 60)
    max_events = env_int("AI_COLLAB_OBSERVER_MAX_EVENTS", DEFAULT_MAX_EVENTS, 1)

    git = git_state(root, runner=runner)
    processes_by_agent = process_snapshot(root, agents, runner=runner)
    logs_by_agent = {agent: read_latest_log(collab_dir, agent) for agent in agents}
    inboxes = {agent: read_inbox(collab_dir, agent) for agent in agents}
    alerts = build_alerts(
        agents=agents,
        inboxes=inboxes,
        logs_by_agent=logs_by_agent,
        git=git,
        now=now,
        stale_claim_seconds=stale_claim_seconds,
    )

    snapshots: dict[str, dict[str, Any]] = {}
    active_agents: list[str] = []
    for agent in agents:
        report = read_agent_report(live_dir, agent)
        inbox = inboxes.get(agent, {})
        latest_log = logs_by_agent.get(agent, {})
        processes = processes_by_agent.get(agent, [])
        status = infer_status(
            inbox=inbox,
            latest_log=latest_log,
            report=report,
            processes=processes,
            now=now,
            active_seconds=active_seconds,
        )
        if status in {"running", "active", "waiting", "blocked"}:
            active_agents.append(agent)
        inbox_meta = inbox.get("meta") or {}
        snapshot = {
            "schema": SCHEMA_VERSION,
            "project": root.name,
            "project_path": str(root),
            "agent": agent,
            "updated": isoformat_z(now),
            "status": status,
            "current_task_id": inbox_meta.get("task_id") or report.get("task_id") or "",
            "current_command": report.get("current_command") or (processes[0]["command"] if processes else ""),
            "phase": report.get("phase", ""),
            "reported": report,
            "reported_events": read_jsonl_tail(live_dir / f"{agent}.agent.events.jsonl", 10),
            "inbox": inbox,
            "latest_log": latest_log,
            "thread_mentions": latest_thread_mentions(collab_dir, agent),
            "processes": processes,
            "git": git,
            "alerts": [alert for alert in alerts if alert.get("agent") == agent],
        }
        previous = load_json(live_dir / f"{agent}.json", {})
        write_json(live_dir / f"{agent}.json", snapshot)
        for change in event_changes(previous if isinstance(previous, dict) else {}, snapshot):
            append_jsonl(
                live_dir / f"{agent}.events.jsonl",
                {"timestamp": isoformat_z(now), "agent": agent, **change},
                max_events=max_events,
            )
        snapshots[agent] = snapshot

    screenshot = maybe_capture_screenshot(root, live_dir, now, active_agents, runner=runner, system=system)
    if screenshot:
        for agent in active_agents:
            snapshots[agent]["screenshot"] = screenshot
            write_json(live_dir / f"{agent}.json", snapshots[agent])
            append_jsonl(
                live_dir / f"{agent}.events.jsonl",
                {"timestamp": isoformat_z(now), "agent": agent, "type": "screenshot", "screenshot": screenshot},
                max_events=max_events,
            )

    for alert in alerts:
        append_jsonl(live_dir / "director-alerts.jsonl", {"timestamp": isoformat_z(now), **alert}, max_events=max_events)

    summary = {
        "schema": SCHEMA_VERSION,
        "project": root.name,
        "project_path": str(root),
        "updated": isoformat_z(now),
        "agents": [
            {
                "agent": agent,
                "status": snapshots[agent]["status"],
                "current_task_id": snapshots[agent]["current_task_id"],
                "current_command": snapshots[agent]["current_command"],
                "alerts": len(snapshots[agent]["alerts"]),
            }
            for agent in agents
        ],
        "active_agents": active_agents,
        "git": git,
        "alerts": alerts,
        "screenshot": screenshot or {},
    }
    write_json(live_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("Usage: ai-collab-observer.py <project-root|.ai-collab-dir>", file=sys.stderr)
        return 2
    target = Path(argv[0]).resolve()
    collab_dir = target if target.name == ".ai-collab" else target / ".ai-collab"
    if not collab_dir.exists():
        print(f"No .ai-collab directory found at {collab_dir}", file=sys.stderr)
        return 1
    if not env_bool("AI_COLLAB_OBSERVER", True):
        return 0
    summary = observe_project(collab_dir)
    print(
        f"observed project={summary['project']} agents={len(summary['agents'])} "
        f"active={len(summary['active_agents'])} alerts={len(summary['alerts'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
