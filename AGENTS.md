<!-- AI-COLLAB-START agent=opencode -->
## AI Collab Protocol

You are `opencode` (OpenCode) in project `ai-collab-skills`.

Identity:
- agent_slug: `opencode`
- role: `worker`
- container: `unknown`
- model: `unknown`

Mandatory preflight before EVERY response, analysis, or tool action:
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read your direct inbox `.ai-collab/inbox-opencode.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
4. Read recent task threads `.ai-collab/thread-*.md` and natural discussions `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant. Answer direct `@opencode` mentions before unrelated work.
5. Read the latest session logs in `.ai-collab/*.md` from other agents, skipping `PROTOCOL.md`, `CONTEXT.md`, `TEAM.md`, inbox files, and your own current-session log. Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing.
6. If `.ai-collab/live/summary.json` exists, read it for current agent phases, dirty files, alerts, and open conversations before making coordination decisions.
7. Keep live observability updated in `.ai-collab/live/opencode.agent.json` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
8. After every response, create or update your session log at `.ai-collab/opencode-{YYYYMMDD-HHMMSS}.md`.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: opencode` and `claimed_at: {ISO timestamp}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {ISO timestamp}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{task_id}.md` when present.

Natural conversation contract:
- Use `python3 ~/.claude/ai-collab-converse.py` when you need another agent's judgement instead of hiding the question in a private log.
- Ask concrete questions with `question --to other-agent`, propose implementation options with `proposal`, record accepted choices with `decision`, and mark blockers with `blocker`.
- Mention agents explicitly with `@slug`; the daemon wakes the mentioned agent from task threads and `.ai-collab/discussions/*.md`.
- Do not edit previous messages. Correct yourself by appending a new message.

Required log frontmatter:
```yaml
---
ai: OpenCode (unknown)
agent: opencode
container: unknown
model: unknown
session: {YYYYMMDD-HHMMSS}
project: ai-collab-skills
updated: {ISO timestamp}
---
```

Required log sections:
- `## Working On`
- `## Files Read This Session`
- `## Files Modified This Session`
- `## Decisions Made`
- `## Issues Identified`
- `## Still In Progress`
- `## Do Not Touch (Avoid Conflicts)`
- `## Handoff Note`

Live observability contract:
- Before running a shell command, atomically write `.ai-collab/live/opencode.agent.json` with JSON fields: `agent`, `project`, `updated`, `phase: "command"`, `current_command`, `task_id` if any, and `files_in_scope`.
- After the command finishes, append one JSON line to `.ai-collab/live/opencode.agent.events.jsonl` with: `timestamp`, `agent`, `event: "command"`, `command`, `exit_code`, and a short `output_excerpt` when available.
- Before editing files, update `.ai-collab/live/opencode.agent.json` with `phase: "editing"` and `files_in_scope`.
- When blocked, set `phase: "blocked"` and include `blocker`.
- When idle or finished, set `phase: "idle"` or `phase: "done"` with a concise `summary`.
- Use atomic writes for `.ai-collab/live/opencode.agent.json` (temp file + rename). Append-only is OK for `.ai-collab/live/opencode.agent.events.jsonl`.

Write only in English or the user's language. Do not mix unrelated languages.
<!-- AI-COLLAB-END agent=opencode -->

<!-- AI-COLLAB-START agent=codex -->
## AI Collab Protocol

You are `codex` (Codex) in project `ai-collab-skills`.

Identity:
- agent_slug: `codex`
- role: `worker`
- container: `unknown`
- model: `unknown`

Mandatory preflight before EVERY response, analysis, or tool action:
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read your direct inbox `.ai-collab/inbox-codex.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
4. Read recent task threads `.ai-collab/thread-*.md` and natural discussions `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant. Answer direct `@codex` mentions before unrelated work.
5. Read the latest session logs in `.ai-collab/*.md` from other agents, skipping `PROTOCOL.md`, `CONTEXT.md`, `TEAM.md`, inbox files, and your own current-session log. Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing.
6. If `.ai-collab/live/summary.json` exists, read it for current agent phases, dirty files, alerts, and open conversations before making coordination decisions.
7. Keep live observability updated in `.ai-collab/live/codex.agent.json` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
8. After every response, create or update your session log at `.ai-collab/codex-{YYYYMMDD-HHMMSS}.md`.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: codex` and `claimed_at: {ISO timestamp}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {ISO timestamp}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{task_id}.md` when present.

Natural conversation contract:
- Use `python3 ~/.claude/ai-collab-converse.py` when you need another agent's judgement instead of hiding the question in a private log.
- Ask concrete questions with `question --to other-agent`, propose implementation options with `proposal`, record accepted choices with `decision`, and mark blockers with `blocker`.
- Mention agents explicitly with `@slug`; the daemon wakes the mentioned agent from task threads and `.ai-collab/discussions/*.md`.
- Do not edit previous messages. Correct yourself by appending a new message.

Required log frontmatter:
```yaml
---
ai: Codex (unknown)
agent: codex
container: unknown
model: unknown
session: {YYYYMMDD-HHMMSS}
project: ai-collab-skills
updated: {ISO timestamp}
---
```

Required log sections:
- `## Working On`
- `## Files Read This Session`
- `## Files Modified This Session`
- `## Decisions Made`
- `## Issues Identified`
- `## Still In Progress`
- `## Do Not Touch (Avoid Conflicts)`
- `## Handoff Note`

Live observability contract:
- Before running a shell command, atomically write `.ai-collab/live/codex.agent.json` with JSON fields: `agent`, `project`, `updated`, `phase: "command"`, `current_command`, `task_id` if any, and `files_in_scope`.
- After the command finishes, append one JSON line to `.ai-collab/live/codex.agent.events.jsonl` with: `timestamp`, `agent`, `event: "command"`, `command`, `exit_code`, and a short `output_excerpt` when available.
- Before editing files, update `.ai-collab/live/codex.agent.json` with `phase: "editing"` and `files_in_scope`.
- When blocked, set `phase: "blocked"` and include `blocker`.
- When idle or finished, set `phase: "idle"` or `phase: "done"` with a concise `summary`.
- Use atomic writes for `.ai-collab/live/codex.agent.json` (temp file + rename). Append-only is OK for `.ai-collab/live/codex.agent.events.jsonl`.

Write only in English or the user's language. Do not mix unrelated languages.
<!-- AI-COLLAB-END agent=codex -->

<!-- AI-COLLAB-START agent=hermes -->
## AI Collab Protocol

You are `hermes` (Hermes) in project `ai-collab-skills`.

Identity:
- agent_slug: `hermes`
- role: `worker`
- container: `unknown`
- model: `unknown`

Mandatory preflight before EVERY response, analysis, or tool action:
1. Read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read your direct inbox `.ai-collab/inbox-hermes.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
4. Read recent task threads `.ai-collab/thread-*.md` and natural discussions `.ai-collab/discussions/*.md` where you are mentioned or listed as a participant. Answer direct `@hermes` mentions before unrelated work.
5. Read the latest session logs in `.ai-collab/*.md` from other agents, skipping `PROTOCOL.md`, `CONTEXT.md`, `TEAM.md`, inbox files, and your own current-session log. Respect every `Do Not Touch (Avoid Conflicts)` section before analyzing, replying, or editing.
6. If `.ai-collab/live/summary.json` exists, read it for current agent phases, dirty files, alerts, and open conversations before making coordination decisions.
7. Keep live observability updated in `.ai-collab/live/hermes.agent.json` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
8. After every response, create or update your session log at `.ai-collab/hermes-{YYYYMMDD-HHMMSS}.md`.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: hermes` and `claimed_at: {ISO timestamp}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {ISO timestamp}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{task_id}.md` when present.

Natural conversation contract:
- Use `python3 ~/.claude/ai-collab-converse.py` when you need another agent's judgement instead of hiding the question in a private log.
- Ask concrete questions with `question --to other-agent`, propose implementation options with `proposal`, record accepted choices with `decision`, and mark blockers with `blocker`.
- Mention agents explicitly with `@slug`; the daemon wakes the mentioned agent from task threads and `.ai-collab/discussions/*.md`.
- Do not edit previous messages. Correct yourself by appending a new message.

Required log frontmatter:
```yaml
---
ai: Hermes (unknown)
agent: hermes
container: unknown
model: unknown
session: {YYYYMMDD-HHMMSS}
project: ai-collab-skills
updated: {ISO timestamp}
---
```

Required log sections:
- `## Working On`
- `## Files Read This Session`
- `## Files Modified This Session`
- `## Decisions Made`
- `## Issues Identified`
- `## Still In Progress`
- `## Do Not Touch (Avoid Conflicts)`
- `## Handoff Note`

Live observability contract:
- Before running a shell command, atomically write `.ai-collab/live/hermes.agent.json` with JSON fields: `agent`, `project`, `updated`, `phase: "command"`, `current_command`, `task_id` if any, and `files_in_scope`.
- After the command finishes, append one JSON line to `.ai-collab/live/hermes.agent.events.jsonl` with: `timestamp`, `agent`, `event: "command"`, `command`, `exit_code`, and a short `output_excerpt` when available.
- Before editing files, update `.ai-collab/live/hermes.agent.json` with `phase: "editing"` and `files_in_scope`.
- When blocked, set `phase: "blocked"` and include `blocker`.
- When idle or finished, set `phase: "idle"` or `phase: "done"` with a concise `summary`.
- Use atomic writes for `.ai-collab/live/hermes.agent.json` (temp file + rename). Append-only is OK for `.ai-collab/live/hermes.agent.events.jsonl`.

Write only in English or the user's language. Do not mix unrelated languages.
<!-- AI-COLLAB-END agent=hermes -->
