#!/usr/bin/env python3
"""
Local Codex/Antigravity bridge API.

This is a stable localhost facade for agents that need to address Codex without
knowing whether the current machine supports visible Antigravity injection,
background Codex ACP, or only filesystem wake events.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("AI_COLLAB_CODEX_BRIDGE_PORT", "8765"))
DEFAULT_MODE = os.environ.get("AI_COLLAB_CODEX_BRIDGE_MODE", "background").strip() or "background"
BRIDGE_VERSION = "0.1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def slugify(value: str, fallback: str = "codex-message") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or fallback


def bridge_token() -> str:
    return os.environ.get("AI_COLLAB_CODEX_BRIDGE_TOKEN", "")


def project_root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"project_path is not a directory: {path}")
    collab = root / ".ai-collab"
    if not collab.is_dir():
        raise ValueError(f"project_path has no .ai-collab directory: {path}")
    return root


def wakeup_script() -> Path:
    installed = Path.home() / ".claude" / "ai-collab-wakeup.py"
    if installed.exists():
        return installed
    return Path(__file__).resolve().parent / "ai-collab-wakeup.py"


def adapter_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized in {"background", "headless", "autonomous"}:
        return "codex-acp"
    if normalized in {"visible", "foreground"}:
        return "antigravity-chat"
    if normalized == "auto":
        return "visible"
    if normalized in {"notify", "notify-only"}:
        return "notify-only"
    if normalized in {"codex-acp", "antigravity-chat", "visible"}:
        return normalized
    raise ValueError(f"unsupported bridge mode: {mode}")


def write_codex_discussion(root: Path, payload: dict[str, Any], now: datetime | None = None) -> Path:
    now = now or utc_now()
    timestamp = isoformat_z(now)
    from_agent = str(payload.get("from_agent") or payload.get("from") or "external-agent").strip() or "external-agent"
    topic = str(payload.get("topic") or "codex-bridge-message").strip() or "codex-bridge-message"
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    if "@codex" not in message:
        message = f"@codex {message}"
    thread = f"discussion-{now.strftime('%Y%m%d-%H%M%S')}-{slugify(topic)}"
    path = root / ".ai-collab" / "discussions" / f"{thread}.md"
    participants = ", ".join(dict.fromkeys([from_agent, "codex"]))
    content = f"""---
schema: ai-collab.thread.v2
thread: {thread}
kind: discussion
topic: {topic}
project: {root.name}
created: {timestamp}
updated: {timestamp}
participants: {participants}
status: open
---
## {timestamp} -- {from_agent}

type: message
to: codex
tags: codex-bridge

{message}

---
"""
    atomic_write(path, content)
    return path


def dispatch_wakeup(
    root: Path,
    thread_path: Path,
    mode: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    script = wakeup_script()
    if not script.exists():
        return {"status": "failed", "message": "ai-collab-wakeup.py not found"}
    env = os.environ.copy()
    env["AI_COLLAB_WAKEUP_ADAPTER"] = adapter_mode(mode)
    completed = runner(
        [sys.executable, str(script), root.name, str(thread_path)],
        cwd=str(root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=int(os.environ.get("AI_COLLAB_CODEX_BRIDGE_TIMEOUT", "180")),
        check=False,
    )
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "adapter_mode": env["AI_COLLAB_WAKEUP_ADAPTER"],
    }


def handle_message(
    payload: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = project_root(str(payload.get("project_path") or payload.get("root") or ""))
    mode = str(payload.get("mode") or DEFAULT_MODE)
    thread_path = write_codex_discussion(root, payload, now=now)
    wakeup = dispatch_wakeup(root, thread_path, mode, runner=runner)
    return {
        "ok": wakeup.get("status") == "ok",
        "bridge": "codex-antigravity",
        "version": BRIDGE_VERSION,
        "mode": mode,
        "thread_path": str(thread_path),
        "wakeup": wakeup,
        "note": (
            "background/codex-acp can run autonomous Codex workers; visible "
            "Antigravity wakeup is best-effort until a public Codex panel API exists."
        ),
    }


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"AI-Collab-Codex-Bridge/{BRIDGE_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("AI_COLLAB_CODEX_BRIDGE_LOG", "1") != "0":
            super().log_message(fmt, *args)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        token = bridge_token()
        if not token:
            return True
        return self.headers.get("Authorization") == f"Bearer {token}"

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json(
                200,
                {
                    "ok": True,
                    "bridge": "codex-antigravity",
                    "version": BRIDGE_VERSION,
                    "modes": ["background", "visible", "auto", "notify-only"],
                    "default_mode": DEFAULT_MODE,
                    "visible_status": "degraded-no-public-codex-panel-api",
                },
            )
            return
        self.write_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.write_json(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path not in {"/v1/codex/message", "/v1/codex/wake"}:
            self.write_json(404, {"ok": False, "error": "not found"})
            return
        try:
            result = handle_message(self.read_body())
        except Exception as exc:  # noqa: BLE001 - HTTP boundary returns errors as JSON.
            self.write_json(400, {"ok": False, "error": str(exc)})
            return
        self.write_json(200 if result["ok"] else 202, result)


def serve(host: str, port: int) -> int:
    with http.server.ThreadingHTTPServer((host, port), BridgeHandler) as httpd:
        print(f"[AI-COLLAB] Codex bridge listening on http://{host}:{port}")
        httpd.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Codex/Antigravity bridge API.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="Run the localhost HTTP bridge.")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    send_parser = sub.add_parser("send", help="Send one message through the bridge logic without HTTP.")
    send_parser.add_argument("--project", required=True)
    send_parser.add_argument("--message", required=True)
    send_parser.add_argument("--from-agent", default="external-agent")
    send_parser.add_argument("--topic", default="codex-bridge-message")
    send_parser.add_argument("--mode", default=DEFAULT_MODE)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args.host, args.port)
    result = handle_message(
        {
            "project_path": args.project,
            "message": args.message,
            "from_agent": args.from_agent,
            "topic": args.topic,
            "mode": args.mode,
        }
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
