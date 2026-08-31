#!/usr/bin/env python3
"""Interactive, persistent development-team roles for AI Collab."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROLE_CATALOG: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("senior-director", "Senior director", "Own architecture, planning, delegation, integration, and final decisions.", ()),
    ("frontend", "Frontend developer", "Implement client-side interfaces, state, accessibility, and frontend tests.", ("ui-ux-design", "backend")),
    ("backend", "Backend developer", "Implement APIs, services, domain logic, integrations, and backend tests.", ("database", "frontend", "security-review")),
    ("database", "Database developer", "Own schemas, migrations, queries, data integrity, and persistence performance.", ("backend", "security-review")),
    ("devops", "DevOps engineer", "Own CI/CD, environments, automation, observability, and operational readiness.", ("deployment", "security-review", "architecture-review")),
    ("qa", "QA reviewer", "Independently verify requirements, regressions, edge cases, and release readiness.", ("functional-review",)),
    ("security-review", "Security reviewer", "Review threats, dependencies, secrets, permissions, and security controls.", ("backend", "devops", "deployment")),
    ("architecture-review", "Architecture reviewer", "Review boundaries, maintainability, structure, and cross-system effects.", ("backend", "devops")),
    ("functional-review", "Functional reviewer", "Verify end-to-end behavior against the user's acceptance criteria.", ("qa",)),
    ("deployment", "Deployment owner", "Prepare, execute, verify, and if necessary roll back deployments.", ("devops", "security-review")),
    ("ui-ux-design", "UI/UX designer", "Own user flows, interaction design, visual system, responsive behavior, and handoff.", ("frontend",)),
)

ROLE_ALIASES = {
    "director": "senior-director",
    "senior": "senior-director",
    "db": "database",
    "data": "database",
    "security": "security-review",
    "architecture": "architecture-review",
    "functionality": "functional-review",
    "functional": "functional-review",
    "deploy": "deployment",
    "design": "ui-ux-design",
    "ui": "ui-ux-design",
    "ux": "ui-ux-design",
}

ROLES_START = "<!-- AI-COLLAB-ROLES-START -->"
ROLES_END = "<!-- AI-COLLAB-ROLES-END -->"
UNASSIGNED = {"", "-", "none", "null", "unassigned", "vacant"}

# Repo-wide default (Luis, 2026-08-31, luisvelasquez project): when a
# project's registered roster is exactly claude-code + opencode + codex,
# apply this split automatically instead of demanding manual --assign
# flags or leaving every role unassigned. claude-code keeps the
# orchestrating/cross-cutting roles as director; codex (validated for
# strong autonomous reasoning and real file edits via codex-auto) takes
# backend + review-style roles; opencode takes the remaining hands-on
# implementation roles. This stops applying the moment a fourth agent
# joins the roster -- it is a default for exactly this trio, not a
# general heuristic.
CANONICAL_TRIO = frozenset({"claude-code", "opencode", "codex"})
CANONICAL_TRIO_ASSIGNMENTS: dict[str, str] = {
    "senior-director": "claude-code",
    "architecture-review": "claude-code",
    "devops": "claude-code",
    "deployment": "claude-code",
    "backend": "codex",
    "security-review": "codex",
    "qa": "codex",
    "frontend": "opencode",
    "database": "opencode",
    "ui-ux-design": "opencode",
    "functional-review": "opencode",
}


def default_assignments_for_roster(roster: list[str]) -> dict[str, str] | None:
    """Canonical role split for the exact claude-code+opencode+codex trio, else None."""
    if frozenset(roster) == CANONICAL_TRIO:
        return dict(CANONICAL_TRIO_ASSIGNMENTS)
    return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    return Path.cwd().resolve()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def normalize_role(value: str) -> str:
    role = value.strip().lower().replace("_", "-").replace(" ", "-")
    return ROLE_ALIASES.get(role, role)


def registered_agents(root: Path) -> list[str]:
    result: list[str] = []
    manifest = read_json(root / ".ai-collab" / "agents.json", {})
    if isinstance(manifest, dict):
        for item in manifest.get("agents", []):
            if isinstance(item, dict) and item.get("agent"):
                slug = str(item["agent"])
                if slug not in result:
                    result.append(slug)
    team_path = root / ".ai-collab" / "TEAM.md"
    if team_path.exists():
        in_roster = False
        for line in team_path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "## Roster":
                in_roster = True
                continue
            if in_roster and line.startswith("## "):
                break
            if in_roster and line.strip().startswith("- "):
                slug = line.strip()[2:].split()[0].strip("*`")
                if slug and slug not in result:
                    result.append(slug)
    return result


def registered_identities(root: Path) -> dict[str, str]:
    identities: dict[str, str] = {}
    manifest = read_json(root / ".ai-collab" / "agents.json", {})
    if isinstance(manifest, dict):
        for item in manifest.get("agents", []):
            if isinstance(item, dict) and item.get("agent") and item.get("agent_id"):
                identities[str(item["agent"])] = str(item["agent_id"])
    return identities


def load_profile(root: Path) -> dict[str, Any]:
    data = read_json(root / ".ai-collab" / "roles.json", {})
    if isinstance(data, dict) and isinstance(data.get("assignments"), dict):
        return data
    return {"assignments": {}}


def parse_assignments(values: list[str]) -> dict[str, str | None]:
    assignments: dict[str, str | None] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid assignment '{value}'; use role=agent")
        raw_role, raw_agent = value.split("=", 1)
        role = normalize_role(raw_role)
        agent = raw_agent.strip()
        if not role:
            raise SystemExit(f"Invalid role in assignment: {value}")
        assignments[role] = None if agent.lower() in UNASSIGNED else agent
    return assignments


def role_metadata(role: str) -> tuple[str, str, tuple[str, ...]]:
    for slug, label, responsibility, related_roles in ROLE_CATALOG:
        if slug == role:
            return label, responsibility, related_roles
    label = role.replace("-", " ").title()
    return label, "Own tasks assigned to this team role.", ()


def render_roles_section(profile: dict[str, Any]) -> str:
    assignments = profile.get("assignments", {})
    lines = [
        ROLES_START,
        "## Development Team Roles",
        "",
        "Role assignments guide default task routing. An explicit user/director assignment may override them.",
        "",
        "| role | primary agent | responsibility |",
        "|---|---|---|",
    ]
    for role, item in assignments.items():
        if not isinstance(item, dict):
            continue
        primary = item.get("primary") or "unassigned"
        label = item.get("label") or role_metadata(role)[0]
        responsibility = str(item.get("responsibility") or role_metadata(role)[1]).replace("|", "/")
        related = item.get("related_roles")
        related_suffix = f" (related: {', '.join(related)})" if related else ""
        lines.append(f"| {label} (`{role}`) | {primary} | {responsibility}{related_suffix} |")
    lines.extend(["", ROLES_END])
    return "\n".join(lines)


def update_team_md(root: Path, profile: dict[str, Any]) -> None:
    path = root / ".ai-collab" / "TEAM.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else f"# Team: {root.name}\n"
    section = render_roles_section(profile)
    if ROLES_START in existing and ROLES_END in existing:
        before = existing.split(ROLES_START, 1)[0].rstrip()
        after = existing.split(ROLES_END, 1)[1].lstrip("\n")
        content = f"{before}\n\n{section}\n"
        if after:
            content += f"\n{after}"
    else:
        content = f"{existing.rstrip()}\n\n{section}\n"
    atomic_write(path, content)


def configure_team(
    root: Path,
    requested: dict[str, str | None],
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    roster = registered_agents(root)
    identities = registered_identities(root)
    if not roster:
        raise SystemExit("No registered agents. Run /collab setup before configuring team roles.")
    unknown = sorted({agent for agent in requested.values() if agent and agent not in roster})
    if unknown:
        raise SystemExit("Agent(s) not registered: " + ", ".join(unknown))

    existing = load_profile(root)
    existing_assignments = existing.get("assignments", {}) if not replace else {}
    roles = [slug for slug, _label, _responsibility, _related_roles in ROLE_CATALOG]
    for role in [*existing_assignments.keys(), *requested.keys()]:
        if role not in roles:
            roles.append(role)

    assignments: dict[str, dict[str, Any]] = {}
    for role in roles:
        previous = existing_assignments.get(role, {}) if isinstance(existing_assignments, dict) else {}
        primary = requested[role] if role in requested else (previous.get("primary") if isinstance(previous, dict) else None)
        default_label, default_responsibility, default_related_roles = role_metadata(role)
        label = previous.get("label") if isinstance(previous, dict) and previous.get("label") else default_label
        responsibility = previous.get("responsibility") if isinstance(previous, dict) and previous.get("responsibility") else default_responsibility
        related_roles = (
            previous.get("related_roles")
            if isinstance(previous, dict) and isinstance(previous.get("related_roles"), list)
            else list(default_related_roles)
        )
        assignments[role] = {
            "primary": primary,
            "primary_agent_id": identities.get(primary or "") if primary else None,
            "label": label,
            "responsibility": responsibility,
            "related_roles": related_roles,
        }

    timestamp = isoformat_z(now or utc_now())
    profile = {
        "schema": "ai-collab.roles.v2",
        "project": root.name,
        "updated": timestamp,
        "agents": roster,
        "agent_ids": identities,
        "assignments": assignments,
    }
    atomic_write(root / ".ai-collab" / "roles.json", json.dumps(profile, indent=2, sort_keys=False) + "\n")
    update_team_md(root, profile)
    return profile


def interactive_assignments(root: Path) -> dict[str, str | None]:
    roster = registered_agents(root)
    if not roster:
        raise SystemExit("No registered agents. Run /collab setup first.")
    existing = load_profile(root).get("assignments", {})
    print("\nAI Collab development-team onboarding")
    print("Registered agents:")
    for index, agent in enumerate(roster, 1):
        print(f"  {index}. {agent}")
    print("Enter an agent number/name, '-' for unassigned, or Enter to keep the shown default.\n")
    result: dict[str, str | None] = {}
    for role, label, _responsibility, _related_roles in ROLE_CATALOG:
        current = existing.get(role, {}).get("primary") if isinstance(existing, dict) else None
        default = current or "unassigned"
        answer = input(f"{label} [{default}]: ").strip()
        if not answer:
            result[role] = current
        elif answer.lower() in UNASSIGNED:
            result[role] = None
        elif answer.isdigit() and 1 <= int(answer) <= len(roster):
            result[role] = roster[int(answer) - 1]
        else:
            result[role] = answer
    return result


def print_profile(profile: dict[str, Any]) -> None:
    print(f"Team roles for {profile.get('project', 'project')}:")
    for role, item in profile.get("assignments", {}).items():
        if isinstance(item, dict):
            print(f"- {role}: {item.get('primary') or 'unassigned'}")


def cmd_configure(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    requested = parse_assignments(args.assign or [])
    if not args.non_interactive and sys.stdin.isatty():
        requested.update(interactive_assignments(root))
    elif not requested:
        if not load_profile(root).get("assignments"):
            default = default_assignments_for_roster(registered_agents(root))
            if default is None:
                raise SystemExit("Non-interactive configuration requires at least one --assign role=agent value.")
            print("[AI-COLLAB] Applying canonical claude-code/opencode/codex role split (repo default).")
            requested = default
    profile = configure_team(root, requested, replace=args.replace)
    print("[AI-COLLAB] Development-team roles saved")
    print_profile(profile)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    profile = load_profile(root)
    if not profile.get("assignments"):
        raise SystemExit("No team roles configured. Run /collab team configure.")
    print_profile(profile)
    return 0


def cmd_owner(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else project_root()
    role = normalize_role(args.role)
    item = load_profile(root).get("assignments", {}).get(role, {})
    owner = item.get("primary") if isinstance(item, dict) else None
    if not owner:
        raise SystemExit(f"Role is unassigned: {role}")
    print(owner)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure persistent development-team roles for AI Collab.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure", help="Run role onboarding and save .ai-collab/roles.json.")
    configure.add_argument("--assign", action="append", default=[], help="Role assignment such as frontend=claude-code; repeat as needed.")
    configure.add_argument("--replace", action="store_true", help="Reset roles not included in this configuration to unassigned.")
    configure.add_argument("--non-interactive", action="store_true", help="Do not prompt; require --assign values.")
    configure.set_defaults(func=cmd_configure)

    show = sub.add_parser("show", help="Show the current development-team roles.")
    show.set_defaults(func=cmd_show)

    owner = sub.add_parser("owner", help="Print the primary agent for a role.")
    owner.add_argument("--role", required=True)
    owner.set_defaults(func=cmd_owner)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
