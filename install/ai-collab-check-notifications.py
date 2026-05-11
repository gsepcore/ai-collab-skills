#!/usr/bin/env python3
"""
AI Collab — UserPromptSubmit notification reader (robust + project-isolated).

Reads ~/.ai-collab-notifications.json, prints notifications belonging to the
currently active project as injected context, then atomically removes only
the consumed notifications. Notifications from other projects are preserved
in the file and only surface when Claude runs inside that project.

Exit code: always 0 — must never block the user prompt.

Env vars (all optional):
  AI_COLLAB_LOCK_TIMEOUT    Lock acquisition timeout in seconds  (default 3)
  AI_COLLAB_MAX_AGE_HOURS   Discard notifications older than this (default 24)
  AI_COLLAB_MAX_ITEMS       Max notifications to print            (default 10)
  AI_COLLAB_MAX_NOTE_CHARS  Max chars per notification            (default 500)
  AI_COLLAB_MAX_OUTPUT      Max total stdout chars                (default 4000)
  AI_COLLAB_CROSS_PROJECT   Set to "1" to show notifications from
                            ALL projects, not just the active one (default off)
  AI_COLLAB_PROJECT         Override active project name detection
                            (default: git toplevel basename, fall back to cwd)
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

NOTIF_FILE = os.path.expanduser("~/.ai-collab-notifications.json")

LOCK_TIMEOUT_SEC = float(os.environ.get("AI_COLLAB_LOCK_TIMEOUT", "3.0"))
MAX_AGE_HOURS = float(os.environ.get("AI_COLLAB_MAX_AGE_HOURS", "24"))
MAX_ITEMS = int(os.environ.get("AI_COLLAB_MAX_ITEMS", "10"))
MAX_NOTE_CHARS = int(os.environ.get("AI_COLLAB_MAX_NOTE_CHARS", "500"))
MAX_OUTPUT_CHARS = int(os.environ.get("AI_COLLAB_MAX_OUTPUT", "4000"))
CROSS_PROJECT = os.environ.get("AI_COLLAB_CROSS_PROJECT") == "1"

SKIP_FILES = {"CONTEXT.md", "PROTOCOL.md"}


def detect_project():
    """Return the active project name (basename of git toplevel or cwd)."""
    override = os.environ.get("AI_COLLAB_PROJECT")
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.basename(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    try:
        return os.path.basename(os.getcwd())
    except OSError:
        return ""


def acquire_lock(fileobj, timeout):
    """Try non-blocking lock, polling until timeout. Returns True if acquired."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fcntl.flock(fileobj, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            time.sleep(0.05)
    return False


def coerce_list(data):
    """Force any shape into a list of items."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def parse_timestamp(value):
    """Parse ISO 8601 timestamp, return None if unparseable."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def split_by_project(items, current_project):
    """Return (for_current, for_others). Items without a project tag are
    treated as belonging to the current project (legacy compatibility)."""
    for_current, for_others = [], []
    for x in items:
        if not isinstance(x, dict):
            continue
        proj = x.get("project")
        if not proj or proj == current_project:
            for_current.append(x)
        else:
            for_others.append(x)
    return for_current, for_others


def filter_notifications(items):
    """Return only valid, recent, non-system notifications."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    keep = []
    for x in items:
        if not isinstance(x, dict):
            continue
        ai = x.get("ai")
        if not ai or not isinstance(ai, str):
            continue
        if x.get("file") in SKIP_FILES:
            continue
        ts = parse_timestamp(
            x.get("ts") or x.get("updated") or x.get("timestamp")
        )
        if ts is not None and ts < cutoff:
            continue
        keep.append(x)
    return keep


def format_note(x, include_project=False):
    """One-line, truncated summary of a notification."""
    ai = str(x.get("ai", "unknown"))[:40]
    fname = str(x.get("file", ""))[:80]
    msg = str(
        x.get("working")
        or x.get("message")
        or x.get("note")
        or x.get("summary")
        or ""
    )
    msg = msg.replace("\n", " ").strip()
    if len(msg) > MAX_NOTE_CHARS:
        msg = msg[:MAX_NOTE_CHARS] + "...[truncated]"
    if include_project:
        proj = str(x.get("project", "?"))[:40]
        parts = [f"[{ai}/{proj}]"]
    else:
        parts = [f"[{ai}]"]
    if fname:
        parts.append(fname)
    if msg:
        parts.append(msg)
    return " · ".join(parts)


def atomic_write(path, content):
    """Atomic write via temp file + os.replace (survives crash mid-write)."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    if not os.path.exists(NOTIF_FILE):
        return

    try:
        with open(NOTIF_FILE, "r", encoding="utf-8") as f:
            if not acquire_lock(f, LOCK_TIMEOUT_SEC):
                return
            try:
                raw = f.read()
            finally:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError:
        return

    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            atomic_write(NOTIF_FILE, "[]")
        except OSError:
            pass
        return

    items = coerce_list(data)
    current_project = detect_project()

    if CROSS_PROJECT:
        for_current, for_others = items, []
    else:
        for_current, for_others = split_by_project(items, current_project)

    kept = filter_notifications(for_current)

    if kept:
        header_proj = "cross-project" if CROSS_PROJECT else f"project={current_project}"
        lines = [f"[AI-COLLAB {header_proj}] Pending notifications from other AIs:"]
        for x in kept[:MAX_ITEMS]:
            lines.append("  - " + format_note(x, include_project=CROSS_PROJECT))
        if len(kept) > MAX_ITEMS:
            lines.append(f"  - ...and {len(kept) - MAX_ITEMS} more")
        lines.append("[END AI-COLLAB]")
        output = "\n".join(lines)
        if len(output) > MAX_OUTPUT_CHARS:
            output = (
                output[:MAX_OUTPUT_CHARS]
                + "\n...[output capped]\n[END AI-COLLAB]"
            )
        print(output)

    # Preserve notifications from OTHER projects untouched; drop everything
    # we processed for the current project (consumed or filtered out).
    remaining = [] if CROSS_PROJECT else for_others
    try:
        atomic_write(NOTIF_FILE, json.dumps(remaining))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
