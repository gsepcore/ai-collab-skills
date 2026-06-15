---
name: collab
description: Enable real-time collaboration and directed implementation runs between multiple AI coding agents (Claude Code, OpenCode, Codex, Aider, Hermes, Cursor native chat, Windsurf native chat, Copilot Chat, etc.) working on the same project simultaneously, regardless of IDE/container or LLM model. Use this skill when the user works with more than one AI agent and wants them to share context, read each other's logs, receive tasks, avoid conflicting changes, or choose a director agent to plan and execute a complex multi-agent implementation through inboxes and task threads. Triggers on /collab, /collab orchestrate, "what has codex been doing", "share my context with opencode", "what is the other AI working on", "sync with cursor", "multi-AI", "collab", "read other AI conversation", "lee la conversacion de la otra IA", "qué hizo codex", "comparte contexto", "sincroniza con la otra IA", "haz un plan con varios agentes", "Codex como director".
---

# AI Collab Skill

Shared filesystem protocol so every AI coding agent working on the same project can read and write context in real time. No external service, no API — just a `.ai-collab/` directory inside the project.

---

## How it works

Each AI writes a Markdown log to `{project-root}/.ai-collab/`. Any AI with filesystem access to the project can read those logs. Claude manages its own log via this skill. Other agents (OpenCode, Codex, Aider, Cursor native chat, etc.) write via agent-specific rules installed by `~/.claude/ai-collab-project-setup.py`.

The installed daemon also writes semantic live snapshots to `{project-root}/.ai-collab/live/`. These are the "eyes" layer: current inbox/task state, latest log summary, self-reported commands/edits from each agent, process hints, git dirty files, director alerts, and automatic screenshots unless `AI_COLLAB_OBSERVER_SCREENSHOTS=0`.

**Conceptual model:** this skill is agent-first. `agent` is the runtime doing work, `container` is the IDE/terminal where it is visible, and `model` is metadata about the LLM behind it. Do not treat IDEs and agents as the same thing.

---

## Command: /collab read

Show everything other AIs have written for this project.

**Steps:**
1. Find project root: `git rev-parse --show-toplevel 2>/dev/null || pwd`
2. Check `{root}/.ai-collab/` — if missing or empty, say so and suggest `/collab setup`
3. For each `.md` file except `PROTOCOL.md` and your own `claude-code-*.md` / legacy `claude-*.md` (sorted newest first):
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
3. Find if a `claude-code-*.md` file from this session already exists (modified < 4h) → update it; otherwise create `claude-code-{YYYYMMDD-HHMMSS}.md`
4. Write the log using the Standard Format (see below) — be specific and honest, not generic
5. Confirm: `Saved → .ai-collab/claude-code-{session}.md`

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
2. If `{root}/.ai-collab/live/summary.json` exists, read it and prefer its status/current task/current command for each agent.
3. For each file/live snapshot print one line:
   `[emoji] [AI name] — [filename] — last update: [relative time]`
   - 🟢 < 1h · 🟡 1–4h · 🔴 > 4h
4. If directory empty or missing → "No sessions found. Run `/collab setup`."

## Command: /collab observe

Show the live semantic observer view for the project.

**Steps:**
1. Find project root: `git rev-parse --show-toplevel 2>/dev/null || pwd`
2. Check `{root}/.ai-collab/live/summary.json`
   - If missing, say "No live observer snapshots found yet. The daemon may not be installed/running, or AI_COLLAB_OBSERVER=0."
3. Read `summary.json`, then for each agent read `{root}/.ai-collab/live/{agent}.json` if present.
4. Print one compact block per agent:
   - status
   - current task id
   - current command
   - phase
   - inbox status
   - latest log path/mtime
   - dirty files count
   - alerts
   - screenshot path if present
5. Read `{root}/.ai-collab/live/director-alerts.jsonl` if present and print the latest 5 alerts first.
6. If screenshots are not present, mention they are enabled by default but may require macOS Screen Recording permission, a supported macOS host, or `AI_COLLAB_OBSERVER_SCREENSHOTS` not being set to `0`.

---

## Command: /collab setup

First-time setup on a new project, AND safe to re-run later when a new AI joins. Idempotent.

**Steps:**

1. Find project root.
2. Run the deterministic onboarding helper:

   ```bash
   python3 ~/.claude/ai-collab-project-setup.py --root "$ROOT"
   ```

   If the helper is not installed, fall back to the bundled copy in this repo: `install/ai-collab-project-setup.py`.
3. The helper must ask/record:
   - IDE/container: `antigravity`, `cursor`, `vscode`, `windsurf`, `terminal`, `other`
   - agents: `claude-code`, `opencode`, `codex`, `aider`, `hermes`, `cursor-native`, `windsurf-native`, `copilot-chat`, or custom
   - LLM model for each agent, e.g. `openai/gpt-5.5`, `anthropic/claude-opus-4.7`, `minimax/m2.7`
4. Verify these files exist after setup:
   - `.ai-collab/PROTOCOL.md`
   - `.ai-collab/TEAM.md`
   - `.ai-collab/agents.json`
   - `.ai-collab/inbox-all.md`
   - the relevant agent rules files
5. Report what was done in one line per agent:

   ```
   ✓ claude-code → CLAUDE.md (created/appended)
   ✓ opencode    → .opencode/rules/ai-collab.md + AGENTS.md (created/appended)
   ✓ codex       → AGENTS.md (created/appended)
   ✓ cursor-native → .cursorrules (created/appended)
   ```

6. Run `/collab write` immediately to log Claude's current context.
7. Start `/collab monitor` automatically for this project in the current Claude Code session. Do not ask the user to run it manually. If a monitor for this project is already active, keep it and report "monitor already active." If the current Claude Code runtime cannot launch a persistent Monitor/Task, say that clearly and rely on the installed daemon + macOS/UserPromptSubmit notifications as the fallback.
8. Summarize the registered agents, their containers, their models, the exact rules files created, and whether the live monitor is active.

**Re-run behavior:** This command is idempotent. Re-running it after a new AI joins the project will detect the new AI, append its rules block to its rules file (idempotent — skipped if already there), and merge it into `TEAM.md`. Nothing existing is overwritten or removed.

---

## Command: /collab onboard [ai-slug]

Add a single AI to the project after the initial setup. Use when:

- An AI was not auto-detected by `/collab setup` (e.g. installed after, custom tool, slug not in the detection table).
- You explicitly want to register an AI before opening it in this project.

**Steps:**

1. Find project root.
2. Normalize legacy aliases:
   - `cursor` → `cursor-native`
   - `windsurf` → `windsurf-native`
   - `copilot` / `vscode` → `copilot-chat`
   - `antigravity` → `codex` unless the user explicitly means a native Antigravity agent
3. Ask for container and model if they are unknown.
4. Run:

   ```bash
   python3 ~/.claude/ai-collab-project-setup.py --root "$ROOT" --agents "$SLUG" --container "$CONTAINER" --models "$SLUG=$MODEL"
   ```

5. Report: "Onboarded `{slug}` with container `{container}` and model `{model}`."

**Example:**

```
/collab onboard opencode
→ Onboarded opencode → .opencode/rules/ai-collab.md + AGENTS.md (created)
→ Added to TEAM.md roster
```

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
agent: [agent runtime slug, e.g. claude-code, opencode, codex]
container: [IDE/terminal, e.g. antigravity, cursor, vscode, terminal]
model: [LLM id, e.g. openai/gpt-5.5, minimax/m2.7]
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

## Command: /collab orchestrate

Use this when the user gives a large implementation goal and wants multiple agents to finish the plan together. The user may choose the director for the run: `claude-code`, `codex`, `opencode`, or another registered agent. Only one director controls a given run.

**Director selection rules:**
- If the user names a director, use that agent.
- If no director is named and you are Claude Code, default to `claude-code`.
- If no director is named and you are Codex using this skill, default to `codex`.
- Never create two active directors for the same run. Respect `.ai-collab/runs/{run_id}/director.json`.

**State layout:**

```text
.ai-collab/runs/{run_id}/
  PLAN.md
  director.json
  tasks.json
  status.md
  final-summary.md
.ai-collab/thread-{task_id}.md
.ai-collab/inbox-{agent}.md
```

**Use the deterministic helper:**

```bash
python3 ~/.claude/ai-collab-orchestrate.py init --goal "$GOAL" --director "$DIRECTOR" --agents "$AGENTS" --title "$TITLE"
python3 ~/.claude/ai-collab-orchestrate.py add-task --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK" --title "$TITLE" --owner "$AGENT" --allowed-files "$FILES" --description "$DESC" --validation "$VALIDATION"
python3 ~/.claude/ai-collab-orchestrate.py assign --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK"
python3 ~/.claude/ai-collab-orchestrate.py thread --run-id "$RUN_ID" --task-id "$TASK" --author "$AGENT" --message "$MESSAGE"
python3 ~/.claude/ai-collab-orchestrate.py set-task --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK" --status done --summary "$SUMMARY"
python3 ~/.claude/ai-collab-orchestrate.py finalize --run-id "$RUN_ID" --actor "$DIRECTOR" --summary "$SUMMARY" --validation "$VALIDATION"
```

**Execution workflow:**
1. Read `.ai-collab/CONTEXT.md`, `TEAM.md`, active inboxes, and recent logs.
2. Create the run with the selected director and participating agents.
3. Write a concrete `PLAN.md`: tasks, dependencies, owners, allowed files, validation.
4. Add and assign tasks with one owner each. Never assign a task without file boundaries for code edits.
5. Agents ask and answer questions in `thread-{task_id}.md` using normal language and `@slug` mentions. Treat these threads as the conversation with each other.
6. Director monitors logs, inbox status, and task threads. If blocked, ask a clarifying question in the thread or reassign with an explicit reason.
7. Before finalizing, run the validation commands appropriate to the repo. Record exact commands and outcomes.
8. Finalize only when all tasks are `done` or explicitly `failed`, validation evidence exists, and `final-summary.md` is written.

**Safety rules:**
- Do not overwrite another agent's active inbox (`unread`, `claimed`, `running`, `blocked`, `review`) unless the user explicitly approves force.
- Do not edit files outside a task's allowed file list without asking in the thread and receiving director approval.
- Do not mark a task `done` unless the owning agent reported completion or the director verified the work.
- Do not release the director lock until final validation has been recorded.

---

## Command: /collab assign [ai-name] [task description]

Write a task directly to another AI's inbox so it can be picked up by that AI's per-agent monitor/adapter path — without the user having to copy prompts between IDEs.

**Inbox files:**
- `.ai-collab/inbox-{ai-name}.md` — task for a specific AI (e.g. `inbox-opencode.md`, `inbox-codex.md`)
- `.ai-collab/inbox-all.md` — broadcast to all AIs

**Steps:**

1. Find project root.
2. Generate a fresh `task_id` from the current UTC timestamp + target slug + short task slug. Format: `YYYYMMDD-HHMMSS-{slug}-{short-description}`. Example: `20260512-143015-codex-fix-daemon`.
3. Check whether `.ai-collab/inbox-{ai-name}.md` already exists. If it does AND its current `status` is not `done` or `failed`, STOP and warn the user: "Existing task in inbox-{ai-name}.md is still {status}. Overwrite? (y/n)" Only proceed on explicit confirmation.
4. Write `.ai-collab/inbox-{ai-name}.md` with this frontmatter:

```yaml
---
from: Claude Code
to: {ai-name}
task_id: {generated task_id}
priority: critical | high | normal | low
updated: {ISO-8601 UTC timestamp}
status: unread
attempts: 0
last_attempt:
claimed_by:
claimed_at:
done_at:
---
## Task
{detailed task description with files, constraints, and exit criteria}
```

5. Confirm: "Task written to inbox-{ai-name}.md (task_id: {task_id}) — the daemon will record a wake event for {ai-name}; with an execution adapter configured it can wake automatically, otherwise {ai-name} will pick it up on next response."

**Schema fields (all required):**

| Field         | Purpose                                                                   |
|---------------|---------------------------------------------------------------------------|
| `task_id`     | Durable identifier. Never changes after creation. Used by daemon dedup.   |
| `status`      | Lifecycle state: `unread → claimed → running → blocked → done \| failed`. |
| `attempts`    | Wakeup attempts counter (incremented by daemon or adapter on each wake).  |
| `last_attempt`| ISO timestamp of most recent wake attempt (empty until first attempt).    |
| `claimed_by`  | Slug of the agent that claimed the task (empty until claimed).            |
| `claimed_at`  | ISO timestamp when claim happened (empty until claimed).                  |
| `done_at`     | ISO timestamp when status moved to `done` (empty until done).             |

**For broadcast to all AIs:**

```
/collab assign all [task]
→ writes to inbox-all.md with the same schema, to: all
```

**How other AIs respond:** Every AI checks `inbox-{its-name}.md` and `inbox-all.md` at the start of every response. If `status: unread`, the agent first sets `status: claimed` + `claimed_by` + `claimed_at`, then executes the task, then marks `status: done` and sets `done_at`. The installed daemon also scans inboxes and task threads, creating wake events for direct tasks and `@slug` mentions so configured adapters can activate each agent's monitor path. See `claude-task-lifecycle-spec.md` for the full state machine, conflict resolution, and director semantics.

**Director rules (when reassigning or overriding):**

- Do not overwrite a `running` task that is making progress. Wait for the daemon's stale-claim timeout (30 min default).
- Moving `blocked` → `unread` requires a reason in the task body or thread file.
- Marking a task `failed` is allowed when the agent reports non-recoverable error or `attempts >= MAX_ATTEMPTS`.
- `done` and `failed` are terminal. To re-do work, issue a NEW task with a new `task_id`.

---

## Command: /collab monitor

Start or restart the current Claude Code session's live monitor for this project. `/collab setup` must start this automatically; this command exists for recovery, debugging, or manual restart after closing/reopening Claude Code.

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
    [[ "$BASENAME" == claude-code-* || "$BASENAME" == claude-* ]] && continue
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
