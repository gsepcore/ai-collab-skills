#!/usr/bin/env python3
"""Build one compact, deterministic AI Collab action packet per agent turn.

The helper makes collaboration state actionable without requiring the user to
remember slash commands. It is read-only apart from refreshing the caller's
runtime session through ``ai-collab-session.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROLE_SIGNALS: dict[str, tuple[str, ...]] = {
    "frontend": ("frontend", "front-end", "react", "vue", "css", "client-side", "interfaz web"),
    "backend": ("backend", "back-end", "api", "server", "servidor", "servicio", "endpoint"),
    "database": ("database", "base de datos", "sql", "schema", "migration", "migración"),
    "devops": ("devops", "ci/cd", "pipeline", "infra", "docker", "kubernetes", "observability"),
    "qa": ("qa", "test", "prueba", "regression", "regresión", "quality"),
    "security-review": ("security", "seguridad", "vulnerability", "vulnerabilidad", "threat"),
    "architecture-review": ("architecture", "arquitectura", "design review", "system design"),
    "functional-review": ("acceptance", "criterios", "functional review", "revisión funcional"),
    "deployment": ("deploy", "deployment", "despliegue", "release", "producción", "production"),
    "ui-ux-design": ("ui", "ux", "diseño ui", "diseño ux", "visual design", "interaction design", "user flow", "wireframe"),
}

TEAM_SIGNALS = (
    "agents",
    "agentes",
    "team",
    "equipo",
    "between you",
    "entre ustedes",
    "parallel",
    "paralelo",
)
DISCUSSION_SIGNALS = (
    "debat",
    "discuss",
    "discut",
    "opinions",
    "opiniones",
    "review together",
    "revisen",
    "what do the others think",
    "qué piensan los otros",
)
# RESUMEN DE EJECUCION discussion-20260820-113730 (Luis's non-negotiable
# mandate): a request touching 2+ role owners must converge on an
# implementation plan through ai-collab-debate.py before anyone executes --
# that is the default, not something the director opts into. These signals
# are the only way to skip straight to orchestrate.
DIRECT_OVERRIDE_SIGNALS = (
    "hazlo directo",
    "hazlo tu",
    "hazlo vos",
    "sin debate",
    "no debatan",
    "you decide",
    "just do it",
    "do it directly",
    "skip the debate",
)
# Conservative signal for ai-collab-debate.py's quick mode (1 round/30s,
# director decides) vs full mode (3 rounds/10min). Default is full: getting
# this wrong toward more deliberation is cheap, getting it wrong toward
# skipping real convergence on a feature is not.
MECHANICAL_SIGNALS = (
    "rename",
    "renombrar",
    "renombra",
    "move file",
    "mover archivo",
    "mueve el archivo",
    "typo",
    "reformat",
    "formatting",
    "refactor menor",
    "trivial",
    "mecánico",
    "mecanico",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


def project_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path.cwd().resolve()


def agent_row(manifest: dict[str, Any], slug: str) -> dict[str, Any]:
    for row in manifest.get("agents", []):
        if isinstance(row, dict) and row.get("agent") == slug:
            return row
    return {}


def register_session(root: Path, slug: str, row: dict[str, Any], surface_kind: str) -> dict[str, Any]:
    local = Path(__file__).with_name("ai-collab-session.py")
    installed = Path.home() / ".claude" / "ai-collab-session.py"
    helper = local if local.is_file() else installed
    if not helper.is_file() or not row.get("agent_id"):
        return {}
    command = [
        sys.executable,
        str(helper),
        "register",
        "--root",
        str(root),
        "--agent",
        slug,
        "--agent-id",
        str(row["agent_id"]),
        "--container",
        str(row.get("container") or "unknown"),
        "--surface-kind",
        surface_kind,
        "--pid",
        str(os.getppid()),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {"error": (completed.stderr or completed.stdout).strip()}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": "session registrar returned invalid JSON"}
    return payload if isinstance(payload, dict) else {}


def role_owners(profile: dict[str, Any]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    assignments = profile.get("assignments", {})
    if not isinstance(assignments, dict):
        return result
    for role, item in assignments.items():
        if isinstance(item, dict):
            result[str(role)] = str(item["primary"]) if item.get("primary") else None
    return result


def detect_roles(prompt: str, owners: dict[str, str | None]) -> list[str]:
    normalized = f" {prompt.casefold()} "
    matches: list[str] = []
    for role, signals in ROLE_SIGNALS.items():
        if role not in owners:
            continue
        if any(contains_signal(normalized, signal) for signal in signals):
            matches.append(role)
    return matches


def contains_signal(normalized: str, signal: str) -> bool:
    value = signal.casefold()
    if len(value) <= 3 and value.replace("-", "").isalnum():
        return re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", normalized) is not None
    return value in normalized


def classify_intent(prompt: str, slug: str, owners: dict[str, str | None], registered: list[str]) -> dict[str, Any]:
    normalized = f" {prompt.casefold()} "
    roles = detect_roles(prompt, owners)
    matched_owners = sorted({owners[role] for role in roles if owners.get(role)})
    vacant_roles = sorted(role for role in roles if not owners.get(role))
    mentioned_agents = sorted(agent for agent in registered if agent != slug and f"@{agent.casefold()}" in normalized)
    asks_team = any(signal in normalized for signal in TEAM_SIGNALS)
    asks_discussion = any(signal in normalized for signal in DISCUSSION_SIGNALS)
    wants_direct = any(signal in normalized for signal in DIRECT_OVERRIDE_SIGNALS)
    multi_owner = len(matched_owners) > 1 or (asks_team and len(registered) > 1)

    debate_mode: str | None = None
    if asks_discussion or (asks_team and mentioned_agents):
        action = "convene-discussion"
        reason = "The request asks for multiple agents' judgement or debate."
    elif vacant_roles:
        action = "resolve-vacant-role"
        reason = "The request includes a configured role that has no owner."
    elif multi_owner and not wants_direct:
        action = "auto-debate"
        debate_mode = "quick" if any(signal in normalized for signal in MECHANICAL_SIGNALS) else "full"
        reason = "The request spans 2+ role owners; converge on an implementation plan before executing (non-negotiable default)."
    elif multi_owner and wants_direct:
        action = "orchestrate"
        reason = "The request spans multiple owners, but the user explicitly asked to skip debate and execute directly."
    elif matched_owners and matched_owners[0] != slug:
        action = "route-to-role-owner"
        reason = f"Configured role ownership routes this work to {matched_owners[0]}."
    elif mentioned_agents:
        action = "converse"
        reason = "The request directly mentions another registered agent."
    else:
        action = "execute-with-shared-state"
        reason = "No cross-agent routing condition was detected; keep collaboration state updated automatically."

    result = {
        "action": action,
        "reason": reason,
        "roles": roles,
        "owners": matched_owners,
        "vacant_roles": vacant_roles,
        "mentioned_agents": mentioned_agents,
        "user_must_name_collab_feature": False,
    }
    if debate_mode:
        result["debate_mode"] = debate_mode
    return result


def unread_inboxes(root: Path, slug: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for target in (f"inbox-{slug}.md", "inbox-all.md"):
        path = root / ".ai-collab" / target
        meta = parse_frontmatter(path)
        if (meta.get("status") or "").casefold() == "unread":
            result.append(
                {
                    "path": str(path.relative_to(root)),
                    "task_id": meta.get("task_id", ""),
                    "from": meta.get("from", ""),
                    "to": meta.get("to", ""),
                }
            )
    return result


def capability_awareness(
    root: Path,
    slug: str,
    expected_agent_id: str = "",
) -> dict[str, Any]:
    capabilities = read_json(root / ".ai-collab" / "capabilities.json")
    catalog = capabilities.get("capability_catalog") if isinstance(capabilities, dict) else {}
    if not isinstance(catalog, dict):
        catalog = {}
    digest = str(catalog.get("digest") or "")
    features = catalog.get("features") if isinstance(catalog.get("features"), list) else []
    onboarding = capabilities.get("capability_onboarding") if isinstance(capabilities, dict) else {}
    if not isinstance(onboarding, dict):
        onboarding = {}
    relative = str(onboarding.get("thread") or "")
    path = root / relative if relative else None
    acknowledged = False
    if path and path.is_file() and digest:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        sections = re.split(r"(?m)^## [^\n]+ -- ([\w-]+)\s*$", text)
        for index in range(1, len(sections), 2):
            author = sections[index].strip().casefold()
            body = sections[index + 1] if index + 1 < len(sections) else ""
            ack_matches = re.search(rf"(?mi)^capability_ack:\s*{re.escape(digest)}\s*$", body)
            agent_matches = not expected_agent_id or re.search(
                rf"(?mi)^agent_id:\s*{re.escape(expected_agent_id)}\s*$", body
            )
            session_match = re.search(r"(?mi)^session_id:\s*(ses_[a-zA-Z0-9_]+)\s*$", body)
            session_record = read_json(
                root / ".ai-collab" / "live" / "sessions" / f"{session_match.group(1)}.json"
            ) if session_match else {}
            session_matches = bool(
                session_match
                and session_record.get("agent") == slug
                and session_record.get("agent_id") == expected_agent_id
                and session_record.get("project_id") == capabilities.get("project_id")
            )
            automatic_matches = re.search(r"(?mi)^automatic_use:\s*enabled\s*$", body)
            features_match = all(feature_id in body for feature_id in [
                str(item.get("id")) for item in features if isinstance(item, dict) and item.get("id")
            ])
            if (
                author == slug.casefold()
                and ack_matches
                and agent_matches
                and session_matches
                and automatic_matches
                and features_match
            ):
                acknowledged = True
                break
    feature_ids = [str(item.get("id")) for item in features if isinstance(item, dict) and item.get("id")]
    return {
        "digest": digest,
        # The full descriptions are needed for onboarding. Once this agent has
        # acknowledged the digest, IDs are enough to detect changes without
        # spending tokens rereading the same catalog on every turn.
        "features": features if not acknowledged else [],
        "feature_ids": feature_ids,
        "thread": relative,
        "thread_exists": bool(path and path.is_file()),
        "acknowledged": acknowledged,
        "acknowledgement_required": bool(digest and not acknowledged),
        "continuous_turn_awareness": bool(onboarding.get("continuous_turn_awareness", True)),
    }


def direct_mentions(
    root: Path,
    slug: str,
    limit: int = 10,
    max_age_seconds: float | None = None,
) -> list[str]:
    if max_age_seconds is None:
        # The daemon/wakeup path owns durable peer delivery. Turn preflight only
        # surfaces recent unresolved mentions so yesterday's open discussion
        # cannot hijack a new user request.
        max_age_seconds = float(os.environ.get("AI_COLLAB_TURN_MENTION_MAX_AGE_SECONDS", "3600"))
    candidates = [*root.glob(".ai-collab/thread-*.md"), *root.glob(".ai-collab/discussions/*.md")]
    candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    result: list[str] = []
    pattern = re.compile(rf"(?<![\w-])@{re.escape(slug)}(?![\w-])", re.IGNORECASE)
    for path in candidates[:50]:
        if max_age_seconds >= 0 and time.time() - path.stat().st_mtime > max_age_seconds:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_frontmatter(path)
        if (meta.get("status") or "").casefold() == "closed":
            continue
        sections = re.split(r"(?m)^## [^\n]+ -- ([\w-]+)\s*$", text)
        if len(sections) < 3:
            continue
        author = sections[-2].strip().casefold()
        latest = sections[-1]
        recipients: list[str] = []
        for line in latest.splitlines()[:8]:
            if line.casefold().startswith("to:"):
                recipients = [item.strip().casefold() for item in line.split(":", 1)[1].split(",")]
                break
        if author != slug.casefold() and (slug.casefold() in recipients or pattern.search(latest)):
            result.append(str(path.relative_to(root)))
        if len(result) >= limit:
            break
    return result


def action_commands(action: str, debate_mode: str | None = None) -> list[str]:
    if action == "convene-discussion":
        return ["Use ai-collab-orchestrate.py convene or ai-collab-converse.py before answering for peers."]
    if action == "auto-debate":
        params = "--rounds 1 --wait-seconds 30" if debate_mode == "quick" else "--rounds 3 --wait-seconds 600"
        return [
            f"Run ai-collab-debate.py run {params} with the matched role owners before executing anything. "
            "Only bring the user a converged RESUMEN DE EJECUCION for authorization -- never before that."
        ]
    if action == "orchestrate":
        return ["Initialize/convene a directed run and route tasks through roles.json automatically."]
    if action == "route-to-role-owner":
        return ["Create a task thread/inbox for the configured owner; do not silently take its work."]
    if action == "resolve-vacant-role":
        return ["Ask the user or senior director to assign the vacant role before executing or delegating its work."]
    if action == "converse":
        return ["Use ai-collab-converse.py and keep the exchange in one shared thread."]
    return ["Execute directly, while updating live state and the session handoff without user prompting."]


def build_packet(root: Path, slug: str, prompt: str = "", surface_kind: str = "process") -> dict[str, Any]:
    collab = root / ".ai-collab"
    manifest = read_json(collab / "agents.json")
    if not manifest:
        return {
            "schema": "ai-collab.turn.v1",
            "active": False,
            "root": str(root),
            "required_action": "run-collab-setup",
            "reason": ".ai-collab/agents.json is missing or invalid.",
        }
    row = agent_row(manifest, slug)
    if not row:
        return {
            "schema": "ai-collab.turn.v1",
            "active": False,
            "root": str(root),
            "agent": slug,
            "required_action": "onboard-current-agent",
            "reason": f"{slug} is not registered in this project.",
        }

    profile = read_json(collab / "roles.json")
    owners = role_owners(profile)
    registered = [
        str(item.get("agent"))
        for item in manifest.get("agents", [])
        if isinstance(item, dict) and item.get("agent")
    ]
    intent = classify_intent(prompt, slug, owners, registered)
    inboxes = unread_inboxes(root, slug)
    mentions = direct_mentions(root, slug)
    session = register_session(root, slug, row, surface_kind)
    awareness = capability_awareness(
        root,
        slug,
        expected_agent_id=str(row.get("agent_id") or ""),
    )
    required: list[str] = []
    if inboxes:
        required.append("claim-and-execute-unread-inbox-before-unrelated-work")
    if awareness["acknowledgement_required"]:
        if awareness["thread_exists"]:
            required.append(
                "read-the-current-capability-catalog-and-managed-collab-rules-then-append-your-own-answer-to-"
                f"{awareness['thread']}-with-capability_ack:{awareness['digest']}-agent_id-and-current-session_id; "
                "do-this-autonomously-without-asking-the-user"
            )
        else:
            required.append("capability-onboarding-thread-is-missing; report-setup-incomplete-and-repair-collab-setup")
    required.extend(action_commands(intent["action"], intent.get("debate_mode")))
    if mentions:
        required.append(
            "continue-recent-direct-mentions-in-their-existing-threads-without-creating-duplicates; "
            "the current user request remains primary unless it is the same collaboration turn"
        )
    director = owners.get("senior-director")

    return {
        "schema": "ai-collab.turn.v1",
        "active": True,
        "mode": "always-on",
        "user_invocation_required": False,
        "project": root.name,
        "project_id": manifest.get("project_id"),
        "agent": slug,
        "agent_id": row.get("agent_id"),
        "session": session,
        "capability_catalog": {
            "digest": awareness["digest"],
            "features": awareness["features"],
        },
        "capability_awareness": awareness,
        "director": director,
        "role_owners": owners,
        "unread_inboxes": inboxes,
        "direct_mentions": mentions,
        "intent": intent,
        "required_actions": required,
        "completion_contract": "Update live state and session log/handoff automatically; never ask the user to invoke a collab feature.",
    }


def hook_prompt() -> str:
    try:
        raw = sys.stdin.read()
    except OSError:
        return ""
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if not isinstance(payload, dict):
        return ""
    for key in ("prompt", "user_prompt", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit the mandatory always-on AI Collab action packet for one turn.")
    parser.add_argument("preflight", nargs="?")
    parser.add_argument("--root", default=None)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--hook-stdin", action="store_true")
    parser.add_argument("--surface-kind", default="process", choices=["process", "terminal", "ide-native-chat", "api"])
    args = parser.parse_args(argv)
    prompt = hook_prompt() if args.hook_stdin else args.prompt
    packet = build_packet(project_root(args.root), args.agent, prompt, args.surface_kind)
    print("[AI-COLLAB ALWAYS-ON TURN]")
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    print("[END AI-COLLAB ALWAYS-ON TURN]")
    return 0 if packet.get("active") else 3


if __name__ == "__main__":
    raise SystemExit(main())
