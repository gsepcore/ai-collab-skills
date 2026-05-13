#!/usr/bin/env python3
"""
AI Collab — Auto-generates .ai-collab/CONTEXT.md from all session logs.
Runs on every Claude session stop via Stop hook. Zero token cost.

Also detects the project's AI team roster from:
  1. `.ai-collab/TEAM.md` (explicit manifest, takes precedence)
  2. Heuristic: unique rules files in project root (.cursorrules,
     .windsurfrules, .github/copilot-instructions.md, .aider.conf.yml)
     plus any slugs present in `.ai-collab/{slug}-*.md` logs.

The detected team is rendered as a "## Team" section in CONTEXT.md so that
any AI opening this project sees the roster immediately — even on the first
session before anyone has written a log.
"""
import os, re, sys
from pathlib import Path
from datetime import datetime, timezone

# Unique rules-file → slug mapping. Each path resolves to exactly one AI.
UNIQUE_RULES_FILES = [
    (".cursorrules", "cursor"),
    (".windsurfrules", "windsurf"),
    (".github/copilot-instructions.md", "copilot"),
    (".aider.conf.yml", "aider"),
    (".aider.conf.yaml", "aider"),
]

# AIs that share AGENTS.md (ambiguous — can't tell which one(s) just from the file)
AGENTS_MD_COMPATIBLE = ["opencode", "codex", "aider", "continue", "antigravity", "hermes"]

# Special-case slugs that don't need rules files (the director itself).
# `claude` is kept as a legacy alias; new logs should use `claude-code`.
DIRECTOR_SLUGS = {"claude", "claude-code"}

SKIP_LOG_FILES = {"PROTOCOL.md", "CONTEXT.md", "TEAM.md"}
LOG_RE = re.compile(r"^([a-z][a-z0-9_-]*)-\d{8}-\d{6}\.md$")


def get_project_root():
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=os.getcwd()
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path(os.getcwd())


def parse_frontmatter(content):
    """Extract YAML frontmatter fields."""
    meta = {}
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return meta, content
    end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
    if end is None:
        return meta, content
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, "\n".join(lines[end+1:])


def extract_section(content, header):
    """Extract content under a ## header until the next ## header."""
    pattern = rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def collect_items(text):
    """Return non-empty bullet lines from a section."""
    return [l.strip() for l in text.splitlines() if l.strip() and l.strip() not in ("-", "*")]


def parse_team_manifest(team_md_path):
    """Parse `.ai-collab/TEAM.md` and return a set of slugs from the Roster section."""
    try:
        content = team_md_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    slugs = set()
    in_roster = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## roster"):
            in_roster = True
            continue
        if in_roster and stripped.startswith("##"):
            break
        if in_roster:
            m = re.match(r"^\s*-\s*\*{0,2}([a-z][a-z0-9_-]*)\*{0,2}", line)
            if m:
                slugs.add(m.group(1).lower())
    return slugs


def find_log_mtimes(collab_dir):
    """Return {slug: latest_mtime} for every {slug}-*.md log in .ai-collab/."""
    result = {}
    if not collab_dir.exists():
        return result
    for log in collab_dir.glob("*.md"):
        if log.name in SKIP_LOG_FILES or log.name.startswith("inbox-"):
            continue
        match = LOG_RE.match(log.name)
        if match:
            slug = match.group(1).lower()
        elif "-" in log.stem:
            # Backward compatibility for old test fixtures such as
            # opencode-fresh.md. Timestamped logs should use LOG_RE above so
            # hyphenated agent slugs like claude-code are preserved.
            slug = log.stem.split("-", 1)[0].lower()
        else:
            continue
        try:
            mtime = log.stat().st_mtime
        except OSError:
            continue
        if slug not in result or mtime > result[slug]:
            result[slug] = mtime
    return result


def detect_team(root, collab_dir):
    """
    Return (team, source_note).
    team: {slug: {"source": str, "last_log_mtime": float|None}}
    source_note: optional caveat string about AGENTS.md ambiguity (None if not relevant).
    """
    log_mtimes = find_log_mtimes(collab_dir)
    team = {}

    # 1. Explicit manifest takes precedence
    team_md = collab_dir / "TEAM.md"
    if team_md.exists():
        for slug in parse_team_manifest(team_md):
            team[slug] = {
                "source": "TEAM.md",
                "last_log_mtime": log_mtimes.get(slug),
            }
        # If manifest is empty (no Roster), fall through to heuristic; otherwise return
        if team:
            return team, None

    # 2. Heuristic: unique rules files
    for rel_path, slug in UNIQUE_RULES_FILES:
        if (root / rel_path).exists():
            team[slug] = {
                "source": rel_path,
                "last_log_mtime": log_mtimes.get(slug),
            }

    # 3. Heuristic: any slug with logs (catches AGENTS.md-sharing AIs unambiguously)
    for slug, mtime in log_mtimes.items():
        if slug in DIRECTOR_SLUGS:
            team[slug] = {"source": "director (skill)", "last_log_mtime": mtime}
            continue
        if slug not in team:
            source = "AGENTS.md" if (root / "AGENTS.md").exists() else "log only"
            team[slug] = {"source": source, "last_log_mtime": mtime}

    # 4. AGENTS.md note when present but no compatible logs yet
    note = None
    if (root / "AGENTS.md").exists():
        missing = [s for s in AGENTS_MD_COMPATIBLE if s not in team]
        if missing:
            note = (
                "`AGENTS.md` present — compatible with: "
                + ", ".join(missing)
                + ". They will appear above once they write their first log, "
                "or you can declare them explicitly in `.ai-collab/TEAM.md`."
            )

    return team, note


def format_relative_time(mtime, now_ts):
    """Render mtime (epoch seconds) as 'Nmin ago' / 'Nh ago' / 'Nd ago'."""
    if mtime is None:
        return "no logs yet"
    age = max(0, now_ts - mtime)
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age/60)}min ago"
    if age < 86400:
        return f"{int(age/3600)}h ago"
    return f"{int(age/86400)}d ago"


def render_team_section(team, source_note, now_ts):
    """Format the '## Team' section for CONTEXT.md."""
    if not team and not source_note:
        return (
            "## Team\n"
            "_No team members detected. Run `/collab setup` to register AIs "
            "or paste protocol snippets into rules files._\n"
        )
    lines = ["## Team"]
    for slug in sorted(team.keys()):
        info = team[slug]
        source = info["source"]
        if source == "TEAM.md":
            tag = "declared in `TEAM.md`"
        elif source == "director (skill)":
            tag = "director (Claude Code skill)"
        elif source == "log only":
            tag = "active (logs only)"
        else:
            tag = f"registered via `{source}`"
        mtime = info["last_log_mtime"]
        if mtime is None:
            activity = "no logs yet"
        else:
            activity = f"last seen {format_relative_time(mtime, now_ts)}"
        lines.append(f"- **{slug}** — {tag} · {activity}")
    if source_note:
        lines.append("")
        lines.append(source_note)
    lines.append("")
    return "\n".join(lines)


def main():
    root = get_project_root()
    collab_dir = root / ".ai-collab"

    # We continue even with no logs — Team section may still have content
    # (registered AIs via rules files or TEAM.md).
    if not collab_dir.exists():
        sys.exit(0)

    logs = sorted(
        [f for f in collab_dir.glob("*.md")
         if f.name not in ("PROTOCOL.md", "CONTEXT.md", "TEAM.md")
         and not f.name.startswith("inbox-")],
        key=lambda f: f.stat().st_mtime
    )

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    project_name = root.name

    # Team detection
    team, source_note = detect_team(root, collab_dir)

    # If neither logs nor team — nothing to write
    if not logs and not team and not source_note:
        sys.exit(0)

    ais = []
    all_working   = []
    all_decisions = []
    all_modified  = []
    all_issues    = []
    all_dnt       = []
    all_handoffs  = []

    for log in logs:
        try:
            content = log.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = parse_frontmatter(content)
        ai_name = meta.get("ai", log.stem)
        if ai_name not in ais:
            ais.append(ai_name)
        w = extract_section(body, "Working On")
        if w:
            all_working.append(f"**{ai_name}** — {w.splitlines()[0]}")
        for item in collect_items(extract_section(body, "Decisions Made")):
            entry = f"- {item}" if not item.startswith("-") else item
            if entry not in all_decisions:
                all_decisions.append(entry)
        for item in collect_items(extract_section(body, "Files Modified This Session")):
            entry = f"- {item}" if not item.startswith("-") else item
            if entry not in all_modified:
                all_modified.append(entry)
        for item in collect_items(extract_section(body, "Issues Identified")):
            entry = f"- {item}" if not item.startswith("-") else item
            if entry not in all_issues:
                all_issues.append(entry)
        for item in collect_items(extract_section(body, "Do Not Touch (Avoid Conflicts)")):
            entry = f"- {item}" if not item.startswith("-") else item
            if entry not in all_dnt:
                all_dnt.append(entry)
        h = extract_section(body, "Handoff Note")
        if h:
            all_handoffs.append(f"**{ai_name}:** {h.splitlines()[0]}")

    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def section(title, items, fallback="—"):
        if not items:
            return f"## {title}\n{fallback}\n"
        return f"## {title}\n" + "\n".join(items) + "\n"

    team_section = render_team_section(team, source_note, now_ts)

    context = f"""# Project Context — AI Collaboration Summary
Generated: {now_str}
Project: {project_name}
Active AIs: {", ".join(ais) if ais else "—"}

---

{team_section}
---

{section("What has been done", all_working)}

{section("Key Decisions", all_decisions)}

{section("Files Modified", all_modified)}

{section("Known Issues", all_issues)}

{section("Do Not Touch (Active Locks)", all_dnt)}

## Handoff Notes
{chr(10).join(all_handoffs) if all_handoffs else "—"}

---

## Context for New AI — Start Here
Project **{project_name}** — {len(logs)} AI session log(s) on record from: {", ".join(ais) if ais else "(no logs yet)"}.
Read the sections above before touching any file. Check "Do Not Touch" first, and the Team section to know who else is on the project.
Run `/collab read` in Claude Code for full log details.
"""

    output = collab_dir / "CONTEXT.md"
    tmp = output.with_suffix(".tmp")
    tmp.write_text(context, encoding="utf-8")
    tmp.replace(output)
    print(f"[AI-COLLAB] CONTEXT.md updated — {len(logs)} logs synthesized, {len(team)} team member(s).")


if __name__ == "__main__":
    main()
