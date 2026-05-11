# AI Collab Protocol — Setup for Other AI Tools

Add these snippets to your project once. Each AI will then automatically participate in the shared collab protocol.

**Three rules that make this work:**
1. Every AI saves a log after EVERY response — automatically, no prompting needed.
2. Every new AI reads `CONTEXT.md` first — the single-file project brief from all logs.
3. Every AI checks its inbox on every response — `inbox-{ai-name}.md` and `inbox-all.md` for direct task assignments from the orchestrating AI.

---

## How it works

All AIs write Markdown session logs to `{project-root}/.ai-collab/`.
- File naming: `{ai-name}-{YYYYMMDD-HHMMSS}.md`
- Format: YAML frontmatter + standard sections (see SKILL.md for full spec)
- Any AI with filesystem access can read any log
- The daemon (if installed) detects new/updated logs within 15 seconds and notifies the user

---

## Cursor

Add to `.cursorrules` in the project root:

```
## AI Collab Protocol

You are part of a multi-AI team working on this project. Other AI assistants may be active simultaneously.

### On session start — always do this:
1. Check if `.ai-collab/CONTEXT.md` exists → if yes, read it first (full project brief in one file)
2. Then read any `.md` logs newer than `CONTEXT.md` to catch what changed since it was generated
3. Read `.ai-collab/inbox-cursor.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks now
4. Tell the user: "I found context from [AI names]. [1-2 sentence summary and any Do Not Touch files]."
5. Check the "Do Not Touch" sections — flag any conflicts before touching those files

### AUTOMATIC LOG — after EVERY response (mandatory, no exceptions):
After every single response you give, automatically save your log.
Do NOT wait for the user to ask. Do NOT skip this step. This is what enables real-time multi-AI collaboration.

Log file: `.ai-collab/cursor-{YYYYMMDD-HHMMSS}.md`
Create once per session, update it after each response.

---
ai: Cursor
session: {YYYYMMDD-HHMMSS}
project: {project-root-name}
updated: {ISO timestamp}
---
## Working On
[1-2 lines: what you just responded about — be specific]
## Files Modified This Session
[files changed, if any]
## Decisions Made
[decisions taken, if any]
## Do Not Touch (Avoid Conflicts)
[files currently being edited]
## Handoff Note
[the one thing other AIs must know from this response]

### After every response — proactive check:
Silently check if any `.ai-collab/*.md` file (not yours, not PROTOCOL.md) was modified in the last 5 minutes.
If yes → tell the user: "[AI name] just updated their log: [1-line summary]"
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

### AUTOMATIC LOG — after EVERY response (mandatory, no exceptions):
After every single response, automatically update your log file.
Do NOT wait for the user to ask. This enables real-time multi-AI collaboration.

Log file: `.ai-collab/windsurf-{YYYYMMDD-HHMMSS}.md`
Create once per session, update it after each response.

---
ai: Windsurf
session: {YYYYMMDD-HHMMSS}
project: {project-name}
updated: {ISO timestamp}
---
## Working On
[1-2 lines: what you just responded about — be specific]
## Files Modified This Session
[files changed, if any]
## Decisions Made
[decisions taken, if any]
## Do Not Touch (Avoid Conflicts)
[files currently being edited]
## Handoff Note
[the one thing other AIs must know from this response]

### After every response:
Check for recent updates (last 5 min) in `.ai-collab/` from other AIs.
Notify the user if there are new logs. Say nothing if there are none.

### Rules:
- Respect Do Not Touch — ask before editing flagged files
- No silent overrides — surface disagreements to the user
- Write in English or the user's language only — no mixed alphabets
```

---

## GitHub Copilot

Add to `.github/copilot-instructions.md`:

```markdown
## AI Collab Protocol

At the start of each session, check `.ai-collab/` in the project root for logs from other AI assistants. Read them and summarize relevant context to the user.

**AUTOMATIC LOG — after EVERY response (mandatory):**
After every response, automatically save `.ai-collab/copilot-{YYYYMMDD-HHMMSS}.md`.
Do NOT wait for the user to ask. Use YAML frontmatter (ai, session, project, updated) and these sections:
Working On (1-2 lines of what you just responded about), Files Modified, Decisions Made, Do Not Touch, Handoff Note.

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

AUTOMATIC LOG — MANDATORY after EVERY response:
After every single response you give — automatically, without the user asking — save your log to:
`.ai-collab/opencode-{YYYYMMDD-HHMMSS}.md`

Use this format:
---
ai: OpenCode (Minimax 2.5)
session: {YYYYMMDD-HHMMSS}
project: {project-root-name}
updated: {ISO timestamp}
---
## Working On
[1-2 lines: what you just responded about — be specific]
## Files Modified This Session
[files changed, if any]
## Decisions Made
[decisions taken, if any]
## Do Not Touch (Avoid Conflicts)
[files currently being edited]
## Handoff Note
[the one thing other AIs must know from this response]

After every response: silently check for logs updated in the last 5 minutes. If found, tell the user what changed. If not, say nothing.

Rules: respect Do Not Touch, no silent overrides, write in English or the user's language. No mixed alphabets or non-Latin characters unless the user's language requires them.
```

---

## Codex / GPT

Add to your Codex system prompt:

```
## AI Collab Protocol

Other AI assistants are working on this project simultaneously. Check `.ai-collab/` at session start, summarize context to the user, and flag Do Not Touch files.

AUTOMATIC LOG — MANDATORY after EVERY response:
After every single response you give — automatically, without the user asking — save your log to:
`.ai-collab/codex-{YYYYMMDD-HHMMSS}.md`

Use this format:
---
ai: Codex (GPT 5.5)
session: {YYYYMMDD-HHMMSS}
project: {project-root-name}
updated: {ISO timestamp}
---
## Working On
[1-2 lines: what you just responded about — be specific]
## Files Modified This Session
[files changed, if any]
## Decisions Made
[decisions taken, if any]
## Do Not Touch (Avoid Conflicts)
[files currently being edited]
## Handoff Note
[the one thing other AIs must know from this response]

After each response, silently check for recent logs from other AIs. Notify the user if updates exist.
Respect Do Not Touch. Write in English or the user's language only.
```

---

## Antigravity IDE

Add to your Antigravity rules or system prompt:

```
## AI Collab Protocol

You are part of a multi-AI team inside Antigravity IDE. Other assistants (Claude Code, OpenCode, Codex, etc.) may be active simultaneously.

On session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Tell the user what other AIs were working on and flag Do Not Touch files.

AUTOMATIC LOG — MANDATORY after EVERY response:
After every response — automatically — save to: `.ai-collab/antigravity-{YYYYMMDD-HHMMSS}.md`
Format: YAML frontmatter (ai, session, project, updated) + sections: Working On / Files Modified / Decisions Made / Do Not Touch / Handoff Note.

After every response: check for logs updated in last 5 min. Notify user if found. Say nothing if not.
Rules: respect Do Not Touch, no silent overrides, English or user's language only.
```

---

## VS Code (GitHub Copilot)

Add to `.github/copilot-instructions.md` or VS Code AI instructions:

```
## AI Collab Protocol

You are part of a multi-AI team. Check `.ai-collab/` at session start. Read all `.md` files (skip PROTOCOL.md and your own logs). Summarize context and flag Do Not Touch files.

AUTOMATIC LOG — MANDATORY after EVERY response:
After every response — automatically — save to: `.ai-collab/copilot-{YYYYMMDD-HHMMSS}.md`
Format: YAML frontmatter (ai, session, project, updated) + sections: Working On / Files Modified / Decisions Made / Do Not Touch / Handoff Note.

After every response: check for logs updated in last 5 min. Notify user if found. Say nothing if not.
Rules: respect Do Not Touch, no silent overrides, English or user's language only.
```

---

## Hermes

Add to your Hermes system prompt or rules:

```
## AI Collab Protocol

You are part of a multi-AI team. Check `.ai-collab/` at session start. Read all `.md` files (skip PROTOCOL.md and your own logs). Summarize context and flag Do Not Touch files.

AUTOMATIC LOG — MANDATORY after EVERY response:
After every response — automatically — save to: `.ai-collab/hermes-{YYYYMMDD-HHMMSS}.md`
Format: YAML frontmatter (ai, session, project, updated) + sections: Working On / Files Modified / Decisions Made / Do Not Touch / Handoff Note.

After every response: check for logs updated in last 5 min. Notify user if found. Say nothing if not.
Rules: respect Do Not Touch, no silent overrides, English or user's language only.
```

---

## Generic / Any AI

For any AI tool that accepts system prompts or instruction files:

```
## AI Collab Protocol

You are part of a multi-AI team. A shared directory `.ai-collab/` exists in the project root.

1. At session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Tell the user what other AIs were working on. Flag any Do Not Touch files before editing them.

2. AUTOMATIC LOG — MANDATORY after EVERY response:
   After every single response — automatically, without the user asking — save your log to:
   `.ai-collab/{your-ai-name}-{YYYYMMDD-HHMMSS}.md`

   Format: YAML frontmatter (ai, session, project, updated) + sections:
   Working On (1-2 lines of what you just responded) / Files Modified / Decisions Made / Do Not Touch / Handoff Note
   Omit empty sections. Update the same file within a session.

3. After every response: silently check `.ai-collab/` for files modified in the last 5 minutes (excluding your own and PROTOCOL.md). If found, tell the user "[AI name] just updated: [1-line summary]". If nothing new, say nothing.

4. Coordination: respect Do Not Touch sections, never silently override another AI's decision, write only in English or the user's language.
```

---

## Directory layout

```
your-project/
└── .ai-collab/
    ├── PROTOCOL.md                        ← this file (do not modify)
    ├── claude-20260511-143022.md          ← Claude Code's log (auto-updated)
    ├── cursor-20260511-141500.md          ← Cursor's log (auto-updated after every response)
    ├── windsurf-20260511-140000.md        ← Windsurf's log (auto-updated after every response)
    ├── opencode-20260511-142000.md        ← OpenCode's log (auto-updated after every response)
    └── codex-20260511-141000.md           ← Codex's log (auto-updated after every response)
```

---

## Conflict resolution

1. **Do Not Touch wins** — always ask the user before editing a file another AI marked
2. **Newest decision wins when there is a conflict** — but surface both to the user, don't decide alone
3. **Announce at session start** — always tell the user what context you found
4. **Language** — English or user's language. Never mix writing systems in a single message.
