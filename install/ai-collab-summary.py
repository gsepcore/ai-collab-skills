#!/usr/bin/env python3
"""
AI Collab — Auto-generates .ai-collab/CONTEXT.md from all session logs.
Runs on every Claude session stop via Stop hook. Zero token cost.
"""
import os, sys, re, json
from pathlib import Path
from datetime import datetime, timezone

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
    if lines[0].strip() != "---":
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
    if match:
        return match.group(1).strip()
    return ""

def collect_items(text):
    """Return non-empty bullet lines from a section."""
    return [l.strip() for l in text.splitlines() if l.strip() and l.strip() not in ("-", "*")]

def main():
    root = get_project_root()
    collab_dir = root / ".ai-collab"

    if not collab_dir.exists():
        sys.exit(0)

    logs = sorted(
        [f for f in collab_dir.glob("*.md")
         if f.name not in ("PROTOCOL.md", "CONTEXT.md")],
        key=lambda f: f.stat().st_mtime
    )

    if not logs:
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
        updated = meta.get("updated", "")

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

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    project_name = root.name

    def section(title, items, fallback="—"):
        if not items:
            return f"## {title}\n{fallback}\n"
        return f"## {title}\n" + "\n".join(items) + "\n"

    context = f"""# Project Context — AI Collaboration Summary
Generated: {now}
Project: {project_name}
Active AIs: {", ".join(ais) if ais else "—"}

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
Project **{project_name}** — {len(logs)} AI session log(s) on record from: {", ".join(ais)}.
Read the sections above before touching any file. Check "Do Not Touch" first.
Run `/collab read` in Claude Code for full log details.
"""

    # Atomic write via temp file + os.replace() — prevents race conditions
    output = collab_dir / "CONTEXT.md"
    tmp = output.with_suffix(".tmp")
    tmp.write_text(context, encoding="utf-8")
    tmp.replace(output)
    print(f"[AI-COLLAB] CONTEXT.md updated — {len(logs)} logs synthesized.")

if __name__ == "__main__":
    main()
