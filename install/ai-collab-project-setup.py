#!/usr/bin/env python3
"""
Project onboarding for AI Collab.

This is the deterministic setup path used by `/collab setup` and optionally by
the one-line installer. It is intentionally agent-first:

  agent     = the runtime that reads instructions and performs work
  container = the IDE/terminal where the agent is visible
  model     = metadata about the LLM behind the agent
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


START_MARKER = "<!-- AI-COLLAB-START agent={agent} -->"
END_MARKER = "<!-- AI-COLLAB-END agent={agent} -->"

AGENT_CATALOG: dict[str, dict[str, Any]] = {
    "claude-code": {
        "display": "Claude Code",
        "role": "director",
        "rules": ["CLAUDE.md"],
        "detect": [["claude"]],
    },
    "opencode": {
        "display": "OpenCode",
        "role": "worker",
        "rules": [".opencode/rules/ai-collab.md", "AGENTS.md"],
        "detect": [["opencode"]],
    },
    "codex": {
        "display": "Codex",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [["codex"]],
    },
    "aider": {
        "display": "Aider",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [["aider"]],
    },
    "hermes": {
        "display": "Hermes",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [["hermes"]],
    },
    "kimi": {
        "display": "Kimi Code",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [["kimi"], ["kimi-cli"]],
    },
    "kilo": {
        "display": "Kilo Code",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [["kilo"]],
    },
    "cursor-native": {
        "display": "Cursor native chat",
        "role": "worker",
        "rules": [".cursorrules"],
        "detect": [["cursor"], ["/Applications/Cursor.app"]],
    },
    "windsurf-native": {
        "display": "Windsurf native chat",
        "role": "worker",
        "rules": [".windsurfrules"],
        "detect": [["windsurf"], ["/Applications/Windsurf.app"]],
    },
    "copilot-chat": {
        "display": "GitHub Copilot Chat",
        "role": "worker",
        "rules": [".github/copilot-instructions.md"],
        "detect": [],
    },
    "generic": {
        "display": "Generic AI agent",
        "role": "worker",
        "rules": ["AGENTS.md"],
        "detect": [],
    },
}

ALIASES = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "claude-code": "claude-code",
    "cursor": "cursor-native",
    "windsurf": "windsurf-native",
    "copilot": "copilot-chat",
    "vscode": "copilot-chat",
    "antigravity": "codex",
    "kimi-code": "kimi",
    "kilo-code": "kilo",
}

CONTAINER_CHOICES = ["antigravity", "cursor", "vscode", "windsurf", "terminal", "zed", "jetbrains", "other"]


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


def project_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except OSError:
        pass
    return Path(os.getcwd()).resolve()


def normalize_agent(slug: str) -> str:
    key = slug.strip().lower().replace(" ", "-")
    return ALIASES.get(key, key)


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    for item in value.split(","):
        normalized = normalize_agent(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def parse_models(value: str | None) -> dict[str, str]:
    models: dict[str, str] = {}
    if not value:
        return models
    for item in value.split(","):
        if "=" not in item:
            continue
        agent, model = item.split("=", 1)
        agent = normalize_agent(agent)
        model = model.strip()
        if agent and model:
            models[agent] = model
    return models


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def detect_agents(root: Path) -> list[str]:
    detected: list[str] = ["claude-code"]
    existing_paths = {
        ".cursorrules": "cursor-native",
        ".windsurfrules": "windsurf-native",
        ".github/copilot-instructions.md": "copilot-chat",
        ".opencode/rules": "opencode",
        "CLAUDE.md": "claude-code",
    }
    for rel, agent in existing_paths.items():
        if (root / rel).exists() and agent not in detected:
            detected.append(agent)
    for agent, config in AGENT_CATALOG.items():
        for signal in config.get("detect", []):
            if len(signal) != 1:
                continue
            value = signal[0]
            found = Path(value).exists() if value.startswith("/") else command_exists(value)
            if found and agent not in detected:
                detected.append(agent)
    if (Path.home() / ".antigravity/extensions").exists() and "codex" not in detected:
        detected.append("codex")
    return detected


def choose_interactive(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def ensure_gitignore(root: Path) -> str:
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if ".ai-collab/" in existing or ".ai-collab" in existing.splitlines():
        return "unchanged"
    content = existing
    if content and not content.endswith("\n"):
        content += "\n"
    content += ".ai-collab/\n"
    atomic_write(gitignore, content)
    return "updated"


def copy_protocol(root: Path, refresh: bool = False, now: datetime | None = None) -> str:
    collab = root / ".ai-collab"
    target = collab / "PROTOCOL.md"
    candidates = [
        Path.home() / ".claude/skills/collab/references/protocol.md",
        Path(__file__).resolve().parents[1] / "references/protocol.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if target.exists():
                existing = target.read_text(encoding="utf-8")
                if existing == content:
                    return "unchanged"
                if not refresh:
                    return "unchanged"
                backup_time = isoformat_z(now or utc_now()).replace(":", "").replace("-", "")
                backup = target.with_name(f"PROTOCOL.md.bak-{backup_time}")
                atomic_write(backup, existing)
                atomic_write(target, content)
                return "updated"
            atomic_write(target, content)
            return "created"
    if target.exists():
        return "unchanged"
    atomic_write(
        target,
        "# AI Collab Protocol\n\n"
        "Before every response or analysis, read `.ai-collab/TEAM.md`, your inbox, "
        "`inbox-all.md`, recent logs, and relevant task threads/discussions.\n",
    )
    return "created"


def agent_rules_targets(agent: str) -> list[str]:
    config = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])
    return list(config["rules"])


def build_snippet(agent: str, container: str, model: str, project: str) -> str:
    display = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])["display"]
    role = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])["role"]
    inbox = f".ai-collab/inbox-{agent}.md"
    log_path = f".ai-collab/{agent}-{{YYYYMMDD-HHMMSS}}.md"
    live_report = f".ai-collab/live/{agent}.agent.json"
    live_events = f".ai-collab/live/{agent}.agent.events.jsonl"
    return f"""{START_MARKER.format(agent=agent)}
## AI Collab Protocol

You are `{agent}` ({display}) in project `{project}`.

Identity:
- agent_slug: `{agent}`
- role: `{role}`
- container: `{container or "unknown"}`
- model: `{model or "unknown"}`

Mandatory preflight before EVERY response, analysis, or tool action:
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read your direct inbox `{inbox}` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
4. Read recent task threads `.ai-collab/thread-*.md` and natural discussions `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant. Answer direct `@{agent}` mentions before unrelated work.
5. Read the latest session logs in `.ai-collab/*.md` from other agents, skipping `PROTOCOL.md`, `CONTEXT.md`, `TEAM.md`, inbox files, and your own current-session log. Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing.
6. If `.ai-collab/live/summary.json` exists, read it for current agent phases, dirty files, alerts, and open conversations before making coordination decisions.
7. Keep live observability updated in `{live_report}` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
8. After every response, create or update your session log at `{log_path}`.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: {agent}` and `claimed_at: {{ISO timestamp}}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {{ISO timestamp}}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{{task_id}}.md` when present.

Natural conversation contract:
- Use `python3 ~/.claude/ai-collab-converse.py` when you need another agent's judgement instead of hiding the question in a private log.
- Ask concrete questions with `question --to other-agent`, propose implementation options with `proposal`, record accepted choices with `decision`, and mark blockers with `blocker`.
- Mention agents explicitly with `@slug`; the daemon wakes the mentioned agent from task threads and `.ai-collab/discussions/*.md`.
- Do not edit previous messages. Correct yourself by appending a new message.

Required log frontmatter:
```yaml
---
ai: {display} ({model or "unknown model"})
agent: {agent}
container: {container or "unknown"}
model: {model or "unknown"}
session: {{YYYYMMDD-HHMMSS}}
project: {project}
updated: {{ISO timestamp}}
---
```

Required log sections:
- `## Working On`
- `## Files Read This Session`
- `## Files Modified This Session`
- `## Decisions Made`
- `## Issues Identified`
- `## Still In Progress`
- `## Do Not Touch (Avoid Conflicts)`
- `## Handoff Note`

Live observability contract:
- Before running a shell command, atomically write `{live_report}` with JSON fields: `agent`, `project`, `updated`, `phase: "command"`, `current_command`, `task_id` if any, and `files_in_scope`.
- After the command finishes, append one JSON line to `{live_events}` with: `timestamp`, `agent`, `event: "command"`, `command`, `exit_code`, and a short `output_excerpt` when available.
- Before editing files, update `{live_report}` with `phase: "editing"` and `files_in_scope`.
- When blocked, set `phase: "blocked"` and include `blocker`.
- When idle or finished, set `phase: "idle"` or `phase: "done"` with a concise `summary`.
- Use atomic writes for `{live_report}` (temp file + rename). Append-only is OK for `{live_events}`.

Write only in English or the user's language. Do not mix unrelated languages.
{END_MARKER.format(agent=agent)}
"""


def append_snippet(path: Path, agent: str, snippet: str) -> str:
    start = START_MARKER.format(agent=agent)
    end = END_MARKER.format(agent=agent)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if start in existing:
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end),
            flags=re.DOTALL,
        )
        updated = pattern.sub(snippet.rstrip(), existing, count=1)
        if updated != existing:
            if not updated.endswith("\n"):
                updated += "\n"
            atomic_write(path, updated)
            return "updated"
        return "unchanged"
    content = existing
    if content and not content.endswith("\n\n"):
        content = content.rstrip() + "\n\n"
    content += snippet.rstrip() + "\n"
    atomic_write(path, content)
    return "created" if not existing else "appended"


def read_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"agents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("agents"), list):
            return data
    except json.JSONDecodeError:
        pass
    return {"agents": []}


def write_agents_json(root: Path, agents: list[str], container: str, models: dict[str, str], rules: dict[str, list[str]], now: datetime) -> None:
    path = root / ".ai-collab" / "agents.json"
    existing = read_existing_manifest(path)
    by_agent: dict[str, dict[str, Any]] = {}
    for item in existing.get("agents", []):
        if isinstance(item, dict) and item.get("agent"):
            by_agent[str(item["agent"])] = item
    for agent in agents:
        config = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])
        previous = by_agent.get(agent, {})
        by_agent[agent] = {
            "agent": agent,
            "display": config["display"],
            "role": config["role"],
            "container": container or previous.get("container", "unknown"),
            "model": models.get(agent) or previous.get("model", "unknown"),
            "rules": rules.get(agent) or previous.get("rules", agent_rules_targets(agent)),
        }
    manifest = {
        "schema": "ai-collab.agents.v1",
        "project": root.name,
        "updated": isoformat_z(now),
        "agents": [by_agent[key] for key in sorted(by_agent.keys())],
    }
    atomic_write(path, json.dumps(manifest, indent=2, sort_keys=False) + "\n")


def write_team_md(root: Path, agents: list[str], container: str, models: dict[str, str], rules: dict[str, list[str]], now: datetime) -> None:
    path = root / ".ai-collab" / "TEAM.md"
    existing_agents: list[str] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                slug = stripped[2:].split()[0].strip("*`")
                if slug and slug not in existing_agents:
                    existing_agents.append(normalize_agent(slug))
    roster = []
    for agent in ["claude-code", *existing_agents, *agents]:
        normalized = normalize_agent(agent)
        if normalized not in roster:
            roster.append(normalized)
    timestamp = isoformat_z(now)
    lines = [
        "---",
        f"project: {root.name}",
        f"declared: {timestamp}",
        "declared_by: ai-collab project onboarding",
        "schema: ai-collab.team.v2",
        "---",
        "",
        "## Roster",
        "",
    ]
    for agent in roster:
        role = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])["role"]
        suffix = " (director)" if role == "director" else ""
        lines.append(f"- {agent}{suffix}")
    lines.extend(["", "## Agent Details", "", "| agent | role | container | model | rules |", "|---|---|---|---|---|"])
    for agent in roster:
        config = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])
        agent_rules = rules.get(agent) or agent_rules_targets(agent)
        lines.append(
            f"| {agent} | {config['role']} | {container or 'unknown'} | "
            f"{models.get(agent, 'unknown')} | {', '.join(agent_rules)} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "This manifest is agent-first. Containers are IDEs/terminals where agents are visible; models are metadata.",
            "",
        ]
    )
    atomic_write(path, "\n".join(lines))


def write_inbox_all(root: Path, agents: list[str], container: str, models: dict[str, str], now: datetime) -> str:
    path = root / ".ai-collab" / "inbox-all.md"
    if path.exists():
        return "unchanged"
    timestamp = isoformat_z(now)
    roster = ", ".join(agents)
    body = f"""---
from: ai-collab project onboarding
to: all
priority: normal
updated: {timestamp}
status: unread
---

## Welcome to AI Collab

This project has been onboarded with an agent-first protocol.

Registered agents: {roster}
Container: {container or "unknown"}
Models: {", ".join(f"{a}={models.get(a, 'unknown')}" for a in agents)}

First-response checklist:
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` and confirm your own `agent_slug`, container, model, and rule file.
3. Read your direct inbox `.ai-collab/inbox-{{your-agent-slug}}.md` and `.ai-collab/inbox-all.md`.
4. Read recent logs from other agents, relevant `thread-*.md` / `discussions/*.md`, and active `Do Not Touch` sections before answering or analyzing.
5. Write your first session log using the exact slug from TEAM.md.
6. Mark this welcome task `done` only after you have oriented yourself.
"""
    atomic_write(path, body)
    return "created"


def setup_project(
    root: Path,
    agents: list[str],
    container: str,
    models: dict[str, str],
    now: datetime | None = None,
    refresh_protocol: bool = False,
) -> dict[str, Any]:
    now = now or utc_now()
    root = root.resolve()
    collab = root / ".ai-collab"
    collab.mkdir(parents=True, exist_ok=True)
    normalized_agents: list[str] = []
    for agent in agents:
        normalized = normalize_agent(agent)
        if normalized not in AGENT_CATALOG:
            normalized = "generic" if normalized == "generic" else normalized
            AGENT_CATALOG.setdefault(normalized, {
                "display": normalized,
                "role": "worker",
                "rules": ["AGENTS.md"],
                "detect": [],
            })
        if normalized not in normalized_agents:
            normalized_agents.append(normalized)
    if "claude-code" not in normalized_agents:
        normalized_agents.insert(0, "claude-code")

    result: dict[str, Any] = {
        "root": str(root),
        "gitignore": ensure_gitignore(root),
        "protocol": copy_protocol(root, refresh=refresh_protocol, now=now),
        "rules": {},
    }
    rule_paths: dict[str, list[str]] = {}
    for agent in normalized_agents:
        snippet = build_snippet(agent, container, models.get(agent, ""), root.name)
        statuses: list[str] = []
        paths: list[str] = []
        for rel in agent_rules_targets(agent):
            status = append_snippet(root / rel, agent, snippet)
            statuses.append(f"{rel}:{status}")
            paths.append(rel)
        result["rules"][agent] = statuses
        rule_paths[agent] = paths
    write_agents_json(root, normalized_agents, container, models, rule_paths, now)
    write_team_md(root, normalized_agents, container, models, rule_paths, now)
    result["inbox_all"] = write_inbox_all(root, normalized_agents, container, models, now)
    return result


def run_interactive(root: Path, defaults: list[str]) -> tuple[list[str], str, dict[str, str]]:
    print("")
    print("AI Collab project onboarding")
    print("Tell me which IDE/container and agents this project will use.")
    print("")
    detected = ",".join(defaults)
    container = choose_interactive("IDE/container (antigravity,cursor,vscode,windsurf,terminal,other)", "terminal")
    raw_agents = choose_interactive("Agents (comma-separated)", detected)
    agents = parse_csv(raw_agents)
    models: dict[str, str] = {}
    for agent in agents:
        model = choose_interactive(f"LLM model for {agent}", "unknown")
        models[agent] = model
    return agents, container, models


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up AI Collab in the current project.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    parser.add_argument("--agents", default=None, help="Comma-separated agents, e.g. claude-code,opencode,codex")
    parser.add_argument("--container", default=None, help="IDE/container, e.g. antigravity, cursor, vscode, terminal")
    parser.add_argument("--models", default=None, help="Comma-separated agent=model pairs")
    parser.add_argument("--non-interactive", action="store_true", help="Use detected/default values without prompts.")
    parser.add_argument("--refresh-protocol", action="store_true", help="Refresh generated .ai-collab/PROTOCOL.md from the installed canonical copy, keeping a backup.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else project_root()
    defaults = detect_agents(root)
    agents = parse_csv(args.agents)
    models = parse_models(args.models)
    container = args.container or ""

    if not args.non_interactive and sys.stdin.isatty():
        agents, container, models = run_interactive(root, agents or defaults)
    else:
        agents = agents or defaults
        container = container or os.environ.get("AI_COLLAB_CONTAINER", "unknown")

    result = setup_project(root, agents, container, models, refresh_protocol=args.refresh_protocol)
    print("[AI-COLLAB] Project onboarding complete")
    print(f"  root: {result['root']}")
    for agent, statuses in result["rules"].items():
        print(f"  {agent}: {', '.join(statuses)}")
    print(f"  inbox-all: {result['inbox_all']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
