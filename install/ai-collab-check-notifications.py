#!/usr/bin/env python3
"""
AI Collab — UserPromptSubmit notification reader (robust version).

Reads ~/.ai-collab-notifications.json, prints any pending notifications
from other AIs as injected context for the current prompt, then clears
the file atomically. Hardened against malformed input, lock contention,
oversized payloads, and stale notifications.

Exit code: always 0 — must never block the user prompt.

Env vars (all optional):
  AI_COLLAB_LOCK_TIMEOUT    Lock acquisition timeout in seconds (default 3)
  AI_COLLAB_MAX_AGE_HOURS   Discard notifications older than this (default 24)
  AI_COLLAB_MAX_ITEMS       Max notifications to print            (default 10)
  AI_COLLAB_MAX_NOTE_CHARS  Max chars per notification             (default 500)
  AI_COLLAB_MAX_OUTPUT      Max total stdout chars                 (default 4000)
"""
import fcntl
import json
import os
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

SKIP_FILES = {"CONTEXT.md", "PROTOCOL.md"}


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
        ts = parse_timestamp(x.get("ts") or x.get("updated") or x.get("timestamp"))
        if ts is not None and ts < cutoff:
            continue
        keep.append(x)
    return keep


def format_note(x):
    """One-line, truncated summary of a notification."""
    ai = str(x.get("ai", "unknown"))[:40]
    fname = str(x.get("file", ""))[:80]
    msg = str(x.get("message") or x.get("note") or x.get("summary") or "")
    msg = msg.replace("\n", " ").strip()
    if len(msg) > MAX_NOTE_CHARS:
        msg = msg[:MAX_NOTE_CHARS] + "...[truncated]"
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
    kept = filter_notifications(items)

    if kept:
        lines = ["[AI-COLLAB] Pending notifications from other AIs:"]
        for x in kept[:MAX_ITEMS]:
            lines.append("  - " + format_note(x))
        if len(kept) > MAX_ITEMS:
            lines.append(f"  - ...and {len(kept) - MAX_ITEMS} more")
        lines.append("[END AI-COLLAB]")
        output = "\n".join(lines)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...[output capped]\n[END AI-COLLAB]"
        print(output)

    try:
        atomic_write(NOTIF_FILE, "[]")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
