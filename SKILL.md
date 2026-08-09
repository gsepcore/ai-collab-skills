---
name: collab
description: Enable real-time collaboration and directed implementation runs between multiple AI coding agents (Claude Code, OpenCode, Codex, Aider, Hermes, Cursor native chat, Windsurf native chat, Copilot Chat, etc.) working on the same project simultaneously, regardless of IDE/container or LLM model. Use this skill when the user wants agents to share context, receive tasks, avoid conflicting changes, configure a persistent development team with roles such as senior director, frontend, backend, database, DevOps, QA, security, deployment, or UI/UX design, or route an orchestrated implementation by those roles. Triggers on /collab, /collab team, /collab orchestrate, "assign roles to my agents", "set up my AI development team", "reparte las tareas", "asigna roles", "equipo de desarrolladores", "multi-AI", "collab", "comparte contexto", "haz un plan con varios agentes", or "Codex como director".
---

# AI Collab Skill

Shared filesystem protocol so every AI coding agent working on the same project can read and write context in real time. No external service, no API — just a `.ai-collab/` directory inside the project.

---

## How it works

Each AI writes a Markdown log to `{project-root}/.ai-collab/`. Any AI with filesystem access to the project can read those logs. Claude manages its own log via this skill. Other agents (OpenCode, Codex, Aider, Cursor native chat, etc.) write via agent-specific rules installed by `~/.claude/ai-collab-project-setup.py`.

Agents can also hold natural conversations: task-specific threads live at `.ai-collab/thread-{task_id}.md`, and broader design/review discussions live at `.ai-collab/discussions/*.md`. `@slug` mentions in either place are scanned by the daemon and can wake the mentioned agent.

Every onboarded project also has `.ai-collab/capabilities.json`. Each agent must read it during preflight so it knows the available internal channels, its exact visible adapter, whether that route is verified or degraded, its visual-evidence duties, and how to contact a sleeping director. Delivery is internal-first: write the inbox/thread, allow the configured short grace period for a real response, notify the user/director before escalation, then target only the non-responsive agents in their visible chats.

The installed daemon also writes semantic live snapshots to `{project-root}/.ai-collab/live/`. These are the project-scoped "eyes" layer: current inbox/task state, latest log summary, self-reported commands/edits from each agent, process hints tied to the current project, git dirty files, director alerts, `health.json`, automatic project-window screenshots, and `.semantic.json` screenshot sidecars unless `AI_COLLAB_OBSERVER_SCREENSHOTS=0`. The installer attempts to install the local OCR engine (`tesseract`) by default; if unavailable, vision remains functional in metadata-only mode and `health.json` reports the degradation.

The daemon also runs the self-updater and reboot recovery by default. The self-updater refreshes the global `~/.claude` install from the configured GitHub branch, then re-applies managed `AI-COLLAB-START` / `AI-COLLAB-END` rule blocks in already-onboarded projects and refreshes generated `PROTOCOL.md` files with backups. Recovery refreshes stale/missing `CONTEXT.md` files and removes stale wakeup dedupe entries for unfinished inbox tasks after restart/session loss. Set `AI_COLLAB_AUTO_UPDATE=0` or `AI_COLLAB_RECOVERY=0` to disable either layer, or tune `AI_COLLAB_UPDATE_INTERVAL_SECONDS` / `AI_COLLAB_RECOVERY_INTERVAL_SECONDS`.

For Codex/Antigravity automation, the installer includes a local bridge API at `~/.claude/ai-collab-codex-bridge.py`. Use it when another agent needs a stable API-shaped way to address Codex. It writes normal `.ai-collab/discussions/` messages and routes to `codex-auto` background mode, `antigravity-chat` visible mode through the official `antigravity-ide chat --reuse-window` CLI when available, `codex-filesystem` deterministic wake receipt, or `notify-only`. `codex-auto` tries ACP first, then a real non-interactive `codex exec` worker, then the degraded filesystem receipt. A successful visible submission still is not a Codex response; require Codex's own authored message. Read `references/codex-antigravity-bridge.md` before changing or relying on this bridge.

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
5. If `summary.json` has `conversations`, show open discussions/task threads with topic, participants, latest author, latest excerpt, and path.
6. Read `{root}/.ai-collab/live/health.json` if present and report:
   - overall health
   - screenshot/window/OCR checks that are not `ok`
   - recommendations
7. If the latest screenshot has a `semantic.path`, read that sidecar and summarize:
   - project match
   - OCR status
   - inferred state (`error`, `waiting-for-input`, `testing`, `editing`, `running`, `unknown`, etc.)
   - text excerpt if present
8. Read `{root}/.ai-collab/live/director-alerts.jsonl` if present and print the latest 5 alerts first.
9. If screenshots are not present, mention they are enabled by default but require a visible window matching the current project fingerprint, macOS Screen Recording permission, a supported macOS host, and `AI_COLLAB_OBSERVER_SCREENSHOTS` not being set to `0`.
10. Whenever a screenshot is captured or inspected for the user, make visual verification part of the deliverable:
    - For an explicit capture request, take a fresh screenshot; do not silently reuse a stale image.
    - Create a reasonably sized thumbnail when the original is too large for inline display.
    - Embed the thumbnail explicitly in the user-visible final response with Markdown image syntax, and include a link to the original image.
    - Do not rely on an internal image-inspection tool result or a filesystem path alone; those may not render for the user.
    - State the capture time and briefly summarize what is visibly present.
    - If the client cannot render or attach the thumbnail, say so clearly instead of claiming it was shown.

---

## Command: /collab converse

Start or continue a natural agent-to-agent discussion for questions, proposals, reviews, blockers, decisions, and handoffs.

This is an internal-first execution contract, not a narration feature. With recipients in `--to`, the helper writes the shared thread first and waits the configured short grace period. Agents that answer internally are never disturbed in their visible chat. For each non-responsive agent, the helper prints and records a notice, focuses the exact surface without submitting when the bridge supports it, captures the real project window, verifies every required surface in `.ai-collab/live/visual-roster.json`, appends the visual fallback context, and only then submits the prompt. If an already-running legacy bridge lacks focus-only support, the exact-terminal submission focuses the pane, immediate visual proof is mandatory, and an evidence-bearing follow-up completes the handoff. Every recipient must inspect that image and attest what it saw in the shared thread. A file write, port, process, log, prompt submission, or wake event alone is never a response. Any mismatch fails closed without a hidden CLI substitute.

**Steps:**
1. Find project root.
2. Prefer the deterministic helper:

   ```bash
   python3 ~/.claude/ai-collab-converse.py --root "$ROOT" start --author claude-code --topic "$TOPIC" --to "$AGENTS" --message "$MESSAGE" --wait-seconds 180
   ```

   If the helper is not installed, fall back to appending the same heading format manually.
3. Use task threads for task-bound work:

   ```bash
   python3 ~/.claude/ai-collab-converse.py --root "$ROOT" start --kind task --task-id "$TASK" --author claude-code --to opencode --type question --topic "$TOPIC" --message "$MESSAGE"
   ```

4. Continue a conversation with typed messages:

   ```bash
   python3 ~/.claude/ai-collab-converse.py --root "$ROOT" question --thread "$THREAD" --author claude-code --to opencode --message "$QUESTION"
   python3 ~/.claude/ai-collab-converse.py --root "$ROOT" proposal --thread "$THREAD" --author opencode --to codex --message "$PROPOSAL"
   python3 ~/.claude/ai-collab-converse.py --root "$ROOT" decision --thread "$THREAD" --author codex --message "$DECISION"
   ```

5. Keep the same thread open for follow-up questions, progress requests, blockers, reviews, and handoffs until a terminal result exists. Do not reduce the collaboration to isolated wakeups.
6. Require every visibly escalated agent to inspect the fresh screenshot and append a substantive, agent-authored reply with its opinion, risks, `visual_evidence: <path>`, and `visible_peers: <slugs>`. `--wait-seconds` exits nonzero when a real reply or visual attestation is missing.
7. Require the automatic post-turn screenshot. Use only these delivery claims: `queued internally`, `internal response`, `escalating visibly`, `submitted visibly`, `responded`, or `failed`. Say `responded` only after the thread contains that agent's own compliant message.
8. Use `/collab observe` to inspect `visual-roster.json`, agent/process/TTY/port ownership, open conversations, and latest replies.

---

## Command: /collab setup

Use one idempotent command for first-time installation, global updates, existing-project migration, and final health verification.

**Steps:**

1. Find project root.
2. Run the deterministic unified setup helper:

   ```bash
   python3 ~/.claude/ai-collab-setup.py --root "$ROOT"
   ```

   If the helper is not installed but this repository is available, run `python3 install/ai-collab-setup.py --root "$ROOT" --installer-source .`. Otherwise bootstrap once with the published installer and then rerun `/collab setup`.
3. Let the helper perform the complete lifecycle in this order:
   - reinstall/update both Claude and Codex skill copies, helpers, hooks, daemon services, Codex bridge, and visible IDE bridge from the current release
   - suppress the installer's nested project onboarding to prevent recursion
   - preserve the existing `agents.json` roster, models, container, custom agents, roles, inboxes, runs, task threads, discussions, and user-authored rule content
   - refresh `PROTOCOL.md` with a timestamped backup and replace only managed AI Collab marker blocks
   - regenerate `TEAM.md`, `agents.json`, `capabilities.json`, and relevant runtime rule blocks
   - run strict global doctor checks and project capability checks
   - write `.ai-collab/setup-report.json` with before/after fingerprints, preservation results, migration status, and reload guidance
4. Ask/record project values only when they are not already present:
   - IDE/container: `antigravity`, `cursor`, `vscode`, `windsurf`, `terminal`, `other`
   - agents: `claude-code`, `opencode`, `codex`, `aider`, `hermes`, `cursor-native`, `windsurf-native`, `copilot-chat`, or custom
   - LLM model for each agent, e.g. `openai/gpt-5.5`, `anthropic/claude-opus-4.7`, `minimax/m2.7`
5. Verify these files exist after setup:
   - `.ai-collab/PROTOCOL.md`
   - `.ai-collab/TEAM.md`
   - `.ai-collab/agents.json`
   - `.ai-collab/capabilities.json`
   - `.ai-collab/roles.json` after development-team role onboarding
   - `.ai-collab/inbox-all.md`
   - the relevant agent rules files
6. Report what was done in one line per agent:

   ```
   ✓ claude-code → CLAUDE.md (created/appended)
   ✓ opencode    → .opencode/rules/ai-collab.md + AGENTS.md (created/appended)
   ✓ codex       → AGENTS.md (created/appended)
   ✓ cursor-native → .cursorrules (created/appended)
   ```

7. For an already-running project, start one setup-refresh discussion with every registered agent. Ask each agent to read its refreshed rule block plus `.ai-collab/capabilities.json` and append its own acknowledgement. Apply the internal-first grace period and visible fallback; do not claim an agent refreshed until its agent-authored acknowledgement exists.
8. Run `/collab write` immediately to log the current context.
9. Start `/collab monitor` automatically for this project in the current Claude Code session. Do not ask the user to run it manually. If a monitor for this project is already active, keep it and report "monitor already active." If the current runtime cannot launch a persistent Monitor/Task, say that clearly and rely on the installed daemon + prompt hooks as the fallback.
10. If `.ai-collab/roles.json` does not exist, run `/collab team configure`. Show every registered agent and ask the user to choose one primary owner for each standard development role. Allow one agent to own multiple roles and allow explicit vacancies.
11. Summarize the global install, registered agents, containers, models, development-team roles, preservation audit, exact rules files, doctor result, agent acknowledgements, and whether one IDE window reload is recommended.

**Re-run behavior:** Treat every invocation as install-or-migrate. Re-running it updates the global installation and current project to the same release, adds newly detected agents, refreshes managed blocks without duplication, and fails honestly if an existing inbox, run, role file, task thread, or discussion changed during migration. Never remove user-authored content or collaboration history.

## Command: /collab update

Force an immediate update of the installed skill/scripts and managed project snippets.

**Steps:**
1. Find project root.
2. Run:

   ```bash
   python3 ~/.claude/ai-collab-update.py --project "$ROOT"
   ```

3. Report:
   - changed global install files
   - refreshed project roots
   - any download or project refresh errors

**Behavior:** The normal daemon already runs this periodically when `AI_COLLAB_AUTO_UPDATE` is not `0`. Use `/collab update` for immediate recovery after a release or when a user suspects stale project rules.

## Command: /collab codex-bridge

Expose a localhost API facade that other agents can call to address Codex.

**Steps:**
1. Read `references/codex-antigravity-bridge.md`.
2. Start the bridge:

   ```bash
   python3 ~/.claude/ai-collab-codex-bridge.py serve --host 127.0.0.1 --port 8765
   ```

3. Tell callers to POST to `/v1/codex/message` with `project_path`, `from_agent`, `topic`, `message`, and `mode`.
4. Be explicit about visibility:
   - `mode: background` uses `codex-auto`: try `codex-acp`, then a real non-interactive `codex exec` worker, then fall back to a degraded deterministic filesystem receipt.
   - `mode: visible` uses `antigravity-chat` through `antigravity-ide chat --reuse-window`; it fails closed when that CLI is unavailable.
   - `mode: codex-filesystem` proves wake delivery through `.ai-collab` files without claiming visible-session control.

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

## Command: /collab team [configure | show]

Create or update a persistent development-team profile after agents are registered.

**Configure:**

1. Read `.ai-collab/agents.json` and `.ai-collab/TEAM.md`; use their exact agent slugs.
2. Run `python3 ~/.claude/ai-collab-team.py --root "$ROOT" configure` in an interactive terminal.
3. Present every registered agent for each standard role: senior director, frontend, backend, database, DevOps, QA, security review, architecture review, functional review, deployment, and UI/UX design.
4. Let one agent own multiple roles. Accept `unassigned` for vacancies and never route work to a vacancy without asking the user.
5. Persist the result in `.ai-collab/roles.json` and the generated Development Team Roles section of `TEAM.md`.
6. Treat roles as default routing, not an unbreakable permission boundary. An explicit user/director owner overrides the profile.

For deterministic non-interactive configuration, repeat `--assign`:

```bash
python3 ~/.claude/ai-collab-team.py --root "$ROOT" configure --non-interactive --replace \
  --assign senior-director=codex \
  --assign frontend=claude-code \
  --assign backend=claude-code \
  --assign database=claude-code \
  --assign devops=opencode \
  --assign qa=opencode \
  --assign security-review=opencode \
  --assign architecture-review=opencode \
  --assign functional-review=opencode \
  --assign deployment=opencode \
  --assign ui-ux-design=unassigned
```

Use `python3 ~/.claude/ai-collab-team.py --root "$ROOT" show` to display the current profile. When a new or replacement agent joins, onboard it first, then rerun team configuration only for the roles that should move.

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
- Otherwise use the primary owner of `senior-director` from `.ai-collab/roles.json` when configured.
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
python3 ~/.claude/ai-collab-orchestrate.py convene --run-id "$RUN_ID" --actor "$DIRECTOR" --participants "$AGENTS" --message "$GOAL Ask each agent for its technical opinion, risks, and recommended approach." --wait-seconds 180
python3 ~/.claude/ai-collab-orchestrate.py add-task --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK" --title "$TITLE" --owner "$AGENT" --allowed-files "$FILES" --description "$DESC" --validation "$VALIDATION"
python3 ~/.claude/ai-collab-orchestrate.py add-task --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK" --title "$TITLE" --role frontend --allowed-files "$FILES" --description "$DESC" --validation "$VALIDATION"
python3 ~/.claude/ai-collab-orchestrate.py assign --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK"
python3 ~/.claude/ai-collab-orchestrate.py thread --run-id "$RUN_ID" --task-id "$TASK" --author "$AGENT" --message "$MESSAGE"
python3 ~/.claude/ai-collab-orchestrate.py set-task --run-id "$RUN_ID" --actor "$DIRECTOR" --task-id "$TASK" --status done --summary "$SUMMARY"
python3 ~/.claude/ai-collab-orchestrate.py finalize --run-id "$RUN_ID" --actor "$DIRECTOR" --summary "$SUMMARY" --validation "$VALIDATION"
```

**Execution workflow:**
1. Read `.ai-collab/CONTEXT.md`, `TEAM.md`, `capabilities.json`, `roles.json`, active inboxes, and recent logs.
2. Create the run with the selected director and participating agents. If none were explicitly selected, use the senior director and assigned role owners from `roles.json`.
3. Convene the visible team. The helper must force a fresh pre-turn screenshot, build an immutable per-capture visual roster plus `.ai-collab/live/visual-roster.json`, and refuse dispatch unless every requested agent is visible in the correct project surface. Each agent must inspect the actual PNG with native vision or `ai-collab-see.py`, which directly processes the pixels for models without image input.
4. Wait for a real thread reply from every participant. Every reply must include `visual_evidence:` and `visible_peers:`; after replies, require a second fresh visual proof showing the project interfaces. Show the user the actual agent-authored recommendations. Never paraphrase a missing or visually unverified reply as if that agent provided it.
5. Write a concrete `PLAN.md`: tasks, dependencies, required roles, owners, allowed files, and validation.
6. Add and assign tasks with one owner each. Prefer `--role` for default routing; use `--owner` for an explicit override. Assignment writes the inbox/thread first, waits briefly for a claim, announces non-response, then escalates only that agent to its verified visible route. It exits nonzero when the visible route rejects the prompt.
7. Agents ask and answer questions in `thread-{task_id}.md` using normal language and `@slug` mentions. Keep progress, doubts, recommendations, reviews, and handoffs in that same continuous conversation.
8. Director monitors logs, inbox status, and task threads. If blocked or progress is stale, ask internally first and follow the same notice-before-visible-fallback contract. When the director is stale/sleeping, workers use the director's `capabilities.json` visible route; native Codex must be contacted through its visible chat and a degraded route must be reported honestly.
9. Before finalizing, run the validation commands appropriate to the repo. Record exact commands and outcomes.
10. Finalize only when all tasks are `done` or explicitly `failed`, validation evidence exists, and `final-summary.md` is written.

**Safety rules:**
- Do not overwrite another agent's active inbox (`unread`, `claimed`, `running`, `blocked`, `review`) unless the user explicitly approves force.
- Do not edit files outside a task's allowed file list without asking in the thread and receiving director approval.
- Do not mark a task `done` unless the owning agent reported completion or the director verified the work.
- Do not release the director lock until final validation has been recorded.
- Never say an agent is working because a task file was written or a prompt was submitted. `started` requires the agent's own claim/live update; `responded` requires its authored thread message; `completed` requires its done state and handoff.
- Never silently fall back to headless execution when the user requested visible multi-agent work.
- Ports and logs are corroborating evidence, never substitutes for sight. The visual roster must map agent ↔ project ↔ visible surface ↔ PID/TTY ↔ agent-owned port, and label IDE-bridge ports as routing infrastructure rather than pretending they belong to an agent.
- Evidence depends on surface type: terminals require one exact project process/PID/TTY and any agent-owned port; native IDE chats intentionally share the outer IDE host. Require that host PID to be an ancestor of the exact project bridge, then bind the agent with `registered-shared-project-host+position-bound-top-band-label` plus actual pane pixels. Never invent a child process or port.
- A visible workflow fails closed if the screenshot is missing/stale, OCR cannot identify a required surface, process/project identity is ambiguous, an agent cannot inspect the image, or pre/post visual evidence disagrees.

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

5. Wait the `capabilities.json` internal grace period for the target to claim/respond. If it does not, tell the user/director which agent did not respond and that visible fallback is starting, then submit to that agent's exact visible project chat. Never report prompt submission as a response.
6. Confirm one precise state: `queued internally`, `internal response`, `submitted visibly`, `responded`, or `failed`, with the matching inbox/thread/adapter evidence.

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

**Sleeping-director rule:** Workers normally reply through the inbox/thread/log. If they have progress, a question, a blocker, or a completed implementation and the director's live state is missing/stale, they must use the director's declared visible route from `.ai-collab/capabilities.json`. A native-chat-only director such as Codex is addressed in its visible chat; if that inbound route cannot be verified, record and surface the failure instead of fabricating a wake or answer.

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
