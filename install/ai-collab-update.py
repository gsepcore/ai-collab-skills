#!/usr/bin/env python3
"""
AI Collab self-updater.

Updates the global install in ~/.claude from the configured raw GitHub branch,
then refreshes managed AI-COLLAB rule blocks in already-onboarded projects.
Only generated files and generated marker blocks are touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RAW_BASE = os.environ.get(
    "AI_COLLAB_UPDATE_RAW_BASE",
    "https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main",
).rstrip("/")

CLAUDE_DIR = Path.home() / ".claude"
SKILL_DIR = CLAUDE_DIR / "skills" / "collab"
STATE_FILE = CLAUDE_DIR / "ai-collab-update-state.json"
IDE_BRIDGE_SOURCE = CLAUDE_DIR / "ai-collab-vscode-bridge"
IDE_BRIDGE_VSIX = CLAUDE_DIR / "ai-collab-visible-bridge.vsix"

GLOBAL_FILES: list[tuple[str, Path, bool]] = [
    ("SKILL.md", SKILL_DIR / "SKILL.md", False),
    ("references/protocol.md", SKILL_DIR / "references" / "protocol.md", False),
    ("install/daemon.sh", CLAUDE_DIR / "ai-collab-daemon.sh", True),
    ("install/ai-collab-summary.py", CLAUDE_DIR / "ai-collab-summary.py", True),
    ("install/ai-collab-check-notifications.py", CLAUDE_DIR / "ai-collab-check-notifications.py", True),
    ("install/ai-collab-wakeup.py", CLAUDE_DIR / "ai-collab-wakeup.py", True),
    ("install/ai-collab-auto-onboard.py", CLAUDE_DIR / "ai-collab-auto-onboard.py", True),
    ("install/ai-collab-project-setup.py", CLAUDE_DIR / "ai-collab-project-setup.py", True),
    ("install/ai-collab-orchestrate.py", CLAUDE_DIR / "ai-collab-orchestrate.py", True),
    ("install/ai-collab-team.py", CLAUDE_DIR / "ai-collab-team.py", True),
    ("install/ai-collab-converse.py", CLAUDE_DIR / "ai-collab-converse.py", True),
    ("install/ai-collab-observer.py", CLAUDE_DIR / "ai-collab-observer.py", True),
    ("install/ai-collab-see.py", CLAUDE_DIR / "ai-collab-see.py", True),
    ("install/ai-collab-doctor.py", CLAUDE_DIR / "ai-collab-doctor.py", True),
    ("install/ai-collab-update.py", CLAUDE_DIR / "ai-collab-update.py", True),
    ("install/ai-collab-recover.py", CLAUDE_DIR / "ai-collab-recover.py", True),
    ("install/ai-collab-codex-bridge.py", CLAUDE_DIR / "ai-collab-codex-bridge.py", True),
    ("install/build-vscode-bridge.py", CLAUDE_DIR / "ai-collab-build-vscode-bridge.py", True),
    ("install/vscode-ai-collab-bridge/package.json", IDE_BRIDGE_SOURCE / "package.json", False),
    ("install/vscode-ai-collab-bridge/extension.js", IDE_BRIDGE_SOURCE / "extension.js", False),
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(rel: str, timeout: float) -> bytes:
    url = f"{RAW_BASE}/{rel}"
    request = urllib.request.Request(url, headers={"User-Agent": "ai-collab-update/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def update_global(timeout: float, dry_run: bool) -> dict[str, Any]:
    changed: list[str] = []
    unchanged: list[str] = []
    errors: list[str] = []
    for rel, dest, executable in GLOBAL_FILES:
        try:
            content = fetch(rel, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{rel}: {exc}")
            continue
        current = dest.read_bytes() if dest.exists() else b""
        if current and sha256(current) == sha256(content):
            unchanged.append(str(dest))
            continue
        changed.append(str(dest))
        if not dry_run:
            atomic_write_bytes(dest, content)
            if executable:
                mode = dest.stat().st_mode
                dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"changed": changed, "unchanged": unchanged, "errors": errors}


def ide_cli_candidates() -> list[Path]:
    paths = [
        shutil.which("antigravity-ide"),
        "/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide",
        shutil.which("code"),
        "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
        shutil.which("cursor"),
        "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
        shutil.which("windsurf"),
        "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
    ]
    result: list[Path] = []
    for value in paths:
        if not value:
            continue
        path = Path(value)
        if path.exists() and os.access(path, os.X_OK) and path not in result:
            result.append(path)
    return result


def refresh_visible_bridge(dry_run: bool) -> dict[str, Any]:
    builder = CLAUDE_DIR / "ai-collab-build-vscode-bridge.py"
    if not builder.exists() or not (IDE_BRIDGE_SOURCE / "package.json").exists():
        return {"status": "failed", "reason": "visible bridge source is incomplete"}
    commands: list[list[str]] = [
        [sys.executable, str(builder), "--source", str(IDE_BRIDGE_SOURCE), "--output", str(IDE_BRIDGE_VSIX)]
    ]
    commands.extend([[str(cli), "--install-extension", str(IDE_BRIDGE_VSIX), "--force"] for cli in ide_cli_candidates()])
    if dry_run:
        return {"status": "dry-run", "commands": commands}
    results = []
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        results.append({"command": command, "returncode": completed.returncode, "stderr": completed.stderr[-500:]})
        if completed.returncode != 0:
            return {"status": "failed", "results": results}
    if len(commands) == 1:
        return {"status": "failed", "reason": "no supported IDE CLI found", "results": results}
    return {"status": "updated", "results": results, "restart_required": True}


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def project_args_from_manifest(root: Path) -> tuple[list[str], str, dict[str, str]]:
    manifest = read_json(root / ".ai-collab" / "agents.json")
    agents_data = manifest.get("agents")
    agents: list[str] = []
    models: dict[str, str] = {}
    containers: list[str] = []
    if isinstance(agents_data, list):
        for item in agents_data:
            if not isinstance(item, dict):
                continue
            agent = str(item.get("agent") or "").strip()
            if not agent:
                continue
            agents.append(agent)
            model = str(item.get("model") or "").strip()
            if model:
                models[agent] = model
            container = str(item.get("container") or "").strip()
            if container and container not in containers:
                containers.append(container)
    if not agents:
        team = root / ".ai-collab" / "TEAM.md"
        if team.exists():
            for line in team.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    agent = stripped[2:].split()[0].strip("*`")
                    if agent and agent not in agents:
                        agents.append(agent)
    if "claude-code" not in agents:
        agents.insert(0, "claude-code")
    return agents, (containers[0] if containers else "unknown"), models


def discover_projects(home: Path, max_depth: int) -> list[Path]:
    projects: list[Path] = []
    base_depth = len(home.parts)
    skip = {
        ".Trash",
        ".cache",
        ".npm",
        ".pnpm-store",
        ".cargo",
        ".rustup",
        "Library/Caches",
        "node_modules",
    }
    for current, dirs, files in os.walk(home):
        path = Path(current)
        rel = path.relative_to(home) if path != home else Path(".")
        if str(rel) in skip or path.name in skip:
            dirs[:] = []
            continue
        if len(path.parts) - base_depth > max_depth:
            dirs[:] = []
            continue
        if ".ai-collab" in dirs:
            projects.append(path)
            dirs.remove(".ai-collab")
    return sorted(set(projects))


def refresh_project(root: Path, dry_run: bool) -> dict[str, Any]:
    helper = CLAUDE_DIR / "ai-collab-project-setup.py"
    if not helper.exists():
        return {"root": str(root), "status": "skipped", "reason": "project setup helper missing"}
    agents, container, models = project_args_from_manifest(root)
    model_arg = ",".join(f"{agent}={model}" for agent, model in models.items())
    cmd = [
        sys.executable,
        str(helper),
        "--root",
        str(root),
        "--agents",
        ",".join(agents),
        "--container",
        container,
        "--models",
        model_arg,
        "--non-interactive",
        "--refresh-protocol",
    ]
    if dry_run:
        return {"root": str(root), "status": "dry-run", "command": cmd}
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "root": str(root),
        "status": "updated" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-1000:],
    }


def write_state(state: dict[str, Any]) -> None:
    atomic_write_bytes(STATE_FILE, (json.dumps(state, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update AI Collab global files and managed project snippets.")
    parser.add_argument("--global-only", action="store_true", help="Only refresh ~/.claude installed files.")
    parser.add_argument("--projects-only", action="store_true", help="Only refresh already-onboarded projects.")
    parser.add_argument("--project", action="append", default=[], help="Project root to refresh. Can be repeated.")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory to scan for .ai-collab projects.")
    parser.add_argument("--max-depth", type=int, default=int(os.environ.get("AI_COLLAB_UPDATE_MAX_DEPTH", "6")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("AI_COLLAB_UPDATE_TIMEOUT", "15")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    started = utc_now()
    state: dict[str, Any] = {
        "schema": "ai-collab.update.v1",
        "started": isoformat_z(started),
        "raw_base": RAW_BASE,
        "global": {},
        "projects": [],
    }

    exit_code = 0
    if not args.projects_only:
        state["global"] = update_global(args.timeout, args.dry_run)
        if state["global"].get("errors"):
            exit_code = 1
        bridge_changed = any(
            "ai-collab-vscode-bridge" in path or "build-vscode-bridge" in path
            for path in state["global"].get("changed", [])
        )
        if bridge_changed or not IDE_BRIDGE_VSIX.exists():
            state["visible_bridge"] = refresh_visible_bridge(args.dry_run)
            if state["visible_bridge"].get("status") == "failed":
                exit_code = 1
        else:
            state["visible_bridge"] = {"status": "unchanged"}

    if not args.global_only:
        roots = [Path(p).expanduser().resolve() for p in args.project]
        if not roots:
            roots = discover_projects(Path(args.home).expanduser().resolve(), args.max_depth)
        for root in roots:
            if (root / ".ai-collab").is_dir():
                result = refresh_project(root, args.dry_run)
                state["projects"].append(result)
                if result.get("status") == "failed":
                    exit_code = 1

    state["finished"] = isoformat_z(utc_now())
    state["status"] = "ok" if exit_code == 0 else "partial"
    if not args.dry_run:
        write_state(state)
    print(json.dumps(state, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
