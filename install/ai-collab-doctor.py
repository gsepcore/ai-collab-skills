#!/usr/bin/env python3
"""
AI Collab install health check.

This script verifies the files, hooks, daemon, and local queues that the
one-line installer manages. It is intentionally read-only.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_CLAUDE_FILES = (
    ".claude/ai-collab-daemon.sh",
    ".claude/ai-collab-summary.py",
    ".claude/ai-collab-check-notifications.py",
    ".claude/ai-collab-wakeup.py",
    ".claude/ai-collab-auto-onboard.py",
    ".claude/ai-collab-project-setup.py",
    ".claude/ai-collab-orchestrate.py",
    ".claude/ai-collab-observer.py",
    ".claude/ai-collab-doctor.py",
)

REQUIRED_SKILL_FILES = (
    ".claude/skills/collab/SKILL.md",
    ".claude/skills/collab/references/protocol.md",
)

OPTIONAL_JSON_QUEUES = (
    ".ai-collab-notifications.json",
    ".ai-collab-wakeup-events.json",
    ".ai-collab-wakeup-state.json",
)


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    message: str


def ok(name: str, message: str) -> CheckResult:
    return CheckResult("OK", name, message)


def warn(name: str, message: str) -> CheckResult:
    return CheckResult("WARN", name, message)


def fail(name: str, message: str) -> CheckResult:
    return CheckResult("FAIL", name, message)


def load_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
    except OSError as exc:
        return None, str(exc)


def check_required_files(home: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rel in (*REQUIRED_CLAUDE_FILES, *REQUIRED_SKILL_FILES):
        path = home / rel
        if path.exists():
            results.append(ok(rel, "present"))
        else:
            results.append(fail(rel, f"missing: {path}"))
    return results


def check_settings(home: Path) -> list[CheckResult]:
    path = home / ".claude/settings.json"
    if not path.exists():
        return [warn("settings", f"missing: {path}")]

    data, error = load_json(path)
    if error:
        return [fail("settings", error)]
    if not isinstance(data, dict):
        return [fail("settings", "settings.json must contain a JSON object")]

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return [warn("hooks", "no hooks object found in ~/.claude/settings.json")]

    events: list[str] = []
    for event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
                command = hook.get("command", "") if isinstance(hook, dict) else ""
                if "ai-collab" in command:
                    events.append(str(event_name))

    if not events:
        return [warn("hooks", "no ai-collab hook commands found in ~/.claude/settings.json")]

    unique_events = ", ".join(sorted(set(events)))
    return [ok("hooks", f"ai-collab hooks found for: {unique_events}")]


def check_optional_json_queues(home: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rel in OPTIONAL_JSON_QUEUES:
        path = home / rel
        if not path.exists():
            results.append(ok(rel, "not present yet"))
            continue
        _data, error = load_json(path)
        if error:
            results.append(fail(rel, error))
        else:
            results.append(ok(rel, "valid JSON"))
    return results


def check_launchd() -> list[CheckResult]:
    if platform.system() != "Darwin":
        return [warn("daemon", "launchd check skipped because this is not macOS")]

    try:
        completed = subprocess.run(
            ["launchctl", "list", "com.gsepcore.ai-collab"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [warn("daemon", f"could not query launchd: {exc}")]

    if completed.returncode == 0:
        return [ok("daemon", "com.gsepcore.ai-collab is loaded")]
    return [warn("daemon", "com.gsepcore.ai-collab is not loaded")]


def check_cli_projects_allowlist(home: Path) -> list[CheckResult]:
    # If the operator left an explicit allowlist in the plist, surface it.
    # A common debug-time mistake is to pin the daemon to a single project
    # and forget to open it back up. Without this check the symptom only
    # shows in other projects ("nothing happens when I write an inbox here").
    plist_path = home / "Library/LaunchAgents/com.gsepcore.ai-collab.plist"
    if not plist_path.exists():
        return []
    try:
        text = plist_path.read_text(encoding="utf-8")
    except OSError:
        return []
    marker = "<key>AI_COLLAB_WAKEUP_CLI_PROJECTS</key>"
    idx = text.find(marker)
    if idx == -1:
        return [ok("wakeup-cli-projects", "no allowlist set; daemon permits all projects with .ai-collab/")]
    rest = text[idx + len(marker) :]
    start = rest.find("<string>")
    end = rest.find("</string>")
    if start == -1 or end == -1 or end <= start:
        return []
    value = rest[start + len("<string>") : end].strip()
    if not value or value == "*":
        return [ok("wakeup-cli-projects", f"allowlist={value or '(empty)'} — all projects permitted")]
    return [
        warn(
            "wakeup-cli-projects",
            f"allowlist restricts daemon to: {value}. Other projects will NOT trigger "
            "the wakeup adapter. To open globally, edit the plist EnvironmentVariables "
            "and set AI_COLLAB_WAKEUP_CLI_PROJECTS to '*' or remove the key, then reload.",
        )
    ]


def check_codex_visible_support() -> list[CheckResult]:
    # Codex visible wakeup (inside Antigravity) is blocked upstream — the
    # extension openai.chatgpt exposes no public API, no public socket on the
    # active app-server, and no proposed VS Code API surface reachable to
    # third-party companion extensions. Three independent reviewers (Cody,
    # Thomas, Claude) confirmed this on 2026-05-13. This check surfaces the
    # limitation as a WARN so a fresh installer never silently expects the
    # invisible wakeup to reach the visible Codex tab.
    return [
        warn(
            "codex-visible",
            "degraded: invisible wakeup of the visible Antigravity Codex tab "
            "is blocked upstream (no public API on openai.chatgpt). Available "
            "modes for Codex today: visible (degraded, best-effort via "
            "antigravity-chat), codex-acp (parallel invisible worker), manual "
            "(user types 'lee tu inbox'). OpenCode/Claude unaffected.",
        )
    ]


def check_ocr_engine() -> list[CheckResult]:
    configured = os.environ.get("AI_COLLAB_OBSERVER_TESSERACT_BIN", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        resolved = shutil.which(configured) or (str(configured_path) if configured_path.exists() else "")
        if resolved:
            return [ok("ocr-engine", f"tesseract configured → {resolved}")]
        return [
            warn(
                "ocr-engine",
                f"AI_COLLAB_OBSERVER_TESSERACT_BIN is set to {configured!r}, but it was not found. "
                "OCR will fall back to metadata-only semantic vision.",
            )
        ]

    detected = shutil.which("tesseract")
    if detected:
        return [ok("ocr-engine", f"tesseract available → {detected}")]
    return [
        warn(
            "ocr-engine",
            "tesseract not found. Fresh installs try to install it automatically by default; "
            "without it, screenshots still work but semantic vision is metadata-only.",
        )
    ]


def run_checks(home: Path | None = None, include_launchd: bool = True) -> list[CheckResult]:
    root = home or Path.home()
    results: list[CheckResult] = []
    results.extend(check_required_files(root))
    results.extend(check_settings(root))
    results.extend(check_optional_json_queues(root))
    if include_launchd:
        results.extend(check_launchd())
        results.extend(check_cli_projects_allowlist(root))
    results.extend(check_ocr_engine())
    results.extend(check_codex_visible_support())
    return results


def exit_code(results: list[CheckResult], strict: bool) -> int:
    if strict and any(result.level == "FAIL" for result in results):
        return 1
    return 0


def print_results(results: list[CheckResult]) -> None:
    symbols = {"OK": "OK", "WARN": "WARN", "FAIL": "FAIL"}
    for result in results:
        print(f"[{symbols[result.level]}] {result.name}: {result.message}")

    failures = sum(1 for result in results if result.level == "FAIL")
    warnings = sum(1 for result in results if result.level == "WARN")
    print("")
    print(f"Summary: {failures} failure(s), {warnings} warning(s)")


def main() -> int:
    strict = os.environ.get("AI_COLLAB_DOCTOR_STRICT") == "1"
    results = run_checks()
    print_results(results)
    return exit_code(results, strict)


if __name__ == "__main__":
    sys.exit(main())
