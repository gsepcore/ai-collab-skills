# AI Collab Protocol — Setup for Other AI Tools

Add these snippets to your project once. Each AI will then automatically participate in the shared collab protocol.

---

## How it works

All AIs write Markdown session logs to `{project-root}/.ai-collab/`.
- File naming: `{ai-name}-{YYYYMMDD-HHMMSS}.md`
- Format: YAML frontmatter + standard sections (see SKILL.md for full spec)
- Any AI with filesystem access can read any log

---

## Cursor

Add to `.cursorrules` in the project root:

```
## AI Collab Protocol

You are part of a multi-AI team working on this project. Other AI assistants may be active simultaneously.

### On session start — always do this:
1. Check if `.ai-collab/` exists in the project root
2. If it does, read all `.md` files there (skip `PROTOCOL.md` and your own previous logs)
3. Tell the user: "I found logs from [AI names]. [1-2 sentence summary of what they were working on and any Do Not Touch files]."
4. Check the "Do Not Touch" sections — flag any conflicts before touching those files

### Write your log when:
- The user says "collab write", "save context", "sync", or "update your log"
- You finish a significant task
- You start editing a file another AI is also working on

### Log file: `.ai-collab/cursor-{YYYYMMDD-HHMMSS}.md`

Use this format:
---
ai: Cursor
session: {YYYYMMDD-HHMMSS}
project: {project-root-name}
updated: {ISO timestamp}
---
## Working On
## Files Read This Session
## Files Modified This Session
## Decisions Made
## Issues Identified
## Still In Progress
## Do Not Touch (Avoid Conflicts)
## Handoff Note

### After every response — proactive check:
Silently check if any `.ai-collab/*.md` file (not yours, not PROTOCOL.md) was modified in the last 5 minutes.
If yes → tell the user: "[AI name] just updated their log: [1-line summary of what changed]"
If no → say nothing.

### Coordination rules:
- Respect "Do Not Touch" — ask the user before editing listed files
- Never silently override another AI's decision — surface disagreements to the user
- Write only in English or the language the user is using — no mixed alphabets
```

---

## Windsurf

Add to `.windsurfrules` or `.windsurf/rules/collab.md`:

```
## AI Collab Protocol

You are part of a multi-AI team. Other assistants (Claude Code, Cursor, Codex, etc.) may be working on this project simultaneously.

### On session start:
1. Read all files in `.ai-collab/` (skip PROTOCOL.md and your own previous logs)
2. Summarize to the user what other AIs have been doing and flag any Do Not Touch files
3. Announce: "Context loaded from [N] AI session(s)."

### Write your log to: `.ai-collab/windsurf-{YYYYMMDD-HHMMSS}.md`

Format (YAML frontmatter + sections):
---
ai: Windsurf
session: {YYYYMMDD-HHMMSS}
project: {project-name}
updated: {ISO timestamp}
---
## Working On
## Files Read This Session
## Files Modified This Session
## Decisions Made
## Issues Identified
## Still In Progress
## Do Not Touch (Avoid Conflicts)
## Handoff Note

### After every response:
Check for recent updates (last 5 min) in `.ai-collab/` from other AIs.
Notify the user if there are new logs. Say nothing if there are none.

### Rules: respect Do Not Touch, no silent overrides, write in English or user's language.
```

---

## GitHub Copilot

Add to `.github/copilot-instructions.md`:

```markdown
## AI Collab Protocol

At the start of each session, check `.ai-collab/` in the project root for logs from other AI assistants. Read them and summarize relevant context to the user.

When the user asks you to save context or sync, create `.ai-collab/copilot-{YYYYMMDD-HHMMSS}.md` with YAML frontmatter (ai, session, project, updated) and sections: Working On, Files Read, Files Modified, Decisions Made, Issues Identified, Still In Progress, Do Not Touch, Handoff Note.

After each response, silently check for new logs from other AIs (modified in last 5 min). Notify the user if there are updates.

Respect "Do Not Touch" sections. Write in English or the user's language only.
```

---

## OpenCode / Minimax

Add to your OpenCode system prompt or rules file:

```
## AI Collab Protocol

You are working alongside other AI assistants on this project. A shared log directory exists at `.ai-collab/` in the project root.

On session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Summarize what other AIs have been working on and flag any Do Not Touch files.

Write your log to `.ai-collab/opencode-{YYYYMMDD-HHMMSS}.md` when asked or when finishing a significant task. Use standard format: YAML frontmatter (ai, session, project, updated) + sections (Working On, Files Read, Files Modified, Decisions Made, Issues Identified, Still In Progress, Do Not Touch, Handoff Note).

After every response: silently check for logs updated in the last 5 minutes. If found, tell the user what changed. If not, say nothing.

Rules: respect Do Not Touch, no silent overrides, write in English or the user's language. No mixed alphabets or non-Latin characters unless the user's language requires them.
```

---

## Codex / GPT

Add to your Codex system prompt:

```
## AI Collab Protocol

Other AI assistants are working on this project simultaneously. Check `.ai-collab/` at session start, summarize context to the user, and flag Do Not Touch files.

Write your log to `.ai-collab/codex-{YYYYMMDD-HHMMSS}.md`. Format: YAML frontmatter (ai, session, project, updated) + sections: Working On, Files Read, Files Modified, Decisions Made, Issues Identified, Still In Progress, Do Not Touch, Handoff Note.

After each response, silently check for recent logs from other AIs. Notify the user if updates exist.

Respect Do Not Touch. Write in English or the user's language only.
```

---

## Generic / Any AI

For any AI tool that accepts system prompts or instruction files:

```
## AI Collab Protocol

You are part of a multi-AI team. A shared directory `.ai-collab/` exists in the project root.

1. At session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Tell the user what other AIs were working on. Flag any Do Not Touch files before editing them.

2. Write your session log to `.ai-collab/{your-name}-{YYYYMMDD-HHMMSS}.md` using this format:
   - YAML frontmatter: ai, session, project, updated
   - Sections: Working On / Files Read / Files Modified / Decisions Made / Issues Identified / Still In Progress / Do Not Touch / Handoff Note
   - Omit empty sections

3. After every response: silently check `.ai-collab/` for files modified in the last 5 minutes (excluding your own and PROTOCOL.md). If found, tell the user "[AI name] just updated: [1-line summary]". If nothing new, say nothing.

4. Coordination: respect Do Not Touch sections, never silently override another AI's decision, write only in English or the user's language.
```

---

## Directory layout

```
your-project/
└── .ai-collab/
    ├── PROTOCOL.md                        ← this file (do not modify)
    ├── claude-20260511-143022.md          ← Claude Code's log
    ├── cursor-20260511-141500.md          ← Cursor's log
    ├── windsurf-20260511-140000.md        ← Windsurf's log
    ├── opencode-20260511-142000.md        ← OpenCode's log
    └── codex-20260511-141000.md           ← Codex's log
```

---

## Conflict resolution

1. **Do Not Touch wins** — always ask the user before editing a file another AI marked
2. **Newest decision wins when there is a conflict** — but surface both to the user, don't decide alone
3. **Announce at session start** — always tell the user what context you found
4. **Language** — English or user's language. Never mix writing systems in a single message.
