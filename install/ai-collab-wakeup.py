#!/usr/bin/env python3
"""
Durable inbox wakeup detection for ai-collab.

Turns unread inbox files into durable wake events, then dispatches the exact
visible fallback when the internal-first wake policy requires it.
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
DEFAULT_NOTIFICATIONS_FILE = Path.home() / ".ai-collab-notifications.json"
DEFAULT_ADAPTER = "visible"
DEFAULT_ADAPTER_TIMEOUT_SECONDS = 120
DEFAULT_REPLY_WAIT_SECONDS = 90
DEFAULT_CLI_TARGETS = ("codex", "opencode", "claude", "claude-code", "hermes", "kimi", "kilo")
DEFAULT_VISIBLE_TARGETS = (
    "codex", "opencode", "claude", "claude-code", "claude-code-ide", "aider", "kilo", "hermes", "kimi",
    "cursor-native", "windsurf-native", "copilot-chat", "generic",
)
DEFAULT_IDE_BRIDGE_DIR = Path.home() / ".ai-collab" / "ide-bridges"
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


def thread_stale_days() -> int:
    # Inboxes already stop retrying via max_attempts/backoff. Discussion/task
    # threads had no such cap: an unanswered @mention in a thread nobody
    # ever formally closed gets rescanned and re-escalated to visible chat
    # forever. Skip threads whose last activity is this old instead of
    # requiring every stale test/onboarding thread to be closed by hand.
    # 0 disables the cutoff.
    return max(0, coerce_int(os.environ.get("AI_COLLAB_THREAD_STALE_DAYS"), 7))


def reply_wait_seconds_from_env() -> int:
    # A successful visible dispatch only proves the prompt reached the
    # target's window, not that the agent read and answered it. Wait this
    # long for a real thread reply from the target before treating the
    # delivery as a non-response (RESUMEN DE EJECUCION, discussion-20260817-214951).
    return max(0, coerce_int(os.environ.get("AI_COLLAB_REPLY_WAIT_SECONDS"), DEFAULT_REPLY_WAIT_SECONDS))


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

    agents_path = collab / "agents.json"
    agents = load_json(agents_path, {})
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

        # A valid v2 manifest is the authoritative active roster. Historical
        # TEAM entries, inboxes, and session logs must never resurrect an
        # agent that was deliberately removed from this project.
        if agents_path.exists() and isinstance(roster, list):
            return False

    # Legacy projects without a usable agents.json keep the old discovery
    # fallbacks until they are migrated.
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


def capability_for(project_root: Path, target_slug: str) -> dict[str, Any]:
    payload = load_json(project_root / ".ai-collab" / "capabilities.json", {})
    rows = payload.get("agents") if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("agent", "")).strip().lower() == target_slug.lower():
            return row
    return {}


def agent_is_active(project_root: Path, target_slug: str, now: datetime, threshold_seconds: int) -> bool:
    live = project_root / ".ai-collab" / "live"
    # The per-turn session registration file (written every preflight by
    # ai-collab-turn.py) is the most direct "is a human actually driving this
    # agent right now" signal -- check it first, keyed on heartbeat_at/status
    # rather than the task-phase vocabulary used by the other two files.
    session_state = load_json(live / "sessions" / f"current-{target_slug}.json", {})
    if isinstance(session_state, dict):
        heartbeat = parse_iso(str(session_state.get("heartbeat_at") or session_state.get("started") or ""))
        if (
            heartbeat
            and (now - heartbeat).total_seconds() <= threshold_seconds
            and str(session_state.get("status") or "").lower() == "active"
        ):
            return True
    for path in (live / f"{target_slug}.agent.json", live / f"{target_slug}.json"):
        state = load_json(path, {})
        if not isinstance(state, dict):
            continue
        updated = parse_iso(str(state.get("updated") or ""))
        phase = str(state.get("phase") or state.get("status") or "").lower()
        if updated and (now - updated).total_seconds() <= threshold_seconds:
            if phase in {"command", "editing", "running", "working", "responding", "claimed"}:
                return True
    return False


def internal_grace_seconds(project_root: Path, target_slug: str, now: datetime) -> int:
    if truthy_env("AI_COLLAB_FORCE_VISIBLE"):
        return 0
    capability = capability_for(project_root, target_slug)
    if not capability:
        return 0
    policy = capability.get("wake_policy") if isinstance(capability, dict) else {}
    delivery = capability.get("delivery") if isinstance(capability, dict) else {}
    if target_slug == "codex" or (isinstance(delivery, dict) and delivery.get("primary") == "visible-chat"):
        return 0
    visible = capability.get("visible") if isinstance(capability, dict) else {}
    try:
        grace = max(
            0,
            int(
                os.environ.get(
                    "AI_COLLAB_INTERNAL_GRACE_SECONDS",
                    str((policy or {}).get("internal_grace_seconds", 15)),
                )
            ),
        )
    except (TypeError, ValueError):
        grace = 15
    try:
        threshold = max(
            1,
            int(
                os.environ.get(
                    "AI_COLLAB_DIRECTOR_SLEEP_SECONDS",
                    str((policy or {}).get("sleep_threshold_seconds", 60)),
                )
            ),
        )
    except (TypeError, ValueError):
        threshold = 60
    if isinstance(visible, dict) and visible.get("native_chat_only"):
        if not agent_is_active(project_root, target_slug, now, threshold):
            return 0
    # An agent with its own per-prompt hook (e.g. Claude Code's
    # UserPromptSubmit) already surfaces pending mentions passively on its
    # next turn preflight -- no terminal injection needed while it is
    # verifiably alive. capabilities.json opts an agent into this by setting
    # visible.required_when_internal_timeout=false (it still keeps
    # required_when_sleeping=true, so a stale/inactive session falls back to
    # the normal short grace period and gets nudged visibly as before).
    try:
        active_session_threshold = max(
            1, int(os.environ.get("AI_COLLAB_ACTIVE_SESSION_THRESHOLD_SECONDS", "900"))
        )
    except (TypeError, ValueError):
        active_session_threshold = 900
    if (
        isinstance(visible, dict)
        and visible.get("required_when_sleeping")
        and visible.get("required_when_internal_timeout") is False
        and agent_is_active(project_root, target_slug, now, active_session_threshold)
    ):
        try:
            active_grace = max(
                grace,
                int(os.environ.get("AI_COLLAB_ACTIVE_GRACE_SECONDS", "1200")),
            )
        except (TypeError, ValueError):
            active_grace = max(grace, 1200)
        return active_grace
    return grace


def emit_escalation_notice(project_root: Path, targets: list[str], source: Path, grace_seconds: int, now: datetime) -> None:
    if truthy_env("AI_COLLAB_ESCALATION_NOTIFIED"):
        return
    message = (
        f"No internal response from {', '.join(targets)} after {grace_seconds}s; "
        "AI Collab is proceeding to the exact visible chat now."
    )
    notice = {
        "ai": "AI Collab delivery supervisor",
        "project": project_root.name,
        "file": source.name,
        "message": message,
        "timestamp": isoformat_z(now),
        "type": "visible-escalation",
        "targets": targets,
    }
    notification_path = Path(os.environ.get("AI_COLLAB_NOTIFICATIONS_FILE", str(DEFAULT_NOTIFICATIONS_FILE))).expanduser()
    lock_file = with_lock(notification_path)
    try:
        rows = load_json(notification_path, [])
        if not isinstance(rows, list):
            rows = []
        rows.append(notice)
        write_json(notification_path, rows[-50:])
    finally:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
    events = project_root / ".ai-collab" / "live" / "delivery-escalations.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(notice, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Daemon safety-net for peer review awareness (point 1 of RESUMEN DE EJECUCION
# in discussion-20260820-113730).  When an agent completes non-trivial work but
# forgets to fire a review request to related-role owners, the daemon detects
# this after a configurable delay and dispatches the review wake automatically.
# ---------------------------------------------------------------------------

DEFAULT_REVIEW_SAFETY_NET_SECONDS = 30
TRIVIAL_CONFIG_PATTERNS = frozenset({"team.md"})
# capabilities.json/roles.json govern every agent's routing and wake
# behavior -- a change to either is always non-trivial regardless of file
# count (RESUMEN DE EJECUCION discussion-20260820-113730). An earlier
# revision of this file grouped them with genuinely trivial config (like
# TEAM.md) by mistake, which made scan_missing_reviews silently skip review
# for the two files that most need one; caught in review before sync.
HIGH_IMPACT_BASENAMES = frozenset({"capabilities.json", "roles.json"})
TRIVIAL_DIR_PREFIXES = ("install/",)


def load_roles(project_root: Path) -> dict[str, Any]:
    """Load .ai-collab/roles.json from *project_root*.

    Returns the ``assignments`` dict mapping role names to their metadata
    (including ``primary``, ``related_roles``, etc.) or an empty dict on any
    read/parse error.
    """
    collab = project_root / ".ai-collab"
    data = load_json(collab / "roles.json", {})
    if not isinstance(data, dict):
        return {}
    return data.get("assignments", {}) if isinstance(data.get("assignments"), dict) else {}


def agent_roles_for(assignments: dict[str, Any], agent_slug: str) -> list[str]:
    """Return the list of role names where *agent_slug* is the ``primary`` owner."""
    roles: list[str] = []
    for role_name, meta in assignments.items():
        if isinstance(meta, dict) and str(meta.get("primary", "")).lower() == agent_slug.lower():
            roles.append(role_name)
    return roles


def related_role_owners(assignments: dict[str, Any], agent_roles: list[str]) -> dict[str, str]:
    """Map ``related_role -> primary_owner`` for all roles related to *agent_roles*.

    Excludes roles owned by the same agent (an agent should not review their own
    work) and roles with no ``primary``.
    """
    self_owners = set()
    for role_name in agent_roles:
        meta = assignments.get(role_name)
        if isinstance(meta, dict):
            owner = str(meta.get("primary", "")).lower()
            if owner:
                self_owners.add(owner)
    result: dict[str, str] = {}
    for role_name in agent_roles:
        meta = assignments.get(role_name)
        if not isinstance(meta, dict):
            continue
        for related in meta.get("related_roles", []):
            if not isinstance(related, str):
                continue
            related_meta = assignments.get(related, {})
            if not isinstance(related_meta, dict):
                continue
            owner = str(related_meta.get("primary", "")).lower()
            if owner and owner not in self_owners:
                result[related] = owner
    return result


def is_trivial_task(files_in_scope: list[str]) -> bool:
    """Return True if the completed task is considered trivial (no review needed).

    A task is trivial when:
    - ``files_in_scope`` is empty or has exactly one file **and** that file is
      a known config-only file (e.g. session logs, CONTEXT.md) **and** it is
      not inside ``install/`` or other high-impact directories.
    """
    if len(files_in_scope) == 0:
        return True
    if any(Path(f).name.lower() in HIGH_IMPACT_BASENAMES for f in files_in_scope):
        return False
    if len(files_in_scope) > 1:
        return False
    # Single file — check if it is in a trivial location
    single = files_in_scope[0]
    basename = Path(single).name.lower()
    if basename in TRIVIAL_CONFIG_PATTERNS:
        return True
    for prefix in TRIVIAL_DIR_PREFIXES:
        if single.startswith(prefix) or f"/{prefix}" in single:
            return False
    return True


def has_review_request(project_root: Path, agent_slug: str, task_key: str, state_file: Path | None = None) -> bool:
    """Check whether a review request was already sent for this agent + task.

    Looks in both the dedup state file and the agent's session log to avoid
    duplicate dispatches.
    """
    if state_file is None:
        state_file = Path(os.environ.get("AI_COLLAB_WAKEUP_STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser()
    state = load_json(state_file, {})
    if isinstance(state, dict):
        review_key = f"review:{agent_slug}:{task_key}"
        entry = state.get(review_key)
        if isinstance(entry, dict) and (entry.get("dispatched") or entry.get("seen")):
            return True
        if isinstance(entry, str):
            return True
    return False


def mark_review_dispatched(
    project_root: Path,
    agent_slug: str,
    task_key: str,
    state_file: Path | None = None,
) -> None:
    """Record that a review wake was dispatched so it is not repeated."""
    if state_file is None:
        state_file = Path(os.environ.get("AI_COLLAB_WAKEUP_STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser()
    state = load_json(state_file, {})
    if not isinstance(state, dict):
        state = {}
    review_key = f"review:{agent_slug}:{task_key}"
    state[review_key] = {"dispatched": True, "dispatched_at": isoformat_z(utc_now())}
    # Cap state size
    if len(state) > MAX_EVENTS:
        keys = list(state.keys())
        for old_key in keys[: len(state) - MAX_EVENTS]:
            del state[old_key]
    write_json(state_file, state)


def scan_missing_reviews(
    project_root: Path,
    *,
    now: datetime | None = None,
    safety_net_seconds: int | None = None,
    adapter_mode: str | None = None,
    adapter_runner=subprocess.run,
    events_file: Path | None = None,
    state_file: Path | None = None,
    log_file: Path = DEFAULT_LOG_FILE,
) -> list[dict[str, Any]]:
    """Scan all registered agents for completed non-trivial work missing a review.

    For each agent whose live phase is ``done`` for at least
    *safety_net_seconds* (default: ``AI_COLLAB_REVIEW_SAFETY_NET_SECONDS``
    or 30), determine whether the task was non-trivial and whether a review
    request was already dispatched.  If not, dispatch a wake to the owner of
    each related role.

    Returns a list of action dicts describing what was (or was not) done.
    """
    if now is None:
        now = utc_now()
    if safety_net_seconds is None:
        safety_net_seconds = max(
            0,
            coerce_int(
                os.environ.get("AI_COLLAB_REVIEW_SAFETY_NET_SECONDS"),
                DEFAULT_REVIEW_SAFETY_NET_SECONDS,
            ),
        )
    if events_file is None:
        events_file = Path(os.environ.get("AI_COLLAB_WAKEUP_EVENTS_FILE", str(DEFAULT_EVENTS_FILE))).expanduser()

    assignments = load_roles(project_root)
    if not assignments:
        return []

    live = project_root / ".ai-collab" / "live"
    collab = project_root / ".ai-collab"
    agents = load_json(collab / "agents.json", {})
    roster: list[str] = []
    if isinstance(agents, dict):
        raw = agents.get("agents", agents.get("roster", []))
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    roster.append(item.lower())
                elif isinstance(item, dict):
                    slug = str(item.get("agent") or item.get("slug") or "").lower()
                    if slug:
                        roster.append(slug)

    results: list[dict[str, Any]] = []
    for agent_slug in roster:
        agent_roles = agent_roles_for(assignments, agent_slug)
        if not agent_roles:
            continue

        # Read live state to check if agent just finished something
        live_state: dict[str, Any] = {}
        for path in (live / f"{agent_slug}.agent.json", live / f"{agent_slug}.json"):
            loaded = load_json(path, {})
            if isinstance(loaded, dict) and loaded:
                live_state = loaded
                break

        if not live_state:
            continue

        phase = str(live_state.get("phase") or live_state.get("status") or "").lower()
        if phase not in {"done", "idle"}:
            continue

        updated = parse_iso(str(live_state.get("updated") or ""))
        if not updated:
            continue
        elapsed = (now - updated).total_seconds()
        if elapsed < safety_net_seconds:
            continue

        files_in_scope = live_state.get("files_in_scope", [])
        if not isinstance(files_in_scope, list):
            files_in_scope = []
        if is_trivial_task(files_in_scope):
            results.append({"agent_slug": agent_slug, "action": "skipped", "reason": "trivial-task"})
            continue

        # Build a dedup key from agent + timestamp of completion
        task_key = f"{agent_slug}:{isoformat_z(updated)}"

        if has_review_request(project_root, agent_slug, task_key, state_file=state_file):
            results.append({"agent_slug": agent_slug, "action": "deduped", "reason": "review-already-sent"})
            continue

        # Find related role owners to notify
        owners = related_role_owners(assignments, agent_roles)
        if not owners:
            results.append({"agent_slug": agent_slug, "action": "skipped", "reason": "no-related-roles"})
            continue

        summary = live_state.get("summary", "unknown task")
        file_list = ", ".join(str(f) for f in files_in_scope[:5])
        dispatched: list[dict[str, str]] = []
        for related_role, owner_slug in owners.items():
            synthetic_prompt = (
                f"@{owner_slug} cerro trabajo no trivial (archivos: {file_list}) "
                f"relacionado con tu rol {related_role}. Revisalo y comenta en su "
                f"hilo/log si corresponde. Contexto: {summary}"
            )
            wake_event = {
                "task_id": task_key,
                "project_path": str(project_root),
                "target_slug": owner_slug,
                "inbox_path": "",
                "source_path": str(live / f"{agent_slug}.agent.json"),
                "source_type": "daemon-review-safety-net",
                "thread_path": "",
                "synthetic_prompt": synthetic_prompt,
            }
            try:
                adapter_result = dispatch_wake_event(
                    wake_event,
                    events_file=events_file,
                    adapter_mode=adapter_mode,
                    adapter_runner=adapter_runner,
                )
                dispatched.append({"target_slug": owner_slug, "related_role": related_role, "result": adapter_result})
            except Exception as exc:
                dispatched.append({"target_slug": owner_slug, "related_role": related_role, "error": str(exc)})

        mark_review_dispatched(project_root, agent_slug, task_key, state_file=state_file)
        results.append({
            "agent_slug": agent_slug,
            "action": "review-dispatched",
            "files_in_scope": files_in_scope,
            "dispatched": dispatched,
        })
        log(
            f"REVIEW_SAFETY_NET agent={agent_slug} files={file_list} "
            f"dispatched_to={[d['target_slug'] for d in dispatched]} "
            f"project={project_root}",
            log_file,
        )

    return results


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
        # A wakeup-triggered turn runs with nobody watching. Default to a
        # read-only permission mode so an injected prompt can never edit
        # files on its own -- only an explicit env override widens this.
        permission_mode = os.environ.get("AI_COLLAB_CLAUDE_CLI_PERMISSION_MODE", "plan")
        return [exe, "-p", "--permission-mode", permission_mode, "--add-dir", project_path, prompt]
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


def ide_bridge_candidates(
    project_path: str,
    *,
    registry_dir: Path = DEFAULT_IDE_BRIDGE_DIR,
) -> list[dict[str, Any]]:
    """Return live IDE bridges whose workspace exactly matches project_path."""
    result: list[dict[str, Any]] = []
    try:
        entries = sorted(registry_dir.glob("*.json"))
    except OSError:
        return result
    for path in entries:
        item = load_json(path, {})
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port") or 0)
            pid = int(item.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        token = str(item.get("token") or "")
        projects = item.get("project_paths") or []
        if not (0 < port < 65536 and pid > 0 and token and isinstance(projects, list)):
            continue
        if not any(same_path(project_path, str(candidate)) for candidate in projects):
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        result.append({**item, "registry_path": str(path), "port": port, "pid": pid, "token": token})
    return result


def run_ide_terminal_visible_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    poster=None,
    registry_dir: Path = DEFAULT_IDE_BRIDGE_DIR,
) -> dict[str, Any]:
    """Submit a prompt to the exact visible integrated terminal for this project."""
    poster = poster or post_json
    guardrail = visible_guardrail(input_data, "ide-terminal-visible")
    if guardrail:
        return guardrail
    target = input_data["target_slug"]
    candidates = ide_bridge_candidates(input_data["project_path"], registry_dir=registry_dir)
    if not candidates:
        return {
            "status": "failed",
            "message": (
                "no live AI Collab IDE bridge matched this project; install/enable "
                "gsepcore.ai-collab-visible-bridge and keep the project window open"
            ),
            "adapter_name": "ide-terminal-visible",
        }
    if len(candidates) > 1:
        details = ", ".join(f"pid={item['pid']} port={item['port']}" for item in candidates)
        return {
            "status": "failed",
            "message": f"multiple IDE bridges matched this project; refusing ambiguous visible delivery: {details}",
            "adapter_name": "ide-terminal-visible",
        }
    bridge = candidates[0]
    status, response = poster(
        f"http://127.0.0.1:{bridge['port']}/terminal/send",
        {
            "project_path": input_data["project_path"],
            "target_slug": target,
            "prompt": input_data["synthetic_prompt"],
            "task_id": input_data.get("task_id", ""),
            "source_path": input_data.get("source_path", ""),
        },
        timeout=min(timeout, 15),
        headers={"Authorization": f"Bearer {bridge['token']}"},
    )
    if 200 <= status < 300:
        try:
            detail = json.loads(response) if isinstance(response, str) else response
        except json.JSONDecodeError:
            detail = {}
        detail = detail if isinstance(detail, dict) else {}
        evidence = {
            "schema": "ai-collab.visible-surface.v1",
            "agent": target,
            "project_path": str(Path(input_data["project_path"]).resolve()),
            "updated": isoformat_z(utc_now()),
            "status": "submitted-visibly",
            "adapter": "ide-terminal-visible",
            "ide": str(bridge.get("ide") or ""),
            "bridge_owner": "ide-visible-bridge",
            "bridge_pid": bridge["pid"],
            "bridge_port": bridge["port"],
            "terminal_name": str(detail.get("terminal_name") or ""),
            "shell_pid": detail.get("shell_pid") or "",
            "agent_pid": detail.get("agent_pid") or "",
            "tty": str(detail.get("tty") or ""),
            "agent_id": str(detail.get("agent_id") or ""),
            "session_id": str(detail.get("session_id") or ""),
            "surface_id": str(detail.get("surface_id") or ""),
            "identity": str(detail.get("identity") or ""),
            "task_id": input_data.get("task_id", ""),
            "source_path": input_data.get("source_path", ""),
        }
        evidence_path = Path(input_data["project_path"]) / ".ai-collab" / "live" / f"{target}.visible.json"
        atomic_write(evidence_path, json.dumps(evidence, indent=2) + "\n")
        return {
            "status": "success",
            "message": f"prompt submitted to {target}'s visible integrated terminal",
            "adapter_name": "ide-terminal-visible",
            "visual_evidence": str(evidence_path),
            **{key: value for key, value in evidence.items() if key not in {"status", "adapter"}},
        }
    detail = response.strip().replace("\n", " ") if isinstance(response, str) else str(response)
    return {
        "status": "failed",
        "message": f"IDE bridge rejected visible delivery ({status}): {detail[:400]}",
        "adapter_name": "ide-terminal-visible",
    }


def prepare_ide_terminal_visible_surface(
    project_path: str,
    target: str,
    *,
    timeout: int = 15,
    poster=None,
    registry_dir: Path = DEFAULT_IDE_BRIDGE_DIR,
) -> dict[str, Any]:
    """Focus an exact project terminal without submitting a prompt."""
    poster = poster or post_json
    candidates = ide_bridge_candidates(project_path, registry_dir=registry_dir)
    if len(candidates) != 1:
        return {
            "status": "failed",
            "target_slug": target,
            "message": (
                "no exact project IDE bridge" if not candidates
                else "multiple project IDE bridges; refusing ambiguous focus"
            ),
            "adapter_name": "ide-terminal-visible-prepare",
        }
    bridge = candidates[0]
    status, response = poster(
        f"http://127.0.0.1:{bridge['port']}/terminal/show",
        {"project_path": project_path, "target_slug": target},
        timeout=min(timeout, 15),
        headers={"Authorization": f"Bearer {bridge['token']}"},
    )
    if 200 <= status < 300:
        try:
            detail = json.loads(response) if isinstance(response, str) else response
        except json.JSONDecodeError:
            detail = {}
        detail = detail if isinstance(detail, dict) else {}
        return {
            "status": "success",
            "target_slug": target,
            "message": "exact visible integrated terminal focused without submitting a prompt",
            "adapter_name": "ide-terminal-visible-prepare",
            "terminal_name": str(detail.get("terminal_name") or ""),
            "agent_pid": detail.get("agent_pid") or "",
            "tty": str(detail.get("tty") or ""),
            "agent_id": str(detail.get("agent_id") or ""),
            "session_id": str(detail.get("session_id") or ""),
            "surface_id": str(detail.get("surface_id") or ""),
        }
    if status == 404:
        return {
            "status": "legacy-focus-on-submit",
            "target_slug": target,
            "message": (
                "installed bridge predates focus-only preparation; exact terminal identity is available, "
                "so submission must focus first and be followed immediately by visual proof"
            ),
            "adapter_name": "ide-terminal-visible-prepare",
        }
    detail = response.strip().replace("\n", " ") if isinstance(response, str) else str(response)
    return {
        "status": "failed",
        "target_slug": target,
        "message": f"IDE bridge rejected focus-only preparation ({status}): {detail[:400]}",
        "adapter_name": "ide-terminal-visible-prepare",
    }


def run_ide_native_chat_adapter(
    input_data: dict[str, str], *, timeout: int, poster=None, registry_dir: Path = DEFAULT_IDE_BRIDGE_DIR
) -> dict[str, Any]:
    """Submit to an exact IDE-native chat and bind the returned runtime session identity."""
    poster = poster or post_json
    guardrail = visible_guardrail(input_data, "ide-native-chat")
    if guardrail:
        return guardrail
    target = input_data["target_slug"]
    candidates = ide_bridge_candidates(input_data["project_path"], registry_dir=registry_dir)
    if len(candidates) != 1:
        return {"status": "failed", "message": "native chat requires exactly one project-matched IDE bridge", "adapter_name": "ide-native-chat"}
    bridge = candidates[0]
    status, response = poster(
        f"http://127.0.0.1:{bridge['port']}/native/send",
        {"project_path": input_data["project_path"], "target_slug": target, "prompt": input_data["synthetic_prompt"]},
        timeout=min(timeout, 15), headers={"Authorization": f"Bearer {bridge['token']}"},
    )
    try:
        detail = json.loads(response) if isinstance(response, str) else response
    except json.JSONDecodeError:
        detail = {}
    detail = detail if isinstance(detail, dict) else {}
    if not 200 <= status < 300:
        return {"status": "failed", "message": f"IDE native bridge rejected delivery ({status}): {str(response)[:400]}", "adapter_name": "ide-native-chat"}
    evidence = {
        "schema": "ai-collab.visible-surface.v2", "agent": target,
        "agent_id": detail.get("agent_id", ""), "session_id": detail.get("session_id", ""),
        "surface_id": detail.get("surface_id", ""), "project_path": str(Path(input_data["project_path"]).resolve()),
        "updated": isoformat_z(utc_now()), "status": "submitted-visibly", "adapter": "ide-native-chat",
        "bridge_owner": "ide-visible-bridge", "bridge_pid": bridge["pid"], "bridge_port": bridge["port"],
        "task_id": input_data.get("task_id", ""), "source_path": input_data.get("source_path", ""),
    }
    evidence_path = Path(input_data["project_path"]) / ".ai-collab" / "live" / f"{target}.visible.json"
    atomic_write(evidence_path, json.dumps(evidence, indent=2) + "\n")
    return {
        **evidence,
        "status": "success",
        "delivery_state": evidence["status"],
        "message": f"prompt submitted to {target}'s exact native chat",
        "adapter_name": "ide-native-chat",
    }


def prepare_ide_native_chat_surface(
    project_path: str, target: str, *, timeout: int = 15, poster=None, registry_dir: Path = DEFAULT_IDE_BRIDGE_DIR
) -> dict[str, Any]:
    poster = poster or post_json
    candidates = ide_bridge_candidates(project_path, registry_dir=registry_dir)
    if len(candidates) != 1:
        return {"status": "failed", "target_slug": target, "message": "native chat requires exactly one project-matched IDE bridge", "adapter_name": "ide-native-chat-prepare"}
    bridge = candidates[0]
    status, response = poster(
        f"http://127.0.0.1:{bridge['port']}/native/show",
        {"project_path": project_path, "target_slug": target}, timeout=min(timeout, 15),
        headers={"Authorization": f"Bearer {bridge['token']}"},
    )
    try:
        detail = json.loads(response) if isinstance(response, str) else response
    except json.JSONDecodeError:
        detail = {}
    detail = detail if isinstance(detail, dict) else {}
    if 200 <= status < 300:
        return {"status": "success", "target_slug": target, "message": "exact native chat focused", "adapter_name": "ide-native-chat-prepare", **detail}
    return {"status": "failed", "target_slug": target, "message": f"native chat focus failed ({status}): {str(response)[:400]}", "adapter_name": "ide-native-chat-prepare"}


def prepare_antigravity_chat_surface(project_path: str, target: str) -> dict[str, Any]:
    """Verify Codex/Antigravity is reachable without submitting a prompt.

    Unlike the terminal and native-chat adapters, antigravity-chat dispatches
    directly through the antigravity CLI (see build_antigravity_chat_command)
    and does not go through an IDE bridge, so there is no terminal/pane to
    focus ahead of time. Preparation is limited to confirming the CLI exists.
    """
    if target not in {"codex", "antigravity"}:
        return {
            "status": "failed",
            "target_slug": target,
            "message": "antigravity-chat preparation only supports target codex/antigravity",
            "adapter_name": "antigravity-chat-prepare",
        }
    if not antigravity_executable():
        return {
            "status": "failed",
            "target_slug": target,
            "message": "no antigravity executable found",
            "adapter_name": "antigravity-chat-prepare",
        }
    return {
        "status": "skipped",
        "target_slug": target,
        "message": "antigravity-chat dispatches directly via CLI; no focus-only preparation step is required",
        "adapter_name": "antigravity-chat-prepare",
    }


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


def opencode_current_project_matches(port: int, project_path: str, *, timeout: int, getter=None) -> bool:
    return any(
        item["source"].startswith("project/current.") and same_path(item["path"], project_path)
        for item in opencode_port_project_candidates(port, timeout=timeout, getter=getter)
    )


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
        if project_path and opencode_current_project_matches(
            port,
            project_path,
            timeout=fast_timeout,
            getter=getter,
        ):
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


def registered_container(project_root: Path, target_slug: str) -> str:
    """Container an agent was registered with in agents.json (e.g. vscode, antigravity)."""
    agents = load_json(project_root / ".ai-collab" / "agents.json", {})
    rows = agents.get("agents") if isinstance(agents, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("agent", "")).strip().lower() == target_slug.lower():
            return str(row.get("container", "")).strip().lower()
    return ""


def antigravity_executable() -> str | None:
    configured = os.environ.get("AI_COLLAB_ANTIGRAVITY_BIN")
    if configured:
        return configured
    for name in ("antigravity-ide", "antigravity"):
        found = executable_for(name)
        if found:
            return found
    fallbacks = (
        Path("/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide"),
        Path.home() / ".antigravity-ide/antigravity-ide/bin/antigravity-ide",
        Path.home() / ".antigravity/antigravity/bin/antigravity",
    )
    for fallback in fallbacks:
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


def run_codex_visible_or_auto(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
    fallback_label: str,
) -> dict[str, str]:
    """codex registered outside Antigravity: show it on screen, but never depend on that.

    ide-native-chat (the VS Code bridge's chatgpt.openSidebar + paste) is
    genuinely different from the Antigravity `--reuse-window` hack: it
    requires an EXACT single project-matched bridge
    (`ide_bridge_candidates`) and fails cleanly with no side effect when
    that isn't available -- it cannot "pop a stray window" the way
    Antigravity's CLI reuse-window guess could, so it is safe to attempt
    even unattended. Try it first so Luis (and anyone else running this
    skill) can watch codex work in its own panel; if the bridge isn't
    reachable (no VS Code window registered for this exact project, wrong
    focus, extension not reloaded yet, Accessibility permission missing,
    etc.) fall back to codex-auto so the wake still succeeds headlessly
    instead of silently doing nothing (2026-08-31, luisvelasquez project).
    """
    visible_result = run_ide_native_chat_adapter(input_data, timeout=min(timeout, 15))
    if visible_result.get("status") == "success":
        return visible_result
    auto_result = run_wakeup_adapter(input_data, mode="codex-auto", timeout=timeout, runner=runner)
    auto_result.setdefault("fallback_from", fallback_label)
    auto_result.setdefault("visible_attempt", visible_result)
    return auto_result


def run_antigravity_chat_adapter(
    input_data: dict[str, str],
    *,
    timeout: int,
    runner=subprocess.run,
) -> dict[str, str]:
    guardrail = visible_guardrail(input_data, "antigravity-chat")
    if guardrail:
        return guardrail
    target = input_data["target_slug"]
    # A project can register codex under a container other than Antigravity
    # (e.g. "vscode"). Antigravity IDE may still be running on the machine
    # for an unrelated project -- targeting it here would pop/reuse the
    # WRONG project's window. Show codex in its own VS Code panel when
    # reachable, falling back to headless codex-auto otherwise, whenever
    # codex isn't actually registered in Antigravity, in every context
    # (2026-08-31, luisvelasquez project: codex registered
    # container=vscode, Antigravity belongs to a different project on the
    # same machine).
    if target == "codex" and registered_container(Path(input_data["project_path"]), "codex") not in {"", "antigravity"}:
        return run_codex_visible_or_auto(
            input_data, timeout=timeout, runner=runner, fallback_label="antigravity-chat-wrong-container"
        )
    # codex has no public API to target its visible panel -- `antigravity-ide
    # chat --reuse-window` reuses "the last active window" (there is no way
    # to address codex's pane specifically), and an automated retry that
    # doesn't land can pop an entirely new, unwanted Antigravity IDE window
    # instead (confirmed live 2026-08-27, no human watching to notice).
    # The unattended daemon loop must never risk that; skip straight to a
    # degraded/no-op result and let the thread's honest timeout-no-response
    # note do its job. A human-invoked converse.py call (someone actually
    # watching, e.g. a deliberate test) is unaffected -- it doesn't run in
    # daemon context.
    if truthy_env("AI_COLLAB_DAEMON_CONTEXT") and target in {"codex", "antigravity"}:
        if target == "codex":
            # Unlike the Antigravity GUI injection above, neither
            # ide-native-chat (fails closed on a bridge mismatch) nor
            # codex-auto (no window/focus at all) carries the "stray
            # window" risk that made the daemon skip codex here in the
            # first place -- it is safe (and the whole point) to run this
            # unattended.
            return run_codex_visible_or_auto(
                input_data, timeout=timeout, runner=runner, fallback_label="antigravity-chat-daemon-skip"
            )
        return {
            "status": "degraded",
            "message": (
                "antigravity has no addressable visible-chat API and no headless fallback; "
                "automated background dispatch is disabled to avoid spawning stray Antigravity "
                "IDE windows unattended. Requires a human directly in its window, or an explicit "
                "interactive converse.py call."
            ),
            "adapter_name": "antigravity-chat-daemon-skip",
        }
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
            "status": "success",
            "message": "prompt submitted to the reused Antigravity IDE chat window",
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


def codex_acp_available_locally() -> bool:
    """True only when codex-acp can start without a cold npm/npx fetch.

    The npx fallback (`npx -y @zed-industries/codex-acp@latest`) can burn an
    entire adapter timeout on package resolution before ever producing a
    response, which starves codex-auto's CLI-exec fallback of its own budget
    (observed live 2026-08-31: both attempts timed out back-to-back on a real
    onboarding thread). Skip ACP outright when there is no explicit override
    and no locally installed codex-acp binary, instead of paying for its
    failure every time.
    """
    if os.environ.get("AI_COLLAB_CODEX_ACP_COMMAND"):
        return True
    return bool(executable_for("codex-acp"))


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
    project_root = Path(input_data["project_path"])
    capability = capability_for(project_root, target)
    cli_fallback_enabled = bool(
        isinstance(capability, dict) and (capability.get("visible") or {}).get("cli_fallback")
    )

    def _with_cli_fallback(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") == "success" or not cli_fallback_enabled:
            return result
        cli_result = run_cli_adapter(input_data, timeout=timeout, runner=runner)
        cli_result["fallback_from"] = result.get("adapter_name", "visible")
        cli_result["fallback_reason"] = result.get("message", "")
        return cli_result

    if target in {"claude-code-ide", "cursor-native", "windsurf-native", "copilot-chat"}:
        return run_ide_native_chat_adapter(input_data, timeout=timeout)
    if target in {"claude", "claude-code"}:
        return _with_cli_fallback(run_ide_terminal_visible_adapter(input_data, timeout=timeout))
    if target == "opencode":
        # When a project-local IDE bridge exists it is stronger evidence than
        # port discovery: the bridge resolves the exact integrated terminal,
        # focuses it, submits there, and returns the agent PID/TTY. A bridge
        # rejection is intentionally terminal; silently switching adapters
        # would destroy the visual guarantee.
        if ide_bridge_candidates(input_data["project_path"]):
            return _with_cli_fallback(run_ide_terminal_visible_adapter(input_data, timeout=timeout))
        return _with_cli_fallback(run_opencode_visible_adapter(input_data, timeout=timeout, runner=runner))
    if target == "kilo":
        if ide_bridge_candidates(input_data["project_path"]):
            return run_ide_terminal_visible_adapter(input_data, timeout=timeout)
        return run_kilo_visible_adapter(input_data, timeout=timeout, runner=runner)
    if target == "hermes":
        return run_hermes_uri_adapter(input_data, timeout=timeout, runner=runner)
    if target in {"codex", "antigravity"}:
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    if ide_bridge_candidates(input_data["project_path"]):
        return run_ide_terminal_visible_adapter(input_data, timeout=timeout)
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
            stdin=subprocess.DEVNULL,
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
    if mode in {"claude-visible", "ide-terminal-visible"}:
        return run_ide_terminal_visible_adapter(input_data, timeout=timeout)
    if mode == "opencode-auto":
        return run_opencode_auto_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "antigravity-chat":
        return run_antigravity_chat_adapter(input_data, timeout=timeout, runner=runner)
    if mode == "ide-native-chat":
        return run_ide_native_chat_adapter(input_data, timeout=timeout)
    if mode == "codex-filesystem":
        return run_codex_filesystem_adapter(input_data)
    if mode == "codex-auto":
        # CLI exec (real, authenticated `codex exec --sandbox workspace-write`)
        # is tried first: it needs only the codex binary already bundled with
        # the extension, while codex-acp falls back to `npx -y
        # @zed-industries/codex-acp@latest`, which can burn most of the
        # adapter timeout on a cold/failed npm fetch before ever reaching the
        # CLI fallback. Validated end-to-end against a live thread on
        # 2026-08-31 (luisvelasquez project) -- CLI exec alone reliably
        # produces a real, agent-authored file edit in well under the
        # default timeout. ACP stays as a secondary attempt for setups that
        # do have a working codex-acp install.
        cli_result = run_cli_adapter(input_data, timeout=timeout, runner=runner)
        if cli_result.get("status") == "success":
            return cli_result
        if codex_acp_available_locally():
            acp_result = run_codex_acp_adapter(input_data, timeout=timeout)
            if acp_result.get("status") == "success":
                acp_result["fallback_from"] = cli_result.get("adapter_name", "cli")
                acp_result["fallback_reason"] = cli_result.get("message", "")
                return acp_result
        else:
            acp_result = {
                "status": "failed",
                "message": "codex-acp skipped: no local codex-acp binary or AI_COLLAB_CODEX_ACP_COMMAND override configured",
                "adapter_name": "codex-acp-unavailable",
            }
        filesystem_result = run_codex_filesystem_adapter(input_data)
        filesystem_result["fallback_from"] = acp_result.get("adapter_name", "codex-acp")
        filesystem_result["fallback_reason"] = acp_result.get("message", "")
        filesystem_result["primary_failure"] = cli_result.get("message", "")
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


def resolve_pending_dispatch(
    *,
    entry: dict[str, Any],
    thread_path: Path,
    project_root: Path,
    project: str,
    task_id: str,
    inbox_name: str,
    target_slug: str,
    author_slug: str,
    synthetic_prompt: str,
    now: datetime,
    adapter_runner=subprocess.run,
) -> dict[str, Any]:
    """Decide what to do about a visible dispatch that already reported delivery
    success but has not yet produced a real reply from the target.

    A prior daemon cycle only proves the prompt reached the target's window
    (adapter status "success"), not that the agent read and answered it. This
    waits `reply_wait_seconds_from_env()` before treating silence as a real
    non-response, then either falls back to a real headless CLI turn (only for
    targets whose capability opts into it) or records an explicit
    timeout-no-response note instead of retrying in silence forever.
    """
    dispatched_at = parse_iso(str(entry.get("dispatched_at", ""))) or now
    wait = reply_wait_seconds_from_env()
    elapsed = (now - dispatched_at).total_seconds()
    if elapsed < wait:
        return {
            "entry": entry,
            "result": {
                "action": "awaiting-reply",
                "elapsed_seconds": elapsed,
                "wait_seconds": wait,
            },
        }

    capability = capability_for(project_root, target_slug)
    cli_fallback_enabled = bool(
        isinstance(capability, dict) and (capability.get("visible") or {}).get("cli_fallback")
    )
    attempts = coerce_int(str(entry.get("attempts", "0")), 0)
    timestamp = isoformat_z(now)
    done_entry = {"done": True, "attempts": attempts, "last_attempt": timestamp}

    if cli_fallback_enabled:
        cli_input = {
            "project_path": str(project_root),
            "target_slug": target_slug,
            "inbox_path": str(project_root / ".ai-collab" / inbox_name) if inbox_name else "",
            "source_path": str(thread_path),
            "source_type": "thread",
            "thread_path": str(thread_path),
            "task_id": task_id,
            "synthetic_prompt": synthetic_prompt,
        }
        cli_result = run_cli_adapter(cli_input, timeout=adapter_timeout_from_env(), runner=adapter_runner)
        if cli_result.get("status") == "success":
            note = (
                f"No se detecto respuesta propia de @{target_slug} en este hilo durante los {int(wait)}s "
                "posteriores al despacho visible (el mensaje llego a su ventana, pero eso no es lo mismo "
                "que una respuesta real). Se activo el fallback CLI headless y se entrego correctamente; "
                f"@{author_slug} puede seguir el hilo para ver si produce una respuesta."
            )
            resolution = "cli-fallback-success"
        else:
            note = (
                f"No se detecto respuesta propia de @{target_slug} en este hilo durante los {int(wait)}s "
                "posteriores al despacho visible, y el fallback CLI headless tambien fallo "
                f"({cli_result.get('message', 'sin detalle')}). No se reintenta mas para este mensaje; "
                f"@{author_slug} deberia decidir si esperar, redirigir, o seguir sin @{target_slug}."
            )
            resolution = "cli-fallback-failed"
    else:
        note = (
            f"timeout esperando a @{target_slug}: el mensaje se entrego a su chat visible pero no hubo "
            f"respuesta propia en este hilo durante los {int(wait)}s de espera. Puede estar ocupado en otra "
            f"tarea o proyecto. No se reintenta mas para este mensaje; @{author_slug} deberia decidir si "
            f"esperar, redirigir, o seguir sin @{target_slug}."
        )
        resolution = "timeout-no-response"

    append_thread_message(
        thread_path,
        task_id=task_id,
        project=project,
        inbox_name=inbox_name,
        author_slug="daemon",
        message=note,
        now=now,
    )
    done_entry["resolution"] = resolution
    return {"entry": done_entry, "result": {"action": resolution, "wait_seconds": wait}}


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

    stale_days = thread_stale_days()
    if stale_days > 0:
        last_activity = parse_iso(str(meta.get("updated") or meta.get("created") or ""))
        if last_activity and (now - last_activity).days >= stale_days:
            return {
                "action": "ignored",
                "reason": "stale",
                "last_activity": isoformat_z(last_activity),
                "stale_days": stale_days,
            }

    message = latest_thread_message(body)
    if not message:
        return {"action": "ignored", "reason": "no-message"}

    author_slug = message["author_slug"]
    targets = [slug for slug in find_mentions(message["content"]) if slug != author_slug]
    target_filter = set(csv_env("AI_COLLAB_WAKE_TARGETS"))
    if target_filter:
        targets = [slug for slug in targets if slug in target_filter]
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

        if inbox_name:
            primary_inbox = collab_root / inbox_name
            if primary_inbox.exists():
                inbox_meta, _inbox_body = parse_frontmatter(primary_inbox.read_text(encoding="utf-8"))
                if inbox_meta.get("status") in {"unread", "claimed", "running", "blocked", "review"}:
                    results.append({"target_slug": target_slug, "action": "inbox-primary"})
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
        if entry.get("dispatched"):
            # Delivery already succeeded once; a successful visible dispatch
            # only means the prompt reached the target's window, not that it
            # was read and answered. Resolve real-reply-or-timeout instead of
            # silently treating "delivered" as "done" forever.
            resolution = resolve_pending_dispatch(
                entry=entry,
                thread_path=thread_path,
                project_root=project_root,
                project=project,
                task_id=task_id,
                inbox_name=inbox_name,
                target_slug=target_slug,
                author_slug=author_slug,
                synthetic_prompt=(
                    f"You were mentioned in {thread_path} by @{author_slug}. "
                    "This is a real visible team conversation. Read the entire thread, add your own "
                    "opinion or recommendation to that same thread, explicitly mention the director "
                    "and relevant participants, then update your log. Do not merely acknowledge the wakeup."
                ),
                now=now,
                adapter_runner=adapter_runner,
            )
            state[state_key] = resolution["entry"]
            results.append({"target_slug": target_slug, **resolution["result"]})
            log(
                "THREAD "
                f"action={resolution['result']['action']} task_id={task_id} target={target_slug} "
                f"thread={thread_path}",
                log_file,
            )
            continue
        if attempts >= max_attempts:
            results.append({"target_slug": target_slug, "action": "failed", "attempts": attempts})
            continue


        grace = internal_grace_seconds(project_root, target_slug, now)
        sent_at = parse_iso(message.get("timestamp"))
        elapsed_internal = (now - sent_at).total_seconds() if sent_at else grace
        if elapsed_internal < grace:
            results.append(
                {
                    "target_slug": target_slug,
                    "action": "internal-grace",
                    "grace_seconds": grace,
                    "elapsed_seconds": elapsed_internal,
                }
            )
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
                "This is a real visible team conversation. Read the entire thread, add your own "
                "opinion or recommendation to that same thread, explicitly mention the director "
                "and relevant participants, then update your log. Do not merely acknowledge the wakeup."
            ),
        }
        emit_escalation_notice(project_root, [target_slug], thread_path, grace, now)
        adapter_result = dispatch_wake_event(
            event,
            events_file=events_file,
            adapter_mode=adapter_mode,
            adapter_runner=adapter_runner,
        )

        action = "notified"
        if adapter_result["status"] == "success":
            entry = {"dispatched": True, "dispatched_at": timestamp, "attempts": attempts, "last_attempt": timestamp}
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
    target_slug = (meta.get("to") or inbox_path.stem.replace("inbox-", "")).strip().lower()
    project_root = inbox_path.parent.parent
    if target_slug != "all" and not project_agent_known(project_root, target_slug):
        log(
            f"INBOX action=skipped task_id={task_id} target={target_slug} "
            f"reason=agent-not-in-project project={project_root} inbox={inbox_path}",
            log_file,
        )
        return {
            "action": "skipped",
            "reason": "agent-not-in-project",
            "task_id": task_id,
            "target_slug": target_slug,
        }
    attempts = coerce_int(meta.get("attempts"), 0)
    last_attempt = parse_iso(meta.get("last_attempt"))

    grace = internal_grace_seconds(project_root, target_slug, now)
    queued_at = parse_iso(meta.get("updated"))
    elapsed_internal = (now - queued_at).total_seconds() if queued_at else grace
    if attempts == 0 and elapsed_internal < grace:
        return {
            "action": "internal-grace",
            "task_id": task_id,
            "target_slug": target_slug,
            "grace_seconds": grace,
            "elapsed_seconds": elapsed_internal,
        }

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
    emit_escalation_notice(project_root, [target_slug], inbox_path, grace, now)
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
        # A bridge accepting a prompt proves delivery to an interface, not that
        # the target agent read or accepted the task. Only the target agent may
        # write claimed_by/claimed_at after its real turn begins.
        meta["visible_dispatched_at"] = timestamp
        meta["visible_adapter"] = adapter_result["adapter_name"]
        action = "dispatched"
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
    if len(argv) >= 4 and argv[1] == "--prepare-visible":
        project_root = Path(argv[2]).expanduser().resolve()
        targets = [value.strip().lower() for value in argv[3].split(",") if value.strip()]
        results = [
            prepare_ide_native_chat_surface(str(project_root), target)
            if target in {"claude-code-ide", "cursor-native", "windsurf-native", "copilot-chat"}
            else prepare_antigravity_chat_surface(str(project_root), target)
            if target in {"codex", "antigravity"}
            else prepare_ide_terminal_visible_surface(str(project_root), target)
            for target in targets
        ]
        ok = all(
            result.get("status") in {"success", "skipped", "legacy-focus-on-submit"}
            for result in results
        )
        print(json.dumps({"action": "prepare-visible", "ok": ok, "results": results}, sort_keys=True))
        return 0 if ok else 1
    if len(argv) >= 3 and argv[1] == "--scan-reviews":
        project_root = Path(argv[2]).expanduser().resolve()
        results = scan_missing_reviews(project_root)
        print(json.dumps({"action": "scan-reviews", "results": results}, sort_keys=True))
        return 0
    if len(argv) < 3:
        print(
            "Usage: ai-collab-wakeup.py <project> <inbox.md|thread.md> | "
            "--prepare-visible <project-root> <agent,...> | "
            "--scan-reviews <project-root>",
            file=sys.stderr,
        )
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
