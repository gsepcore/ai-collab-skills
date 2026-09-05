#!/usr/bin/env python3
"""Unified AI Collab install, project migration, and verification entrypoint."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
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
CLAUDE_DIR = Path(os.environ.get("AI_COLLAB_CLAUDE_DIR", str(Path.home() / ".claude"))).expanduser()
CODEX_DIR = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
REPORT_NAME = "setup-report.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
        json.dump(payload, tmp, indent=2)
        tmp.write("\n")
        temporary = Path(tmp.name)
    os.replace(temporary, path)


def project_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return Path.cwd().resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed_fingerprint() -> dict[str, str | None]:
    return {
        "claude_skill_sha256": sha256_file(CLAUDE_DIR / "skills" / "collab" / "SKILL.md"),
        "codex_skill_sha256": sha256_file(CODEX_DIR / "skills" / "collab" / "SKILL.md"),
        "setup_sha256": sha256_file(CLAUDE_DIR / "ai-collab-setup.py"),
        "project_setup_sha256": sha256_file(CLAUDE_DIR / "ai-collab-project-setup.py"),
        "visible_bridge_sha256": sha256_file(CLAUDE_DIR / "ai-collab-visible-bridge.vsix"),
    }


def manifest_defaults(root: Path) -> tuple[list[str], str, dict[str, str]]:
    manifest = read_json(root / ".ai-collab" / "agents.json")
    agents: list[str] = []
    models: dict[str, str] = {}
    containers: list[str] = []
    rows = manifest.get("agents")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            agent = str(row.get("agent") or "").strip()
            if not agent:
                continue
            if agent not in agents:
                agents.append(agent)
            model = str(row.get("model") or "").strip()
            if model and model.lower() not in {"unknown", "unknown model"}:
                models[agent] = model
            container = str(row.get("container") or "").strip()
            if container and container not in containers:
                containers.append(container)
    return agents, (containers[0] if containers else ""), models


def protected_snapshot(root: Path) -> dict[str, bytes]:
    collab = root / ".ai-collab"
    if not collab.is_dir():
        return {}
    candidates: list[Path] = []
    candidates.extend(collab.glob("inbox-*.md"))
    candidates.extend(collab.glob("thread-*.md"))
    candidates.extend(collab.glob("discussions/**/*.md"))
    candidates.extend(collab.glob("runs/**/*"))
    candidates.append(collab / "roles.json")
    snapshot: dict[str, bytes] = {}
    for path in candidates:
        if path.is_file():
            snapshot[str(path.relative_to(root))] = path.read_bytes()
    return dict(sorted(snapshot.items()))


def protected_changes(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    def compatible_role_migration(path: str, old: bytes, new: bytes) -> bool:
        if path != ".ai-collab/roles.json":
            return False
        try:
            old_data = json.loads(old)
            new_data = json.loads(new)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        old_assignments = old_data.get("assignments", {}) if isinstance(old_data, dict) else {}
        new_assignments = new_data.get("assignments", {}) if isinstance(new_data, dict) else {}
        if not isinstance(old_assignments, dict) or not isinstance(new_assignments, dict):
            return False
        return all(
            role in new_assignments
            and isinstance(old_item, dict)
            and isinstance(new_assignments[role], dict)
            and old_item.get("primary") == new_assignments[role].get("primary")
            for role, old_item in old_assignments.items()
        )
    return sorted(
        path
        for path, content in before.items()
        if path not in after or (
            after[path] != content
            and not after[path].startswith(content)
            and not compatible_role_migration(path, content, after[path])
        )
    )


def protected_appends(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(
        path
        for path, content in before.items()
        if path in after and after[path] != content and after[path].startswith(content)
    )


def resolve_installer(source: str | None, timeout: float) -> tuple[Path, bool]:
    source_value = source or os.environ.get("AI_COLLAB_SETUP_SOURCE", "")
    if source_value:
        candidate = Path(source_value).expanduser().resolve()
        if candidate.is_dir():
            candidate = candidate / "install" / "install.sh"
        if not candidate.is_file():
            raise FileNotFoundError(f"installer not found: {candidate}")
        return candidate, False

    request = urllib.request.Request(f"{RAW_BASE}/install/install.sh", headers={"User-Agent": "ai-collab-setup/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"could not download current installer: {exc}") from exc
    descriptor, name = tempfile.mkstemp(prefix="ai-collab-install-", suffix=".sh")
    os.close(descriptor)
    path = Path(name)
    path.write_bytes(content)
    return path, True


def run_visible(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    print(f"[AI-COLLAB SETUP] $ {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=str(cwd), env=env, check=False)
    return completed.returncode


def reinstall_global(root: Path, source: str | None, timeout: float) -> dict[str, Any]:
    installer: Path | None = None
    temporary = False
    try:
        installer, temporary = resolve_installer(source, timeout)
        env = os.environ.copy()
        env["AI_COLLAB_YES"] = "1"
        env["AI_COLLAB_SKIP_PROJECT_SETUP"] = "1"
        returncode = run_visible(["bash", str(installer)], cwd=root, env=env)
        return {
            "status": "updated" if returncode == 0 else "failed",
            "returncode": returncode,
            "source": str(installer),
        }
    except (FileNotFoundError, RuntimeError) as exc:
        return {"status": "failed", "reason": str(exc)}
    finally:
        if temporary and installer is not None:
            installer.unlink(missing_ok=True)


def build_project_command(
    helper: Path,
    root: Path,
    agents: list[str],
    container: str,
    models: dict[str, str],
    non_interactive: bool,
) -> list[str]:
    command = [sys.executable, str(helper), "--root", str(root), "--refresh-protocol"]
    if agents:
        command.extend(["--agents", ",".join(agents)])
    if container:
        command.extend(["--container", container])
    if models:
        command.extend(["--models", ",".join(f"{agent}={model}" for agent, model in models.items())])
    if non_interactive:
        command.append("--non-interactive")
    return command


def verify_project(root: Path, expected_agents: list[str]) -> dict[str, Any]:
    required = [
        ".ai-collab/PROTOCOL.md",
        ".ai-collab/TEAM.md",
        ".ai-collab/agents.json",
        ".ai-collab/capabilities.json",
        ".ai-collab/inbox-all.md",
        ".ai-collab/roles.json",
    ]
    missing = [path for path in required if not (root / path).is_file()]
    agents_manifest = read_json(root / ".ai-collab" / "agents.json")
    capability_manifest = read_json(root / ".ai-collab" / "capabilities.json")
    registered = {
        str(row.get("agent"))
        for row in agents_manifest.get("agents", [])
        if isinstance(row, dict) and row.get("agent")
    }
    capable = {
        str(row.get("agent"))
        for row in capability_manifest.get("agents", [])
        if isinstance(row, dict) and row.get("agent")
    }
    absent_agents = sorted(agent for agent in expected_agents if agent not in registered or agent not in capable)
    missing_identity = sorted(
        str(row.get("agent")) for row in agents_manifest.get("agents", [])
        if isinstance(row, dict) and row.get("agent") and not row.get("agent_id")
    )
    return {
        "status": "ok" if not missing and not absent_agents and not missing_identity else "failed",
        "missing_files": missing,
        "missing_agent_capabilities": absent_agents,
        "registered_agents": sorted(registered),
        "missing_agent_ids": missing_identity,
    }


def run_role_onboarding(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    helper = CLAUDE_DIR / "ai-collab-team.py"
    if not helper.is_file() and args.installer_source:
        source = Path(args.installer_source).expanduser().resolve()
        source_root = source if source.is_dir() else source.parent.parent
        candidate = source_root / "install" / "ai-collab-team.py"
        if candidate.is_file():
            helper = candidate
    if not helper.is_file():
        return {"status": "failed", "reason": "team onboarding helper is not installed"}
    assignments = list(getattr(args, "assign", None) or [])
    roles_exist = (root / ".ai-collab" / "roles.json").is_file()
    if assignments:
        command = [sys.executable, str(helper), "--root", str(root), "configure", "--non-interactive", "--replace"]
        for assignment in assignments:
            command.extend(["--assign", assignment])
    elif roles_exist:
        command = [sys.executable, str(helper), "--root", str(root), "configure", "--non-interactive"]
    elif not getattr(args, "non_interactive", False) and sys.stdin.isatty():
        command = [sys.executable, str(helper), "--root", str(root), "configure"]
    else:
        # ai-collab-team.py applies its canonical claude-code/opencode/codex
        # default automatically when the registered roster matches exactly
        # (see default_assignments_for_roster) -- try a plain non-interactive
        # configure before giving up and demanding manual --assign flags.
        command = [sys.executable, str(helper), "--root", str(root), "configure", "--non-interactive"]
        returncode = run_visible(command, cwd=root)
        if returncode == 0:
            return {"status": "configured", "returncode": returncode, "command": command}
        manifest = read_json(root / ".ai-collab" / "agents.json")
        pending = {
            "schema": "ai-collab.role-onboarding.v1",
            "status": "required",
            "agents": [row for row in manifest.get("agents", []) if isinstance(row, dict)],
            "message": "Role onboarding is mandatory. Rerun /collab setup interactively or pass --assign role=agent for every role.",
        }
        atomic_write_json(root / ".ai-collab" / "role-onboarding.json", pending)
        return pending
    returncode = run_visible(command, cwd=root)
    return {"status": "configured" if returncode == 0 else "failed", "returncode": returncode, "command": command}


def capability_ack_status(root: Path, expected_agents: list[str]) -> dict[str, Any]:
    capabilities = read_json(root / ".ai-collab" / "capabilities.json")
    catalog = capabilities.get("capability_catalog") if isinstance(capabilities, dict) else {}
    onboarding = capabilities.get("capability_onboarding") if isinstance(capabilities, dict) else {}
    if not isinstance(catalog, dict) or not isinstance(onboarding, dict):
        return {"status": "failed", "reason": "capability catalog/onboarding policy is missing", "missing": expected_agents}
    digest = str(catalog.get("digest") or "")
    relative = str(onboarding.get("thread") or "")
    if not digest or not relative:
        return {"status": "failed", "reason": "capability digest/onboarding thread is missing", "missing": expected_agents}
    path = root / relative
    acknowledged: set[str] = set()
    manifest = read_json(root / ".ai-collab" / "agents.json")
    agent_ids = {
        str(row.get("agent")): str(row.get("agent_id") or "")
        for row in manifest.get("agents", [])
        if isinstance(row, dict) and row.get("agent")
    }
    feature_ids = [
        str(item.get("id"))
        for item in catalog.get("features", [])
        if isinstance(item, dict) and item.get("id")
    ]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    sections = re.split(r"(?m)^## [^\n]+ -- ([\w-]+)\s*$", text)
    for index in range(1, len(sections), 2):
        author = sections[index].strip()
        body = sections[index + 1] if index + 1 < len(sections) else ""
        expected_agent_id = agent_ids.get(author, "")
        session_match = re.search(r"(?mi)^session_id:\s*(ses_[a-zA-Z0-9_]+)\s*$", body)
        session_record = read_json(
            root / ".ai-collab" / "live" / "sessions" / f"{session_match.group(1)}.json"
        ) if session_match else {}
        identity_matches = bool(
            expected_agent_id
            and session_match
            and re.search(rf"(?mi)^agent_id:\s*{re.escape(expected_agent_id)}\s*$", body)
            and session_record.get("agent_id") == expected_agent_id
            and session_record.get("agent") == author
            and session_record.get("project_id") == manifest.get("project_id")
        )
        features_match = all(feature_id in body for feature_id in feature_ids)
        if (
            re.search(rf"(?mi)^capability_ack:\s*{re.escape(digest)}\s*$", body)
            and re.search(r"(?mi)^automatic_use:\s*enabled\s*$", body)
            and identity_matches
            and features_match
        ):
            acknowledged.add(author)
    missing = sorted(agent for agent in expected_agents if agent not in acknowledged)
    return {
        "status": "confirmed" if not missing else "awaiting-agent-acknowledgements",
        "digest": digest,
        "thread": relative,
        "acknowledged": sorted(acknowledged & set(expected_agents)),
        "missing": missing,
    }


def queued_capability_agents(root: Path, digest: str, relative_thread: str) -> set[str]:
    if not digest or not relative_thread:
        return set()
    path = root / relative_thread
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    queued: set[str] = set()
    sections = re.split(r"(?m)^## [^\n]+ -- ([\w-]+)\s*$", text)
    for index in range(1, len(sections), 2):
        author = sections[index].strip().casefold()
        body = sections[index + 1] if index + 1 < len(sections) else ""
        if author != "ai-collab-setup" or f"capability_ack: {digest}" not in body:
            continue
        for match in re.finditer(r"(?mi)^to:\s*(.+)$", body):
            queued.update(item.strip().casefold() for item in match.group(1).split(",") if item.strip())
    return queued


def run_capability_onboarding(root: Path, expected_agents: list[str], args: argparse.Namespace) -> dict[str, Any]:
    before = capability_ack_status(root, expected_agents)
    missing = list(before.get("missing") or [])
    caller = str(getattr(args, "actor", "") or os.environ.get("AI_COLLAB_AGENT", "")).strip()
    if before.get("status") == "failed" or not missing:
        return before
    already_queued = queued_capability_agents(root, str(before.get("digest") or ""), str(before.get("thread") or ""))
    retry = bool(getattr(args, "retry_capability_onboarding", False))
    wake_targets = [
        agent for agent in missing
        if agent != caller and (retry or agent.casefold() not in already_queued)
    ]
    thread_exists = bool(before.get("thread") and (root / str(before["thread"])).is_file())
    if thread_exists and not wake_targets:
        return {
            **before,
            "dispatch": "already-queued" if already_queued else "awaiting-caller-acknowledgement",
            "queued_agents": sorted(already_queued & set(expected_agents)),
        }
    helper = CLAUDE_DIR / "ai-collab-converse.py"
    if not helper.is_file() and args.installer_source:
        source = Path(args.installer_source).expanduser().resolve()
        source_root = source if source.is_dir() else source.parent.parent
        candidate = source_root / "install" / "ai-collab-converse.py"
        if candidate.is_file():
            helper = candidate
    if not helper.is_file():
        return {**before, "dispatch": "failed", "reason": "conversation helper is not installed"}
    digest = str(before.get("digest") or "")
    feature_ids = [
        str(item.get("id"))
        for item in read_json(root / ".ai-collab" / "capabilities.json")
        .get("capability_catalog", {})
        .get("features", [])
        if isinstance(item, dict) and item.get("id")
    ]
    message = (
        "Automatic capability onboarding after install/update. Read your complete managed Collab rules, "
        "`.ai-collab/capabilities.json`, `.ai-collab/TEAM.md`, and `.ai-collab/roles.json`. Then respond "
        "in this same thread without waiting for the user. Your own reply must contain:\n"
        f"capability_ack: {digest}\n"
        "agent_id: <your registered agent_id>\n"
        "session_id: <your current session_id>\n"
        f"understood_features: {', '.join(feature_ids)}\n"
        "automatic_use: enabled\n"
        "Do not merely claim prompt delivery; append the acknowledgement yourself."
    )
    command = [
        sys.executable,
        str(helper),
        "--root",
        str(root),
        "start",
        "--author",
        "ai-collab-setup",
        "--topic",
        "Automatic Collab capability onboarding",
        "--discussion-id",
        f"capability-onboarding-{digest}",
        "--to",
        ",".join(wake_targets),
        "--type",
        "question",
        "--tags",
        "setup,capability-onboarding",
        "--message",
        message,
        "--internal-wait-seconds",
        "-1",
        "--wait-seconds",
        "0",
        "--visual-mode",
        "observe",
    ]
    # Default to queue-only unconditionally. The old logic only forced
    # queue-only when there was nothing to wake (`not wake_targets`) -- the
    # one case that's a safe no-op either way -- and dispatched for real
    # exactly when there WAS someone to wake, which is the case that matters.
    # For an agent whose only registered route is visible chat (e.g. Codex
    # under container=antigravity), "dispatch for real" means launching that
    # IDE from scratch if it isn't already open. Confirmed live: running
    # `/collab setup` on a project with antigravity-only agents popped a new
    # Antigravity window as a pure side effect of onboarding, with no human
    # asking for or watching that window. The installed daemon already
    # delivers queued onboarding messages safely (daemon-context guards
    # already prevent it from popping windows); setup itself should never
    # need to force a live visible wake as a side effect of running a CLI
    # command. AI_COLLAB_SETUP_ONBOARDING_IMMEDIATE_WAKE is the explicit,
    # opt-in escape hatch for a caller that really does want it.
    immediate_wake = os.environ.get("AI_COLLAB_SETUP_ONBOARDING_IMMEDIATE_WAKE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not immediate_wake:
        command.append("--queue-only")
    returncode = run_visible(command, cwd=root)
    after = capability_ack_status(root, expected_agents)
    after["dispatch"] = (
        "queued-for-daemon" if not immediate_wake and returncode == 0
        else "started" if returncode == 0
        else "partially-failed"
    )
    after["returncode"] = returncode
    after["command"] = command
    return after


def run_unified_setup(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = Path(args.root).expanduser().resolve() if args.root else project_root()
    started = utc_now()
    existing_project = (root / ".ai-collab").is_dir()
    existing_agents, existing_container, existing_models = manifest_defaults(root)
    requested_agents = [item.strip() for item in (args.agents or "").split(",") if item.strip()]
    requested_models: dict[str, str] = {}
    for item in (args.models or "").split(","):
        if "=" in item:
            agent, model = item.split("=", 1)
            if agent.strip() and model.strip():
                requested_models[agent.strip()] = model.strip()
    agents = requested_agents or existing_agents
    if not agents and args.actor:
        agents = [args.actor.strip()]
    container = args.container or existing_container
    models = requested_models or existing_models
    before_fingerprint = installed_fingerprint()

    report: dict[str, Any] = {
        "schema": "ai-collab.setup.v2",
        "started": isoformat_z(started),
        "root": str(root),
        "mode": "migration" if existing_project else "new-project",
        "global_install": {"status": "skipped"},
        "project_migration": {},
        "doctor": {"status": "skipped"},
        "preservation": {},
        "installed_before": before_fingerprint,
    }
    exit_code = 0

    if not args.skip_global_install:
        report["global_install"] = reinstall_global(root, args.installer_source, args.timeout)
        if report["global_install"].get("status") != "updated":
            exit_code = 1

    # The global installer is explicitly prevented from onboarding this project.
    # Snapshot immediately before project migration so normal agent messages that
    # arrive during the global reinstall do not become false preservation failures.
    before_protected = protected_snapshot(root)

    helper = CLAUDE_DIR / "ai-collab-project-setup.py"
    if not helper.is_file() and args.installer_source:
        source = Path(args.installer_source).expanduser().resolve()
        source_root = source if source.is_dir() else source.parent.parent
        fallback = source_root / "install" / "ai-collab-project-setup.py"
        if fallback.is_file():
            helper = fallback
    if helper.is_file():
        command = build_project_command(helper, root, agents, container, models, args.non_interactive)
        returncode = run_visible(command, cwd=root)
        report["project_migration"] = {
            "status": "updated" if returncode == 0 else "failed",
            "returncode": returncode,
            "command": command,
        }
        if returncode != 0:
            exit_code = 1
    else:
        report["project_migration"] = {"status": "failed", "reason": "project setup helper is not installed"}
        exit_code = 1

    report["role_onboarding"] = run_role_onboarding(root, args)
    if report["role_onboarding"].get("status") not in {"configured"}:
        exit_code = 2 if report["role_onboarding"].get("status") == "required" else 1

    expected_agents = manifest_defaults(root)[0]
    report["project_verification"] = verify_project(root, expected_agents)
    if report["project_verification"]["status"] != "ok":
        exit_code = 1

    report["capability_onboarding"] = run_capability_onboarding(root, expected_agents, args)
    if report["capability_onboarding"].get("status") != "confirmed" and exit_code == 0:
        exit_code = 3

    after_protected = protected_snapshot(root)
    changed_protected = protected_changes(before_protected, after_protected)
    appended_protected = protected_appends(before_protected, after_protected)
    report["preservation"] = {
        "status": "ok" if not changed_protected else "failed",
        "checked_existing_files": len(before_protected),
        "changed_existing_files": changed_protected,
        "concurrent_append_files": appended_protected,
    }
    if changed_protected:
        exit_code = 1

    if not args.skip_doctor:
        doctor = CLAUDE_DIR / "ai-collab-doctor.py"
        if doctor.is_file():
            env = os.environ.copy()
            env["AI_COLLAB_DOCTOR_STRICT"] = "1"
            returncode = run_visible([sys.executable, str(doctor)], cwd=root, env=env)
            report["doctor"] = {"status": "ok" if returncode == 0 else "failed", "returncode": returncode}
            if returncode != 0:
                exit_code = 1
        else:
            report["doctor"] = {"status": "failed", "reason": "doctor is not installed"}
            exit_code = 1

    report["installed_after"] = installed_fingerprint()
    report["agent_refresh"] = {
        "managed_rules_refreshed": report["project_migration"].get("status") == "updated",
        "capability_catalog_in_every_turn": False,
        "capability_digest_in_every_turn": True,
        "capability_digest": report.get("capability_onboarding", {}).get("digest", ""),
        "agent_acknowledgements_confirmed": report.get("capability_onboarding", {}).get("status") == "confirmed",
        "ide_window_reload_recommended": not args.skip_global_install,
    }
    report["finished"] = isoformat_z(utc_now())
    report["status"] = (
        "ok" if exit_code == 0
        else "awaiting-agent-acknowledgements" if exit_code == 3
        else "partial"
    )
    report_path = root / ".ai-collab" / REPORT_NAME
    atomic_write_json(report_path, report)
    print(json.dumps(report, indent=2))
    print(f"[AI-COLLAB SETUP] Report saved: {report_path}")
    return report, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install/update AI Collab, migrate this project, and verify the result.")
    parser.add_argument("--root", default=None, help="Project root. Defaults to git root or cwd.")
    parser.add_argument("--agents", default=None, help="Comma-separated agents. Existing manifest is preserved by default.")
    parser.add_argument("--container", default=None, help="IDE/container. Existing manifest is preserved by default.")
    parser.add_argument("--models", default=None, help="Comma-separated agent=model pairs.")
    parser.add_argument("--assign", action="append", default=[], help="Role assignment role=agent; repeat to complete onboarding non-interactively.")
    parser.add_argument("--actor", default=os.environ.get("AI_COLLAB_AGENT", ""), help="Agent running setup; it self-acknowledges without waking its own visible chat.")
    parser.add_argument("--retry-capability-onboarding", action="store_true", help="Explicitly resend onboarding to active agents that were already queued for this capability digest.")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt during project migration.")
    parser.add_argument("--installer-source", default=None, help="Local repository or install.sh path; default downloads current main.")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("AI_COLLAB_SETUP_TIMEOUT", "30")))
    parser.add_argument("--skip-global-install", action="store_true", help="Migrate only; intended for offline recovery and tests.")
    parser.add_argument("--skip-doctor", action="store_true", help="Skip final global health diagnosis.")
    args = parser.parse_args(argv)
    _report, returncode = run_unified_setup(args)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
