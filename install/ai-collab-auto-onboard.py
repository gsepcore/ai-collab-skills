#!/usr/bin/env python3
"""
Auto-onboard newly observed AI agents.

The daemon calls this when a recent `{slug}-{timestamp}.md` log appears in a
project's `.ai-collab/` directory. Known slugs get their rules snippet appended
idempotently; unknown slugs produce a low-priority inbox-all notice.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


START_MARKER = "<!-- AI-COLLAB-START agent={slug} -->"
END_MARKER = "<!-- AI-COLLAB-END agent={slug} -->"
STATE_FILE = ".auto-onboard-state.json"
KNOWN_SLUGS = {
    "cursor": {"heading": "Cursor", "rules": [".cursorrules"]},
    "windsurf": {"heading": "Windsurf", "rules": [".windsurfrules"]},
    "copilot": {"heading": "GitHub Copilot", "rules": [".github/copilot-instructions.md"]},
    "cursor-native": {"heading": "Cursor", "rules": [".cursorrules"]},
    "windsurf-native": {"heading": "Windsurf", "rules": [".windsurfrules"]},
    "copilot-chat": {"heading": "GitHub Copilot", "rules": [".github/copilot-instructions.md"]},
    "claude-code": {"heading": "Generic / Any AI", "rules": ["CLAUDE.md"]},
    "opencode": {"heading": "OpenCode / Minimax", "rules": [".opencode/rules/ai-collab.md", "AGENTS.md"]},
    "codex": {"heading": "Codex / GPT", "rules": ["AGENTS.md"]},
    "antigravity": {"heading": "Codex / GPT", "rules": ["AGENTS.md"]},
    "hermes": {"heading": "Hermes", "rules": ["AGENTS.md"]},
    "aider": {"heading": "Generic / Any AI", "rules": ["AGENTS.md"]},
}
SKIP_FILES = {"PROTOCOL.md", "CONTEXT.md", "TEAM.md"}
LOG_RE = re.compile(r"^([a-z][a-z0-9_-]*)-\d{8}-\d{6}\.md$")
AGENT_ALIASES = {
    "antigravity": "codex",
    "cursor": "cursor-native",
    "windsurf": "windsurf-native",
    "copilot": "copilot-chat",
}


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


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def slug_from_log(path: Path) -> str | None:
    if path.name in SKIP_FILES or path.name.startswith("inbox-") or path.name.startswith("thread-"):
        return None
    match = LOG_RE.match(path.name)
    return match.group(1).lower() if match else None


def registered_agents(collab_dir: Path) -> set[str] | None:
    manifest_path = collab_dir / "agents.json"
    manifest = load_json(manifest_path, None)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("agents"), list):
        return None
    registered: set[str] = set()
    for item in manifest["agents"]:
        if isinstance(item, str):
            slug = item
        elif isinstance(item, dict):
            slug = str(item.get("agent") or item.get("slug") or "")
        else:
            continue
        slug = slug.strip().lower()
        if slug:
            registered.add(AGENT_ALIASES.get(slug, slug))
    return registered


def protocol_path() -> Path:
    configured = os.environ.get("AI_COLLAB_PROTOCOL_FILE")
    if configured:
        return Path(configured).expanduser()
    installed = Path.home() / ".claude/skills/collab/references/protocol.md"
    if installed.exists():
        return installed
    return Path(__file__).resolve().parents[1] / "references/protocol.md"


def extract_snippet(protocol: str, heading: str) -> str | None:
    heading_re = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = heading_re.search(protocol)
    if not match:
        return None
    rest = protocol[match.end() :]
    fence_start = re.search(r"^```(?:markdown)?\s*$", rest, re.MULTILINE)
    if not fence_start:
        return None
    snippet_start = fence_start.end()
    fence_end = re.search(r"^```\s*$", rest[snippet_start:], re.MULTILINE)
    if not fence_end:
        return None
    snippet = rest[snippet_start : snippet_start + fence_end.start()].strip()
    return snippet or None


def append_rules_if_missing(root: Path, slug: str) -> str:
    config = KNOWN_SLUGS[slug]
    protocol = protocol_path().read_text(encoding="utf-8")
    snippet = extract_snippet(protocol, config["heading"])
    if not snippet:
        raise RuntimeError(f"missing protocol snippet for {slug}")
    wrapped = (
        f"{START_MARKER.format(slug=slug)}\n"
        f"{snippet.rstrip()}\n"
        f"{END_MARKER.format(slug=slug)}"
    )
    changed = False
    for rel in config["rules"]:
        rules_path = root / rel
        existing = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
        if START_MARKER.format(slug=slug) in existing:
            continue
        prefix = "\n\n" if existing and not existing.endswith("\n\n") else ""
        content = existing + prefix + wrapped.rstrip() + "\n"
        atomic_write(rules_path, content)
        changed = True
    return "appended" if changed else "noop"


def parse_team_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    in_roster = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## roster"):
            in_roster = True
            continue
        if in_roster and stripped.startswith("##"):
            break
        if in_roster:
            match = re.match(r"^\s*-\s*\*{0,2}([a-z][a-z0-9_-]*)\*{0,2}", line)
            if match:
                slugs.add(match.group(1).lower())
    return slugs


def merge_team_member(collab_dir: Path, project: str, slug: str, now: datetime) -> str:
    team_path = collab_dir / "TEAM.md"
    timestamp = isoformat_z(now)
    if not team_path.exists():
        atomic_write(
            team_path,
            "\n".join(
                [
                    "---",
                    f"project: {project}",
                    f"declared: {timestamp}",
                    "declared_by: ai-collab-daemon auto-onboard",
                    "---",
                    "",
                    "## Roster",
                    "",
                    "- claude-code (director)",
                    f"- {slug}",
                    "",
                    "## Notes",
                    "",
                    "Roster created automatically after a new agent wrote its first log.",
                    "",
                ]
            ),
        )
        return "appended"

    text = team_path.read_text(encoding="utf-8")
    if slug in parse_team_slugs(text):
        return "noop"

    if "## Roster" not in text:
        addition = f"\n\n## Roster\n\n- {slug}\n"
        atomic_write(team_path, text.rstrip() + addition)
        return "appended"

    lines = text.splitlines()
    output: list[str] = []
    inserted = False
    in_roster = False
    for line in lines:
        if in_roster and line.startswith("## ") and not inserted:
            output.extend(["", f"- {slug}"])
            inserted = True
        output.append(line)
        if line.strip().lower().startswith("## roster"):
            in_roster = True
    if in_roster and not inserted:
        if output and output[-1].strip():
            output.append("")
        output.append(f"- {slug}")
    atomic_write(team_path, "\n".join(output).rstrip() + "\n")
    return "appended"


def notify_unknown_slug(collab_dir: Path, slug: str, now: datetime) -> str:
    inbox = collab_dir / "inbox-all.md"
    timestamp = isoformat_z(now)
    message = (
        f"## New agent detected: {slug}\n\n"
        "A new AI logged its first session here. To onboard it manually, run:\n"
        f"`/collab onboard {slug}` in Claude Code.\n"
    )
    if inbox.exists():
        text = inbox.read_text(encoding="utf-8")
        if f"New agent detected: {slug}" in text:
            return "noop"
        if text.startswith("---\n"):
            text = re.sub(r"(?m)^status:.*$", "status: unread", text, count=1)
            text = re.sub(r"(?m)^updated:.*$", f"updated: {timestamp}", text, count=1)
            atomic_write(inbox, text.rstrip() + "\n\n" + message)
            return "notified-user"

    atomic_write(
        inbox,
        "\n".join(
            [
                "---",
                "from: ai-collab-daemon",
                "to: all",
                "priority: low",
                f"updated: {timestamp}",
                "status: unread",
                "---",
                "",
                message.rstrip(),
                "",
            ]
        ),
    )
    return "notified-user"


def process_log(project: str, log_path: Path, *, now: datetime | None = None) -> dict[str, str]:
    now = now or utc_now()
    slug = slug_from_log(log_path)
    if not slug:
        return {"action": "ignored", "slug": ""}

    collab_dir = log_path.parent
    root = collab_dir.parent
    state_path = collab_dir / STATE_FILE
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    seen = state.setdefault("seen_slugs", [])
    if slug in seen:
        return {"action": "noop", "slug": slug}

    roster = registered_agents(collab_dir)
    normalized_slug = AGENT_ALIASES.get(slug, slug)
    if roster is not None and normalized_slug not in roster:
        action = "ignored-unregistered"
    elif slug in KNOWN_SLUGS:
        action = append_rules_if_missing(root, slug)
        merge_team_member(collab_dir, project, slug, now)
    else:
        action = notify_unknown_slug(collab_dir, slug, now)
        merge_team_member(collab_dir, project, slug, now)

    if slug not in seen:
        seen.append(slug)
    state["seen_slugs"] = sorted(seen)
    state["updated"] = isoformat_z(now)
    write_json(state_path, state)
    return {"action": action, "slug": slug}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: ai-collab-auto-onboard.py <project> <log-path>", file=sys.stderr)
        return 2
    project = argv[1]
    log_path = Path(argv[2])
    try:
        result = process_log(project, log_path)
    except Exception as exc:
        print(f"[AI-COLLAB] {isoformat_z(utc_now())} AUTO-ONBOARD slug=unknown action=error error={exc}", file=sys.stderr)
        return 1
    if result["action"] != "ignored":
        print(f"[AI-COLLAB] {isoformat_z(utc_now())} AUTO-ONBOARD slug={result['slug']} action={result['action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
