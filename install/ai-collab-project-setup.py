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
import hashlib
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
    "claude-code-ide": {
        "display": "Claude Code native IDE chat",
        "role": "worker",
        "rules": [".ai-collab/rules/claude-code-ide.md"],
        "detect": [],
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
    "claude-ide": "claude-code-ide",
    "claude_code_ide": "claude-code-ide",
    "cursor": "cursor-native",
    "windsurf": "windsurf-native",
    "copilot": "copilot-chat",
    "vscode": "copilot-chat",
    "antigravity": "codex",
    "kimi-code": "kimi",
    "kilo-code": "kilo",
}

CONTAINER_CHOICES = ["antigravity", "cursor", "vscode", "windsurf", "terminal", "zed", "jetbrains", "other"]

DEFAULT_INTERNAL_GRACE_SECONDS = max(0, int(os.environ.get("AI_COLLAB_INTERNAL_GRACE_SECONDS", "15")))
DEFAULT_SLEEP_THRESHOLD_SECONDS = max(1, int(os.environ.get("AI_COLLAB_DIRECTOR_SLEEP_SECONDS", "60")))
CAPABILITY_CATALOG_SCHEMA = "ai-collab.features.v1"
CAPABILITY_FEATURES: list[dict[str, str]] = [
    {"id": "always-on-intent-routing", "use": "Infer and run the appropriate Collab workflow without requiring a slash command."},
    {"id": "shared-context-preflight", "use": "Read team context, capabilities, roles, inboxes, relevant threads, locks, and live state each turn."},
    {"id": "stable-identity-sessions", "use": "Address one project_id + agent_id and a fresh session_id/surface_id for each runtime."},
    {"id": "role-onboarding-routing", "use": "Route work through persistent development-team roles; explicit assignments override defaults."},
    {"id": "internal-inboxes", "use": "Assign and claim durable direct or broadcast work with an explicit lifecycle."},
    {"id": "shared-conversations", "use": "Keep questions, debate, decisions, blockers, reviews, and handoffs in one canonical thread."},
    {"id": "directed-orchestration", "use": "Plan, delegate, monitor, validate, and finalize multi-agent implementation runs."},
    {"id": "visible-wake-fallback", "use": "Use internal delivery first for non-Codex agents, then exact visible chat fallback; Codex visible chat is immediate."},
    {"id": "visual-eyes", "use": "Capture screenshots, OCR, surfaces, PID/TTY, ports, and visual rosters continuously; observe by default and gate only in strict audit mode."},
    {"id": "live-observer", "use": "Expose current phases, commands, tasks, alerts, conversations, health, and screenshots."},
    {"id": "conflict-avoidance-handoffs", "use": "Respect file boundaries and Do Not Touch locks; publish progress, blockers, completion, and handoffs."},
    {"id": "recovery-self-update", "use": "Restore context/wakeup state after restarts and refresh installed helpers and managed project rules."},
    {"id": "setup-migration", "use": "Install or migrate idempotently while preserving roles, inboxes, tasks, discussions, and user-authored rules."},
    {"id": "truthful-evidence", "use": "Distinguish queued, submitted, responded, started, visually verified, and completed states."},
    {"id": "proactive-peer-review", "use": "Agent-initiated review wakes to the nearest related role owner when non-trivial work closes, backed by a daemon safety net; a non-negotiable timeout-no-response rule keeps this from blocking on a silent peer."},
    {"id": "auto-debate-on-multi-role", "use": "A request spanning 2+ role owners converges on an implementation plan through a bounded debate before anyone executes; only an explicit user override skips straight to orchestrate."},
]


def capability_catalog() -> dict[str, Any]:
    canonical = json.dumps(CAPABILITY_FEATURES, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return {
        "schema": CAPABILITY_CATALOG_SCHEMA,
        "digest": f"cap_{digest}",
        "features": CAPABILITY_FEATURES,
    }


def visible_adapter_for(agent: str, container: str = "") -> str:
    if agent in {"claude-code", "opencode", "aider", "hermes", "kimi", "kilo", "generic"}:
        return "ide-terminal-visible"
    if agent in {"claude-code-ide", "cursor-native", "windsurf-native", "copilot-chat"}:
        return "ide-native-chat"
    if agent == "codex":
        # antigravity-chat only makes sense when codex is actually registered
        # in Antigravity IDE. A project can register codex under a different
        # container (e.g. "vscode") while Antigravity runs unrelated to it on
        # the same machine -- targeting antigravity-chat there would reuse/pop
        # the WRONG project's window. codex-auto (headless `codex exec`)
        # needs no window at all and was validated end-to-end against a live
        # onboarding thread (2026-08-31, luisvelasquez project).
        if container and container != "antigravity":
            return "codex-auto"
        return "antigravity-chat"
    return "visible-adapter-required"


def stable_code(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def project_identity(root: Path, existing: dict[str, Any]) -> str:
    value = str(existing.get("project_id") or "").strip()
    return value or stable_code("prj", str(root.resolve()))


def agent_identity(root: Path, agent: str, previous: dict[str, Any]) -> str:
    value = str(previous.get("agent_id") or "").strip()
    return value or stable_code("agt", str(root.resolve()), agent)


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
    existing = read_existing_manifest(root / ".ai-collab" / "agents.json")
    registered = [
        normalize_agent(str(item.get("agent") or item.get("slug") or ""))
        for item in existing.get("agents", [])
        if isinstance(item, dict) and (item.get("agent") or item.get("slug"))
    ]
    if registered:
        return list(dict.fromkeys(registered))

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
    # A globally installed CLI/application only proves availability on the
    # machine, not membership in this project. Project-local rules or an
    # explicit setup/onboard request are the only safe first-run signals.
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


def build_snippet(agent: str, container: str, model: str, project: str, project_id: str, agent_id: str) -> str:
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
- project_id: `{project_id}`
- agent_id: `{agent_id}`
- session_id: generated uniquely at runtime; never reuse another session's code
- role: `{role}`
- container: `{container or "unknown"}`
- model: `{model or "unknown"}`

Always-on activation contract:
- The presence of `.ai-collab/agents.json` means collaboration is already enabled for every normal request in this project. Never wait for the user to say `collab`, name a feature, or repeat an agent-routing instruction.
- At the beginning of every user or agent turn, run this once before unrelated work: `python3 ~/.claude/ai-collab-turn.py preflight --root <project-root> --agent {agent} --prompt "<short faithful summary of the current request>"`. Treat its `required_actions` as mandatory. Reuse the returned runtime `session_id` only for this running session.
- Infer the collaboration behavior from intent: a request spanning 2+ role owners -> converge on an implementation plan through `ai-collab-debate.py` before anyone executes (non-negotiable default; only an explicit user override like "hazlo directo" skips straight to orchestrate); debate/review/opinions -> convene a discussion; another role owner -> route to that owner; vacant role -> ask the user/director to assign it; direct mention/question -> converse in the existing thread; small single-owner work -> execute directly with shared live/log state. Never bring the user a plan before the participating role owners have actually converged on it in that debate thread.
- Unread inboxes and unanswered current-thread mentions take priority over unrelated work. Do not make the user ask you to check them.
- Complete live updates, handoffs, and session logs automatically. Never tell the user to invoke a Collab feature that you can invoke yourself.
- During onboarding or after a catalog change, preflight returns the complete `capability_catalog`; read and acknowledge it once. After acknowledgement, preflight returns only its digest and feature IDs so you retain awareness without rereading identical descriptions every turn.
- If preflight says `acknowledgement_required`, read the current managed Collab rules plus `.ai-collab/capabilities.json`, then append your own acknowledgement to the specified onboarding thread before unrelated work. A changed catalog digest invalidates an older acknowledgement automatically.
- If the helper is missing or reports inactive, fall back to the context checks below and report the installation/setup defect; do not silently behave as if Collab were absent.

Context fallback only when the always-on helper is missing or returns `active: false`:
0. If the always-on turn helper did not return an active session, register this exact runtime before doing work: `python3 ~/.claude/ai-collab-session.py register --root <project-root> --agent {agent} --agent-id {agent_id} --container {container or "unknown"}`. Reuse the returned `session_id` only for this running session and include it in live reports, claims, messages, and logs.
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read `.ai-collab/capabilities.json`. Know your internal channels, visible adapter, wake policy, vision method, and every peer's supported routes before sending work. Never treat an unavailable route as successful.
4. Read `.ai-collab/roles.json` if it exists. Treat its development-team roles as the default routing policy; explicit user/director assignments override defaults.
5. Read your direct inbox `{inbox}` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
6. Read recent task threads `.ai-collab/thread-*.md` and natural discussions `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant. Answer direct `@{agent}` mentions before unrelated work.
7. Read the latest session logs in `.ai-collab/*.md` from other agents, skipping `PROTOCOL.md`, `CONTEXT.md`, `TEAM.md`, inbox files, and your own current-session log. Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing.
8. If `.ai-collab/live/summary.json` exists, read it for current agent phases, dirty files, alerts, and open conversations before making coordination decisions.
9. Read `.ai-collab/live/visual-roster.json` when it exists. For every visible conversation or assigned task, open its fresh `screenshot.path` with your native image capability before responding. If the current model cannot accept images, run `python3 ~/.claude/ai-collab-see.py --root <project> --image <screenshot> --agents <participants>` so the actual PNG pixels are processed directly; cite its SHA-256 and `direct-pixel-ocr` result. A prewritten sidecar or metadata alone is not sight. Identify your own surface and the other required agents, then cross-check the roster's project, PID, TTY, port ownership, and recent logs.
10. Keep live observability updated in `{live_report}` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
11. After every response, create or update your session log at `{log_path}`.

Development-team role contract:
- Use `.ai-collab/roles.json` to decide the default owner for work by discipline.
- One agent may own several roles. A role with `primary: null` is vacant; ask the user/director before routing that work.
- Never silently take work from another role owner. Use a task thread for cross-role questions and handoffs.
- Explicit task ownership in an inbox or directed run is authoritative even when it differs from the default role profile.
- Proactive peer review (non-negotiable, RESUMEN DE EJECUCION discussion-20260820-113730): when you close non-trivial work (2+ files affecting multiple roles, or any change to `install/`, `capabilities.json`, or `roles.json`), initiate a `review` request yourself to the nearest owner in that role's `related_roles` list via `ai-collab-converse.py` -- do not wait to be asked. Do not wait passively for someone else to notice your work; the daemon only exists as a 30s safety net if you forget.
- Cross-role audit is mandatory only for security/auth/permissions, deployment/infrastructure, and changes to `capabilities.json` or `roles.json` -- get the related role owner's sign-off in the same thread before marking that work done. For everything else, proactive review is recommended but not blocking: if the related owner does not respond in the wait window, note that explicitly in the thread and proceed.
- Scope-drift correction: any peer may flag drift with `type: blocker` at any time, non-blocking, and must state (a) what deviated, (b) the original agreed plan, (c) a proposed correction -- an alert missing those three is noise, not signal. Only the affected role's owner or the director may pause or revert work. Three or more drift alerts on the same task is a systemic pattern; escalate to the director explicitly rather than repeating the alert.
- Timeout-no-response is non-negotiable: if a role owner does not answer a review request, audit request, or drift alert within the wait window, say so explicitly in the thread and keep moving -- never block in silence waiting for a peer. This applies in particular to native-chat-only agents (e.g. Codex) whose wake depends on an attended visible window.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: {agent}` and `claimed_at: {{ISO timestamp}}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {{ISO timestamp}}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{{task_id}}.md` when present.

Natural conversation contract:
- Use `python3 ~/.claude/ai-collab-converse.py` when you need another agent's judgement instead of hiding the question in a private log.
- Ask concrete questions with `question --to other-agent`, propose implementation options with `proposal`, record accepted choices with `decision`, and mark blockers with `blocker`.
- Mention agents explicitly with `@slug`. The helper always writes a durable inbox/thread record. Codex is submitted immediately to its exact visible chat. Every other agent gets the short internal grace period from `.ai-collab/capabilities.json`, followed by mandatory exact visible-chat fallback if it does not claim/respond.
- For any agent whose primary delivery is visible-chat (today: Codex, or any agent marked `native_chat_only` in `capabilities.json`), writing directly into that agent's own visible session is its standard, only wake path -- already pre-authorized by the project's collaboration setup, not a new or risky action. Trigger it immediately when that agent needs to be reached; never pause to ask the user for permission first.
- Keep delivery states distinct: `queued-internally`, `internal-response`, `escalating-visible`, `submitted-visibly`, `responded`, `failed`. A timeout or prompt submission is never a response.
- When you finish work, need a decision, discover a blocker, or have material progress, append it to the shared thread/log immediately. If the director is sleeping or stale according to its live state, use the helper to wake the director through the visible route declared in `capabilities.json`; for Codex native chat, visible-chat delivery is the only wake evidence that counts.
- Continue the exchange until the implementation is complete: questions, answers, progress reports, review requests, blockers, decisions, and handoffs belong in the same task thread so the user can follow a fluid conversation.
- When a visible collaboration prompt reaches you, read the entire referenced thread and append your own substantive opinion, risks, or recommendation to that same thread before unrelated work. Mention the director and any agent whose response you need.
- Keep the visual eyes active for visible turns. In the default `observe` mode, inspect fresh screenshot/roster evidence when available and report ambiguity without invalidating a durable message or agent-authored reply. In explicit `strict` audit mode, inspect the actual PNG with native vision or the direct-pixel helper and require `visual_evidence:` plus `visible_peers:`; a mismatch blocks only that strict visual claim.
- If you are the director and the user asks the team to execute work, begin with `ai-collab-orchestrate.py convene`; require a real thread reply from every requested participant before presenting their opinions or assigning implementation tasks.
- The director must keep pre-turn and post-turn visual observations enabled. Require both proofs to pass only when the user requests a visible verification/audit or `--visual-mode strict`; normal collaboration continues from durable identity and agent-authored replies while visual ambiguity is surfaced as a warning.
- Apply surface-specific identity evidence. Terminal agents require one exact project PID/TTY and their own listening port when applicable. An IDE-native chat has no invented child PID or port: verify the captured window PID is an ancestor-host of the exact project bridge, plus a position-bound top-band agent label and actual pane pixels (`registered-shared-project-host+position-bound-top-band-label`).
- Never roleplay another agent or claim it started, reviewed, agreed, or completed work from an inbox write, daemon event, process listing, or prompt submission alone.
- Evidence vocabulary is strict: `queued` requires an inbox/thread on disk; `submitted visibly` requires a successful project-matched adapter result; `visually verified` requires a fresh screenshot plus verified visual roster; `responded` requires an agent-authored thread message with its visual attestation; `started` requires the agent's own inbox claim/live update; `completed` requires `status: done`, `done_at`, and an agent-authored handoff.
- If visible delivery or the required reply fails, report exactly which agent failed and stop attributing work to it. Never fall back to a hidden/headless worker for a visible team conversation.
- Do not edit previous messages. Correct yourself by appending a new message.

Required log frontmatter:
```yaml
---
ai: {display} ({model or "unknown model"})
agent: {agent}
agent_id: {agent_id}
container: {container or "unknown"}
model: {model or "unknown"}
session: {{runtime session_id}}
session_id: {{runtime session_id}}
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


def remove_snippet(path: Path, agent: str) -> str:
    if not path.exists():
        return "missing"
    start = START_MARKER.format(agent=agent)
    end = END_MARKER.format(agent=agent)
    existing = path.read_text(encoding="utf-8")
    if start not in existing:
        return "absent"
    pattern = re.compile(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n*", flags=re.DOTALL)
    updated = pattern.sub("\n", existing, count=1).strip()
    atomic_write(path, updated + ("\n" if updated else ""))
    return "removed"


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


def write_agents_json(root: Path, agents: list[str], container: str, models: dict[str, str], rules: dict[str, list[str]], now: datetime) -> dict[str, Any]:
    path = root / ".ai-collab" / "agents.json"
    existing = read_existing_manifest(path)
    previous_by_agent: dict[str, dict[str, Any]] = {}
    for item in existing.get("agents", []):
        if isinstance(item, dict) and item.get("agent"):
            previous_by_agent[str(item["agent"])] = item
    by_agent: dict[str, dict[str, Any]] = {}
    project_id = project_identity(root, existing)
    for agent in agents:
        config = AGENT_CATALOG.get(agent, AGENT_CATALOG["generic"])
        previous = previous_by_agent.get(agent, {})
        by_agent[agent] = {
            "agent": agent,
            "agent_id": agent_identity(root, agent, previous),
            "display": config["display"],
            "role": config["role"],
            "container": container or previous.get("container", "unknown"),
            "model": models.get(agent) or previous.get("model", "unknown"),
            "rules": rules.get(agent) or previous.get("rules", agent_rules_targets(agent)),
        }
    manifest = {
        "schema": "ai-collab.agents.v2",
        "project": root.name,
        "project_id": project_id,
        "updated": isoformat_z(now),
        "agents": [by_agent[key] for key in sorted(by_agent.keys())],
    }
    atomic_write(path, json.dumps(manifest, indent=2, sort_keys=False) + "\n")
    return manifest


def write_capabilities_json(root: Path, manifest: dict[str, Any], container: str, models: dict[str, str], now: datetime) -> str:
    path = root / ".ai-collab" / "capabilities.json"
    rows: list[dict[str, Any]] = []
    manifest_rows = {str(row.get("agent")): row for row in manifest.get("agents", []) if isinstance(row, dict)}
    for agent in sorted(manifest_rows):
        row = manifest_rows[agent]
        agent_container = str(row.get("container") or container or "unknown")
        codex_headless = agent == "codex" and agent_container not in {"antigravity", "unknown", ""}
        native_chat = (
            agent.endswith("-ide") or agent in {"cursor-native", "windsurf-native", "copilot-chat"}
            or (agent == "codex" and not codex_headless)
        )
        primary_delivery = "visible-chat" if agent == "codex" and not codex_headless else "internal-inbox"
        rows.append(
            {
                "agent": agent,
                "agent_id": row.get("agent_id"),
                "container": agent_container,
                "model": models.get(agent, "unknown"),
                "internal_channels": ["direct-inbox", "task-thread", "discussion", "session-log"],
                "visible": {
                    "adapter": visible_adapter_for(agent, agent_container),
                    "project_match_required": True,
                    "required_when_internal_timeout": True,
                    "required_when_sleeping": True,
                    "native_chat_only": native_chat,
                    "availability": "verify-at-runtime",
                    "delivery_is_not_response": True,
                    # RESUMEN DE EJECUCION discussion-20260817-214951: real
                    # headless CLI fallback when a visible dispatch never
                    # produces a real reply. Never for codex registered in
                    # Antigravity (or any other native-chat-only agent) --
                    # forcing a second execution path behind its back was
                    # explicitly rejected there. codex registered outside
                    # Antigravity (e.g. container=vscode) already routes
                    # natively through codex-auto above, so this flag simply
                    # follows the same native_chat_only computation.
                    "cli_fallback": not native_chat,
                },
                "delivery": {
                    "primary": primary_delivery,
                    "durable_internal_record": True,
                    "fallback": "visible-chat",
                    "target_identity": ["project_id", "agent_id", "session_id", "surface_id"],
                },
                "vision": {
                    "default_mode": "observe",
                    "eyes_enabled_for_visible_turns": True,
                    "strict_mode_available": True,
                    "strict_mode_is_blocking": True,
                    "observe_mode_is_blocking": False,
                    "method": "native-or-direct-pixel-ocr",
                },
                "wake_policy": {
                    "internal_first": agent != "codex",
                    "internal_grace_seconds": DEFAULT_INTERNAL_GRACE_SECONDS,
                    "sleep_threshold_seconds": DEFAULT_SLEEP_THRESHOLD_SECONDS,
                    "notify_before_visible_escalation": True,
                    # codex-auto (headless codex exec/ACP) carries none of the
                    # "stray window" risk that keeps hidden fallback banned for
                    # a real visible-chat agent, so it is allowed precisely
                    # when codex itself is the one running headless here.
                    "hidden_fallback_allowed": codex_headless,
                },
            }
        )
    catalog = capability_catalog()
    payload = {
        "schema": "ai-collab.capabilities.v2",
        "project": root.name,
        "project_id": manifest.get("project_id"),
        "updated": isoformat_z(now),
        "capability_catalog": catalog,
        "capability_onboarding": {
            "automatic": True,
            "continuous_turn_awareness": True,
            "acknowledgement_required_per_digest": True,
            "thread": f".ai-collab/discussions/discussion-capability-onboarding-{catalog['digest']}.md",
            "user_prompt_required": False,
        },
        "conversation_policy": {
            "delivery_order": ["internal", "wait-for-response", "notify-user", "visible-chat"],
            "continue_until_terminal_handoff": True,
            "visible_submission_is_not_response": True,
            "director_sleeping_requires_visible_wake": True,
            "internal_wake_default": True,
            "codex_visible_wake_immediate": True,
            "visible_fallback_for_every_agent": True,
        },
        "automation_policy": {
            "mode": "always-on",
            "user_invocation_required": False,
            "turn_preflight": "python3 ~/.claude/ai-collab-turn.py preflight",
            "intent_routing": {
                "multiple-role-owners": "auto-debate",
                "debate-review-opinions": "convene-discussion",
                "different-role-owner": "route-to-role-owner",
                "vacant-role": "resolve-with-user-or-director",
                "direct-mention-or-question": "converse",
                "single-owner-task": "execute-with-shared-state",
            },
            "automatic_completion_handoff": True,
            "feature_inventory_in_every_preflight": False,
            "capability_digest_in_every_preflight": True,
            "full_catalog_until_acknowledged": True,
        },
        "agents": rows,
    }
    status = "updated" if path.exists() else "created"
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=False) + "\n")
    return status


def sync_roles_json(root: Path, manifest: dict[str, Any], now: datetime) -> str:
    path = root / ".ai-collab" / "roles.json"
    if not path.exists():
        return "absent"
    try:
        roles = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        roles = {}
    if not isinstance(roles, dict):
        roles = {}
    active_rows = [row for row in manifest.get("agents", []) if isinstance(row, dict) and row.get("agent")]
    active_agents = [str(row["agent"]) for row in active_rows]
    agent_ids = {str(row["agent"]): row.get("agent_id") for row in active_rows}
    assignments = roles.get("assignments", {}) if isinstance(roles, dict) else {}
    if not isinstance(assignments, dict):
        assignments = {}
    for item in assignments.values():
        if not isinstance(item, dict):
            continue
        primary = str(item.get("primary") or "")
        if primary not in agent_ids:
            item["primary"] = None
            item["primary_agent_id"] = None
        else:
            item["primary_agent_id"] = agent_ids[primary]
    roles.update(
        {
            "schema": roles.get("schema") or "ai-collab.roles.v2",
            "project": root.name,
            "updated": isoformat_z(now),
            "agents": active_agents,
            "agent_ids": agent_ids,
            "assignments": assignments,
        }
    )
    atomic_write(path, json.dumps(roles, indent=2, sort_keys=False) + "\n")
    return "updated"


def write_team_md(root: Path, agents: list[str], container: str, models: dict[str, str], rules: dict[str, list[str]], now: datetime) -> None:
    path = root / ".ai-collab" / "TEAM.md"
    roster = []
    for agent in ["claude-code", *agents]:
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
    roles_path = root / ".ai-collab" / "roles.json"
    roles = read_existing_manifest(roles_path) if roles_path.exists() else {}
    assignments = roles.get("assignments", {}) if isinstance(roles, dict) else {}
    if isinstance(assignments, dict) and assignments:
        lines.extend(
            [
                "<!-- AI-COLLAB-ROLES-START -->",
                "## Development Team Roles",
                "",
                "Role assignments guide default task routing. An explicit user/director assignment may override them.",
                "",
                "| role | primary agent | responsibility |",
                "|---|---|---|",
            ]
        )
        for role, item in assignments.items():
            if not isinstance(item, dict):
                continue
            primary = item.get("primary") or "unassigned"
            label = item.get("label") or str(role).replace("-", " ").title()
            responsibility = str(item.get("responsibility") or "Own tasks assigned to this team role.").replace("|", "/")
            lines.append(f"| {label} (`{role}`) | {primary} | {responsibility} |")
        lines.extend(["", "<!-- AI-COLLAB-ROLES-END -->", ""])
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
3. Read `.ai-collab/capabilities.json`; identify how you send internally, when you escalate visibly, how you wake the director, and which routes are degraded.
4. Read `.ai-collab/roles.json` when present and identify your development-team responsibilities.
5. Read your direct inbox `.ai-collab/inbox-{{your-agent-slug}}.md` and `.ai-collab/inbox-all.md`.
6. Read recent logs from other agents, relevant `thread-*.md` / `discussions/*.md`, and active `Do Not Touch` sections before answering or analyzing.
7. Write your first session log using the exact slug from TEAM.md.
8. Mark this welcome task `done` only after you have oriented yourself and understood the complete team capability matrix.
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
    existing_manifest = read_existing_manifest(collab / "agents.json")
    project_id = project_identity(root, existing_manifest)
    previous_rows = {str(row.get("agent")): row for row in existing_manifest.get("agents", []) if isinstance(row, dict)}
    agent_ids = {agent: agent_identity(root, agent, previous_rows.get(agent, {})) for agent in normalized_agents}
    removed_agents = sorted(set(previous_rows) - set(normalized_agents))
    if removed_agents:
        result["removed_rules"] = {}
        for agent in removed_agents:
            statuses: list[str] = []
            previous_rules = previous_rows.get(agent, {}).get("rules", [])
            if isinstance(previous_rules, list):
                for rel in previous_rules:
                    if isinstance(rel, str):
                        statuses.append(f"{rel}:{remove_snippet(root / rel, agent)}")
            result["removed_rules"][agent] = statuses
    for agent in normalized_agents:
        snippet = build_snippet(agent, container, models.get(agent, ""), root.name, project_id, agent_ids[agent])
        statuses: list[str] = []
        paths: list[str] = []
        current_rules = agent_rules_targets(agent)
        previous_rules = previous_rows.get(agent, {}).get("rules", [])
        if isinstance(previous_rules, list):
            for rel in previous_rules:
                if isinstance(rel, str) and rel not in current_rules:
                    statuses.append(f"{rel}:{remove_snippet(root / rel, agent)}")
        for rel in current_rules:
            status = append_snippet(root / rel, agent, snippet)
            statuses.append(f"{rel}:{status}")
            paths.append(rel)
        result["rules"][agent] = statuses
        rule_paths[agent] = paths
    manifest = write_agents_json(root, normalized_agents, container, models, rule_paths, now)
    result["capabilities"] = write_capabilities_json(root, manifest, container, models, now)
    result["roles"] = sync_roles_json(root, manifest, now)
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
    parser.add_argument("--add-agents", default=None, help="Comma-separated agents to add without removing the existing roster.")
    parser.add_argument("--container", default=None, help="IDE/container, e.g. antigravity, cursor, vscode, terminal")
    parser.add_argument("--models", default=None, help="Comma-separated agent=model pairs")
    parser.add_argument("--non-interactive", action="store_true", help="Use detected/default values without prompts.")
    parser.add_argument("--refresh-protocol", action="store_true", help="Refresh generated .ai-collab/PROTOCOL.md from the installed canonical copy, keeping a backup.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else project_root()
    defaults = detect_agents(root)
    agents = parse_csv(args.agents)
    added_agents = parse_csv(args.add_agents)
    models = parse_models(args.models)
    container = args.container or ""

    interactive = not args.non_interactive and sys.stdin.isatty()
    if interactive:
        agents, container, models = run_interactive(root, agents or defaults)
    else:
        agents = agents or defaults
        container = container or os.environ.get("AI_COLLAB_CONTAINER", "unknown")

    if added_agents:
        existing = [
            normalize_agent(str(row.get("agent") or ""))
            for row in read_existing_manifest(root / ".ai-collab" / "agents.json").get("agents", [])
            if isinstance(row, dict) and row.get("agent")
        ]
        agents = list(dict.fromkeys([*existing, *agents, *added_agents]))

    result = setup_project(root, agents, container, models, refresh_protocol=args.refresh_protocol)
    print("[AI-COLLAB] Project onboarding complete")
    print(f"  root: {result['root']}")
    for agent, statuses in result["rules"].items():
        print(f"  {agent}: {', '.join(statuses)}")
    print(f"  capabilities: {result['capabilities']}")
    print(f"  roles: {result['roles']}")
    print(f"  inbox-all: {result['inbox_all']}")
    if interactive and not (root / ".ai-collab" / "roles.json").exists():
        configure_roles = choose_interactive("Configure development-team roles now? (yes/no)", "yes")
        if configure_roles.lower() not in {"n", "no"}:
            helper = Path(__file__).with_name("ai-collab-team.py")
            if helper.exists():
                subprocess.run([sys.executable, str(helper), "--root", str(root), "configure"], check=False)
            else:
                print("  team roles: helper missing; run /collab team configure after reinstalling AI Collab")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
