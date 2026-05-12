---
name: collab
description: Enable real-time collaboration between multiple AI assistants (Claude Code, Cursor, Windsurf, Copilot, OpenCode, Codex, Gemini, etc.) working on the same project simultaneously. Use this skill when the user is working with more than one AI tool at the same time and wants them to share context, read each other's work, or avoid conflicting changes. Triggers on /collab, "what has cursor been doing", "share my context with windsurf", "what is the other AI working on", "sync with cursor", "multi-AI", "collab", "read other AI conversation", "what did the other AI do", "lee la conversacion de la otra IA", "qué hizo cursor", "comparte contexto", "sincroniza con la otra IA".
---

# AI Collab Skill

Shared filesystem protocol so every AI assistant working on the same project can read and write context in real time. No external service, no API — just a `.ai-collab/` directory inside the project.

---

## How it works

Each AI writes a Markdown log to `{project-root}/.ai-collab/`. Any AI with filesystem access to the project can read those logs. Claude manages its own log via this skill. Other AIs (Cursor, Windsurf, Codex, OpenCode, etc.) write via rules snippets added to the project once — see `references/protocol.md`.

---

## Command: /collab read

Show everything other AIs have written for this project.

**Steps:**
1. Find project root: `git rev-parse --show-toplevel 2>/dev/null || pwd`
2. Check `{root}/.ai-collab/` — if missing or empty, say so and suggest `/collab setup`
3. For each `.md` file except `PROTOCOL.md` and your own `claude-*.md` (sorted newest first):
   - Parse frontmatter: `ai`, `session`, `updated`
   - Print header: `## [AI name] — updated [timestamp] — [🟢 active / 🟡 idle / 🔴 stale]`
     - 🟢 active = modified < 1h ago
     - 🟡 idle = 1–4h ago
     - 🔴 stale = > 4h ago
   - Print full file content (strip frontmatter block)
4. End with: `[N active · M idle · K stale]`

**Edge cases:**
- If `.ai-collab/` does not exist → say "No collab directory found. Run `/collab setup` first."
- If all files are your own logs → say "No logs from other AIs yet."
- If a file has no frontmatter → show filename and raw content, note "no frontmatter"

---

## Command: /collab write [optional: brief note]

Save your current conversation context to the shared directory.

**Steps:**
1. Find project root (same as above)
2. Create `{root}/.ai-collab/` if it doesn't exist
3. Find if a `claude-*.md` file from this session already exists (modified < 4h) → update it; otherwise create `claude-{YYYYMMDD-HHMMSS}.md`
4. Write the log using the Standard Format (see below) — be specific and honest, not generic
5. Confirm: `Saved → .ai-collab/claude-{session}.md`

**What to write — be concrete:**
- What the user is working on right now (specific task, not "general development")
- Files you read or modified this session, with what changed and why
- Decisions made and the reasoning
- Bugs or issues found, with file:line when possible
- What is still in progress or unresolved
- Files other AIs should not touch while you are working on them

---

## Command: /collab status

One-line overview of every AI active on this project.

**Steps:**
1. List all `.md` files in `{root}/.ai-collab/` except `PROTOCOL.md`
2. For each file print one line:
   `[emoji] [AI name] — [filename] — last update: [relative time]`
   - 🟢 < 1h · 🟡 1–4h · 🔴 > 4h
3. If directory empty or missing → "No sessions found. Run `/collab setup`."

---

## Command: /collab setup

First-time setup on a new project.

**Steps:**
1. Find project root
2. Create `{root}/.ai-collab/` if missing
3. Check if `.ai-collab/` is in `.gitignore` → if not, append it (ask first if a `.gitignore` already exists)
4. Copy `references/PROTOCOL.md` to `{root}/.ai-collab/PROTOCOL.md`
5. Detect which AI tools the project uses:
   - Ask: "Which other AI tools are working on this project? (Cursor / Windsurf / Copilot / OpenCode / Codex / Aider / Other)"
   - For each confirmed tool, read `references/protocol.md` → extract the snippet for that tool → append to the relevant rules file (`.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`, etc.) or create it
6. **Write `.ai-collab/TEAM.md`** — explicit team manifest so every AI knows who else is on the project from the first session, even before anyone has written a log. Use the slugs the user confirmed in step 5 plus `claude` (always the director). Format:
   ```
   ---
   project: {project-name}
   declared: {ISO timestamp}
   declared_by: Claude Code (/collab setup)
   ---

   ## Roster

   - claude (director)
   - opencode
   - cursor
   - codex
   - {other slugs the user confirmed}

   ## Notes

   {one-line free-form note about team composition, optional}
   ```
   If `TEAM.md` already exists, do NOT overwrite — read it, list the current roster, and ask the user if they want to add or remove members.
7. **Create onboarding welcome** — if `.ai-collab/inbox-all.md` does NOT already exist, write it with this content so the first worker AI to open this project self-orients without the user prompting:
   ```
   ---
   from: Claude Code (setup)
   to: all
   priority: normal
   updated: {ISO timestamp}
   status: unread
   ---

   ## Welcome to the multi-AI team for this project

   You are joining a shared collaboration protocol. The orchestrating director
   is Claude Code. Other AIs may already be active on this codebase.

   ### First-response checklist
   1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise `.ai-collab/PROTOCOL.md`.
   2. Check your specific inbox: `.ai-collab/inbox-{your-slug}.md` (slug = lowercase tool name).
   3. Save your first session log at `.ai-collab/{your-slug}-{YYYYMMDD-HHMMSS}.md`.
   4. Tell the user what role you are taking and what you read from existing logs.

   ### Project context
   - Project: {project-name}
   - Director: Claude Code
   - Coordination: filesystem-based via `.ai-collab/`

   When done, change `status: unread` → `status: done` via atomic write.
   ```
   If `inbox-all.md` already exists, do NOT overwrite — tell the user it was preserved.
8. Run `/collab write` immediately to log Claude's current context
9. Summarize what was created — explicitly mention that the team manifest is ready, the onboarding inbox is ready, and that any worker AI opening this project will see the full roster + self-orient on first response

---

## Command: /collab clear [--all]

Clean up stale logs.

**Steps:**
- Default: delete files in `.ai-collab/` modified > 24h ago → report count
- `--all`: delete everything in `.ai-collab/` except `PROTOCOL.md` → confirm before deleting
- Never touch files outside `.ai-collab/`

---

## Standard Log Format

Every AI writes logs in this exact format. Consistency makes logs machine-readable across tools.

```
---
ai: [Tool name and model, e.g. "Claude Code (claude-sonnet-4-6)"]
session: [YYYYMMDD-HHMMSS]
project: [project root directory name]
updated: [ISO 8601 timestamp]
---

## Working On
[Current task — 2-3 sentences, specific. Not "general review" but "fixing the auth timeout in src/auth.ts line 42"]

## Files Read This Session
- `path/to/file` — [why you read it]

## Files Modified This Session
- `path/to/file` — [what changed and why]

## Decisions Made
- [decision] — [reason]

## Issues Identified
- [problem description] — [file:line if applicable]

## Still In Progress
- [what is not done yet]
- [open question or blocked item]

## Do Not Touch (Avoid Conflicts)
- `path/to/file` — [reason — e.g. "currently being refactored, unstable"]

## Handoff Note
[One paragraph: what the next AI or next session needs to know to continue without losing context or causing conflicts]
```

**Omit any section that has nothing to report.** Don't write empty sections.

---

## Command: /collab summary

Generate a `CONTEXT.md` file — a clean, structured synthesis of all AI logs in the project. This is the onboarding document for any new AI joining the project. One file, full picture, no reading required.

**Steps:**
1. Find project root
2. Read ALL `.md` files in `.ai-collab/` except `PROTOCOL.md` and `CONTEXT.md` itself
3. Extract and deduplicate across all logs:
   - All "Working On" sections → what has been done
   - All "Decisions Made" → key decisions and reasoning
   - All "Files Modified" → what changed and why
   - All "Issues Identified" → known bugs and problems
   - All "Do Not Touch" → files currently locked
   - All "Handoff Notes" → critical context per AI
4. Write `.ai-collab/CONTEXT.md` using this format:

```markdown
# Project Context — AI Collaboration Summary
Generated: {ISO timestamp}
Project: {project name}
Active AIs: {list of AIs that have written logs}

## What has been done
[Chronological summary of work across all AIs — what was built, fixed, analyzed]

## Key Decisions
[All decisions from all logs, deduplicated, with reasoning]

## Current State of the Codebase
[Files modified by each AI and what changed]

## Known Issues
[All bugs and problems identified, with file:line when available]

## Do Not Touch (Active Locks)
[All files currently locked by any AI, with reason]

## Context for New AI — Start Here
[A paragraph synthesizing everything above into a concise onboarding brief.
What is this project? What stage is it at? What should the next AI know before touching anything?]
```

5. Confirm: `CONTEXT.md written → .ai-collab/CONTEXT.md`
6. Tell the user: "Any new AI can now read `.ai-collab/CONTEXT.md` and have full project context instantly."

**Edge cases:**
- If no logs exist yet → say "No AI logs found. Run `/collab write` first."
- If only your own logs exist → generate from those, note it's single-AI context

---

## Coordination rules (remind other AIs of these)

- **Do Not Touch is binding** — if a file appears in another AI's Do Not Touch section, ask the user before editing it
- **Never silently override another AI's decision** — if you disagree, surface it to the user
- **Update your log when something important changes** — don't wait until end of session
- **Announce context at session start** — tell the user what you found in other AIs' logs
- **Language** — write all log content in English or in the language the user is using; never mix alphabets

---

## Command: /collab assign [ai-name] [task description]

Write a task directly to another AI's inbox so it executes automatically on their next response — without the user having to tell them.

**Inbox files:**
- `.ai-collab/inbox-{ai-name}.md` — task for a specific AI (e.g. `inbox-opencode.md`, `inbox-codex.md`)
- `.ai-collab/inbox-all.md` — broadcast to all AIs

**Steps:**
1. Find project root
2. Write `.ai-collab/inbox-{ai-name}.md` with this format:
```
---
from: Claude Code
to: {ai-name}
priority: high | normal | low
updated: {ISO timestamp}
status: unread
---
## Task
{detailed task description with files, constraints, and exit criteria}
```
3. Confirm: "Task written to inbox-{ai-name}.md — {ai-name} will pick it up on next response"

**For broadcast to all AIs:**
```
/collab assign all [task]
→ writes to inbox-all.md
```

**How other AIs respond:** Every AI checks `inbox-{its-name}.md` and `inbox-all.md` at the start of every response. If `status: unread`, it executes the task and marks it `status: done`.

---

## Command: /collab monitor

Start a zero-cost background monitor that watches `.ai-collab/` and notifies you the instant another AI writes or updates their log.

**How it works:** Runs a persistent bash script in the background. No tokens consumed while waiting. Only activates Claude when a real change is detected.

**Steps:**
1. Find project root
2. Launch a Monitor with this script:

```bash
COLLAB_DIR="{project-root}/.ai-collab"
LAST_CHECK=$(date +%s)
while true; do
  sleep 20
  NOW=$(date +%s)
  for f in "$COLLAB_DIR"/*.md; do
    [ -f "$f" ] || continue
    BASENAME=$(basename "$f")
    [[ "$BASENAME" == claude-* ]] && continue
    [[ "$BASENAME" == PROTOCOL.md ]] && continue
    [[ "$BASENAME" == CONTEXT.md ]] && continue
    [[ "$BASENAME" == TEAM.md ]] && continue
    [[ "$BASENAME" == inbox-* ]] && continue
    MOD=$(stat -f "%m" "$f" 2>/dev/null) || MOD=$(stat -c "%Y" "$f" 2>/dev/null) || continue
    if [ "$MOD" -gt "$LAST_CHECK" ]; then
      AI=$(grep "^ai:" "$f" 2>/dev/null | head -1 | cut -d' ' -f2-)
      WORKING=$(grep -A2 "^## Working On" "$f" 2>/dev/null | tail -1 | cut -c1-120)
      echo "UPDATE|$AI|$BASENAME|$WORKING"
    fi
  done
  LAST_CHECK=$NOW
done
```

3. When a notification arrives, read the updated file and tell the user: "[AI name] updated their log: [1-2 line summary]"
4. Confirm to user: "Monitor active — I'll notify you when OpenCode / Codex / [other AIs] update their logs. No tokens consumed while waiting."

**To stop the monitor:** Run `/collab status` to get the monitor task ID, then `TaskStop <id>`.

**Important:** Do NOT use a cron job for this. Cron fires on a fixed interval and consumes tokens every tick even when nothing changed. The Monitor approach is free while idle.

---

## Reference files

- `references/protocol.md` — Setup snippets for Cursor, Windsurf, Copilot, OpenCode, Codex, and generic AIs
