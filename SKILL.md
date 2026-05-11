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
   - Ask: "Which other AI tools are working on this project? (Cursor / Windsurf / Copilot / OpenCode / Codex / Other)"
   - For each confirmed tool, read `references/protocol.md` → extract the snippet for that tool → append to the relevant rules file (`.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`, etc.) or create it
6. Run `/collab write` immediately to log Claude's current context
7. Summarize what was created

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

## Coordination rules (remind other AIs of these)

- **Do Not Touch is binding** — if a file appears in another AI's Do Not Touch section, ask the user before editing it
- **Never silently override another AI's decision** — if you disagree, surface it to the user
- **Update your log when something important changes** — don't wait until end of session
- **Announce context at session start** — tell the user what you found in other AIs' logs
- **Language** — write all log content in English or in the language the user is using; never mix alphabets

---

## Reference files

- `references/protocol.md` — Setup snippets for Cursor, Windsurf, Copilot, OpenCode, Codex, and generic AIs
