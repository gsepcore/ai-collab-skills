# AI Collab Protocol — Setup for Other AI Agents

Add these snippets to your project once. Each AI agent will then automatically participate in the shared collab protocol.

**Agent-first identity model:**

- `agent` is the runtime doing work: `claude-code`, `opencode`, `codex`, `aider`, `hermes`, `cursor-native`, `windsurf-native`, `copilot-chat`, or a custom slug.
- `container` is where the agent is visible: `antigravity`, `cursor`, `vscode`, `windsurf`, `terminal`, etc.
- `model` is the LLM behind the agent: `openai/gpt-5.5`, `minimax/m2.7`, `anthropic/claude-opus-4.7`, etc.

Prefer the project onboarding helper instead of manually copying snippets:

```bash
python3 ~/.claude/ai-collab-project-setup.py
```

It writes `.ai-collab/TEAM.md`, `.ai-collab/agents.json`, `.ai-collab/capabilities.json`, `.ai-collab/inbox-all.md`, and the correct rules files for each selected agent. Then run `python3 ~/.claude/ai-collab-team.py configure` to assign persistent development-team roles.

**Core rules that make this work:**
1. Every AI saves a log after EVERY response — automatically, no prompting needed.
2. Every new AI reads `CONTEXT.md` first — the single-file project brief from all logs.
3. Every AI performs a full preflight before EVERY response, analysis, or tool action: context/protocol, team roster, `.ai-collab/capabilities.json`, `.ai-collab/roles.json` when present, direct inbox, `inbox-all.md`, relevant threads/discussions, recent logs from other agents, and active `Do Not Touch` sections.
4. Every AI treats `thread-{task_id}.md` as the task conversation channel, and `.ai-collab/discussions/*.md` as natural design/review conversations — `@slug` mentions can wake the mentioned agent when the daemon and adapter are running.
5. Directed implementation runs have one active director in `.ai-collab/runs/{run_id}/director.json`; all other agents respect that director for the run.
6. Every AI keeps live observability current in `.ai-collab/live/{agent}.agent.json` before and after commands, tests, file edits, blockers, and handoffs.
7. Development-team roles guide default routing. Explicit task ownership overrides them, and a vacant role must be resolved with the user/director before delegation.
8. Delivery is internal-first. Write the inbox/thread, wait the configured short grace period, announce any non-response before visible escalation, and wake only the missing agents in their exact visible project chats.
9. Prompt submission is not a response. Use distinct states for queued, internal response, escalating visibly, submitted visibly, responded, and failed.
10. Keep one continuous task/discussion thread through progress questions, blockers, reviews, and handoff. If the director is stale/sleeping, workers use the director's declared visible route in `capabilities.json` and fail closed when that route is degraded.

---

## How it works

All AIs write Markdown session logs to `{project-root}/.ai-collab/`.
- File naming: `{agent-slug}-{YYYYMMDD-HHMMSS}.md`
- Format: YAML frontmatter + standard sections (see SKILL.md for full spec)
- Any AI with filesystem access can read any log
- The daemon (if installed) detects new/updated logs within 15 seconds and notifies the user
- The daemon also scans `inbox-*.md`, `thread-*.md`, and `discussions/*.md`: unread inboxes target an agent mailbox, while `@slug` mentions in conversations target that agent's monitor/adapter path.
- The daemon also writes semantic live snapshots to `.ai-collab/live/{agent}.json`, `.ai-collab/live/summary.json`, `.ai-collab/live/health.json`, `.ai-collab/live/visual-roster.json`, and `.ai-collab/live/director-alerts.jsonl`. Process hints and screenshots are project-scoped. The visual roster correlates the real image with each visible surface, exact-project PID/TTY/process, owned ports, bridge routes, and recent logs. An IDE bridge's local port belongs to routing infrastructure; it is never mislabeled as the agent's own port. Screenshots and `.semantic.json` sidecars live under `.ai-collab/live/screenshots/`; Retina images are downscaled only in a disposable OCR copy while the original evidence remains unchanged.

Every log should include these frontmatter fields when the agent supports them:

```yaml
agent: opencode
container: antigravity
model: minimax/m2.7
```

---

## Live observability contract

When your agent slug is `{agent}`, keep these files up to date:

- `.ai-collab/live/{agent}.agent.json` — your current self-reported state.
- `.ai-collab/live/{agent}.agent.events.jsonl` — append-only command/test/edit events you report.
- `.ai-collab/live/{agent}.events.jsonl` — observer-owned status/process/screenshot events; do not write to this file directly.
- `.ai-collab/live/health.json` and `.ai-collab/live/screenshots/*.semantic.json` — observer-owned health/vision diagnostics; read them if useful, but do not write them directly.

Before running a command, atomically write `.agent.json` like:

```json
{
  "agent": "opencode",
  "updated": "2026-06-15T12:00:00Z",
  "phase": "command",
  "current_command": "python3 -m unittest install/test_wakeup.py",
  "task_id": "task-123",
  "files_in_scope": ["install/ai-collab-wakeup.py"]
}
```

After the command finishes, append one JSON line to `.agent.events.jsonl`:

```json
{"timestamp":"2026-06-15T12:00:10Z","agent":"opencode","event":"command","command":"python3 -m unittest install/test_wakeup.py","exit_code":0,"output_excerpt":"Ran 43 tests in 0.6s OK"}
```

Use `phase: "editing"` before file edits, `phase: "blocked"` with a `blocker` when stuck, and `phase: "idle"` or `phase: "done"` when finished. Use temp-file + rename for `.agent.json`.

---

## Task lifecycle — what every snippet implements

Inbox tasks carry a state machine: `unread → claimed → running → done | failed` (with `blocked` as a side branch). The daemon, the wakeup adapters, and every AI must respect it so two workers don't race the same task and the daemon can deduplicate and retry safely.

Required frontmatter fields on every inbox file:

- `task_id` — durable identifier, never mutated after creation.
- `status` — current state (`unread`, `claimed`, `running`, `blocked`, `done`, `failed`).
- `attempts`, `last_attempt` — wakeup retry tracking, owned by the daemon.
- `claimed_by`, `claimed_at` — set when an AI picks up the task.
- `done_at` — set when the task completes successfully.

The contract for every AI: **claim before executing**, **mark done after executing**, **never overwrite another AI's claim**. The full spec (director semantics, conflict resolution, stale-claim timeouts) is in `claude-task-lifecycle-spec.md`.

---

## Threaded agent-to-agent conversation

Threads are append-only task conversations, and discussions are append-only natural conversations:

```text
.ai-collab/thread-{task_id}.md
.ai-collab/discussions/discussion-{timestamp}-{topic}.md
```

Use a task thread when a task needs clarification, review, or handoff between agents. Use a discussion when agents need to explore a design choice, ask for help, compare implementation options, or record a decision before a task exists. The inbox remains canonical for task status; the thread/discussion is the conversation layer.

Rules for every agent:

- If an inbox has a matching `thread-{task_id}.md`, read the latest 3 messages before acting.
- Read recent `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant.
- When setting `status: blocked`, append the blocker reason to the thread.
- When setting `status: done`, append a short final summary if the thread exists.
- Use `@slug` to ask another agent for help or review. Example: `@codex please verify the release script`.
- Do not edit past thread messages. Append corrections as new messages.
- Do not write to a thread/discussion whose frontmatter says `status: closed`.
- A direct mention must be submitted to the project-matched visible interface. Never replace visible delivery with a hidden worker.
- Before a visible turn, require a fresh screenshot and its immutable `.visual-roster.json` evidence file with every requested participant verified. Each recipient must open the PNG with native vision or run `ai-collab-see.py` so the actual pixels are processed directly, identify itself and its peers, and compare that sight with project/PID/TTY/port/log evidence.
- On a visible collaboration wake, append a substantive opinion/recommendation to the same thread, mention the director, and include `visual_evidence: <screenshot path>` plus `visible_peers: <slugs actually seen>` before unrelated work.
- Require a fresh post-turn visual proof. A wake event or adapter success proves only submission. `visually verified` requires the image and roster; `responded` requires a compliant agent-authored thread message; `started` requires the agent's own inbox claim/live update; `completed` requires done state plus handoff evidence.
- Missing/stale images, failed OCR, ambiguous processes/ports, unreadable visual evidence, or any identity mismatch are blockers. Fail closed rather than substituting logs, ports, or a hidden worker.
- Use the surface-specific standard recorded by the roster. Terminal/TUI surfaces require an exact project PID/TTY and their own port when one exists. IDE-native chat panels share the outer IDE process and may have no agent-owned port; require the captured host PID to be an ancestor of the exact project bridge, then verify the position-bound label and actual pane pixels (`registered-shared-project-host+position-bound-top-band-label`). Shared hosting is expected, but an unrelated Electron window fails.

Use the deterministic helper when available:

```bash
python3 ~/.claude/ai-collab-converse.py start --author codex --topic "API boundary" --to opencode --message "Can you compare option A vs B?" --wait-seconds 180
python3 ~/.claude/ai-collab-converse.py question --thread discussion-20260616-120000-api-boundary --author opencode --to codex --message "Which files are safe to touch?"
python3 ~/.claude/ai-collab-converse.py decision --thread discussion-20260616-120000-api-boundary --author codex --message "Decision: keep API v1 stable and add an adapter."
```

Messages include a parseable `type:` field (`question`, `answer`, `proposal`, `decision`, `blocker`, `review`, `handoff`, or `message`) so agents can skim intent quickly.

The conversation helper writes internally first. If the recipient does not answer within its `capabilities.json` grace period, it records a user-visible escalation notice, focuses the exact target surface without sending when supported, runs mandatory visual proof, and dispatches only the missing direct mentions. A still-running legacy bridge may focus on the first exact-terminal submission; in that case immediate post-submit proof and an evidence follow-up are mandatory. The daemon provides retries/recovery. When the latest message mentions `@codex`, `@opencode`, `@claude`, or another registered slug, a wake event targets that agent without changing inbox claim state. Only the target agent's own visually attested response or claim advances evidence.

---

## Directed multi-agent runs

For large implementation plans, the user may choose a run director (`claude-code`, `codex`, `opencode`, or another registered slug). The director uses:

```text
.ai-collab/runs/{run_id}/director.json
.ai-collab/runs/{run_id}/PLAN.md
.ai-collab/runs/{run_id}/tasks.json
.ai-collab/runs/{run_id}/status.md
.ai-collab/runs/{run_id}/final-summary.md
```

Workers must follow these rules:

- If `director_lock` is `active`, do not override the run director.
- Only work on tasks where you are the owner or where the director explicitly mentions you in the thread.
- Stay inside the task's allowed files. Ask in `thread-{task_id}.md` before crossing boundaries.
- Use natural conversation in task threads for questions, answers, review requests, and handoffs.
- Keep your session log updated after every meaningful change.

The deterministic helper is `python3 ~/.claude/ai-collab-orchestrate.py`.

Before task assignment, the director runs `convene` and waits for one real thread response per participant. If any interface or response fails, the director reports the named failure and must not manufacture that agent's opinion or progress.

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

### Inbox check — every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response (not just session start), re-read `.ai-collab/inbox-cursor.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` before doing anything else. (2) Execute the task BEFORE responding to the user's current message. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`. Use temp file + rename for every write.

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
2. Read `.ai-collab/inbox-windsurf.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks now and mark them `status: done`
3. Summarize to the user what other AIs have been doing and flag any Do Not Touch files
4. Announce: "Context loaded from [N] AI session(s)."

### Inbox check — every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response, re-read `.ai-collab/inbox-windsurf.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` before doing anything else. (2) Execute the task BEFORE responding to the user's current message. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`. Use temp file + rename for every write.

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

At the start of each session, check `.ai-collab/` in the project root for logs from other AI assistants. Read them and summarize relevant context to the user. Also read `.ai-collab/inbox-copilot.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

**INBOX CHECK — before every response (MANDATORY):**
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response, re-read `.ai-collab/inbox-copilot.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` first. (2) Execute the task BEFORE responding. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`.

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

On session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Summarize what other AIs have been working on and flag any Do Not Touch files. Also read `.ai-collab/inbox-opencode.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

INBOX CHECK — before every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response (not just session start), re-read `.ai-collab/inbox-opencode.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` before doing anything else. (2) Execute the task BEFORE responding to the user's current message. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`. Use temp file + rename for every write.

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

Other AI assistants are working on this project simultaneously. Check `.ai-collab/` at session start, summarize context to the user, and flag Do Not Touch files. Also read `.ai-collab/inbox-codex.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

INBOX CHECK — before every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response (not just session start), re-read `.ai-collab/inbox-codex.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` before doing anything else. (2) Execute the task BEFORE responding to the user's current message. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`. Use temp file + rename for every write.

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

On session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Tell the user what other AIs were working on and flag Do Not Touch files. Also read `.ai-collab/inbox-antigravity.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

INBOX CHECK — before every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response, re-read `.ai-collab/inbox-antigravity.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` first. (2) Execute the task BEFORE responding. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`.

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

You are part of a multi-AI team. Check `.ai-collab/` at session start. Read all `.md` files (skip PROTOCOL.md and your own logs). Summarize context and flag Do Not Touch files. Also read `.ai-collab/inbox-copilot.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

INBOX CHECK — before every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response, re-read `.ai-collab/inbox-copilot.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` first. (2) Execute the task BEFORE responding. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`.

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

You are part of a multi-AI team. Check `.ai-collab/` at session start. Read all `.md` files (skip PROTOCOL.md and your own logs). Summarize context and flag Do Not Touch files. Also read `.ai-collab/inbox-hermes.md` and `.ai-collab/inbox-all.md` — if `status: unread`, execute those tasks immediately and mark them `status: done`.

INBOX CHECK — before every response (MANDATORY):
Before any analysis or answer, first re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing.

Before every response, re-read `.ai-collab/inbox-hermes.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`: (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` first. (2) Execute the task BEFORE responding. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`.

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

1. At session start: read all `.md` files in `.ai-collab/` (skip PROTOCOL.md and your own logs). Tell the user what other AIs were working on. Flag any Do Not Touch files before editing them. Then read `.ai-collab/inbox-{your-ai-name}.md` and `.ai-collab/inbox-all.md` — if either has `status: unread`, execute those tasks immediately and mark them `status: done`.

2. PREFLIGHT + INBOX CHECK — before every response, analysis, or tool action (MANDATORY):
   First re-read project context and recent coordination state: `.ai-collab/CONTEXT.md` or `.ai-collab/PROTOCOL.md`, `.ai-collab/TEAM.md`, your inbox, `inbox-all.md`, relevant `thread-*.md` / `discussions/*.md`, and recent logs from other agents. Respect `Do Not Touch` before replying or editing. Then handle unread inboxes: if either inbox has `status: unread`, (1) atomically set `status: claimed`, `claimed_by: {your-slug}`, `claimed_at: {ISO timestamp}` before doing anything else. (2) Execute the task BEFORE responding to the user's current message. (3) Atomically set `status: done`, `done_at: {ISO timestamp}`. Never modify `task_id`. Use temp file + rename for every write.

3. AUTOMATIC LOG — MANDATORY after EVERY response:
   After every single response — automatically, without the user asking — save your log to:
   `.ai-collab/{your-ai-name}-{YYYYMMDD-HHMMSS}.md`

   Format: YAML frontmatter (ai, session, project, updated) + sections:
   Working On (1-2 lines of what you just responded) / Files Modified / Decisions Made / Do Not Touch / Handoff Note
   Omit empty sections. Update the same file within a session.

4. After every response: silently check `.ai-collab/` for files modified in the last 5 minutes (excluding your own and PROTOCOL.md). If found, tell the user "[AI name] just updated: [1-line summary]". If nothing new, say nothing.

5. Coordination: respect Do Not Touch sections, never silently override another AI's decision, write only in English or the user's language.
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
