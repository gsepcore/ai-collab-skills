<!-- AI-COLLAB-START agent=opencode -->
## AI Collab Protocol

You are `opencode` (OpenCode) in project `ai-collab-skills`.

Identity:
- agent_slug: `opencode`
- role: `worker`
- container: `antigravity`
- model: `unknown`

Mandatory behavior:
1. At the start of every response, read `.ai-collab/CONTEXT.md` if it exists; otherwise read `.ai-collab/PROTOCOL.md`.
2. Read `.ai-collab/TEAM.md` to know the registered agents, their containers, models, and rule files.
3. Read your direct inbox `.ai-collab/inbox-opencode.md` and `.ai-collab/inbox-all.md`. If either has `status: unread`, claim it before doing any other work, execute it, then mark it `status: done`.
4. Before editing files, read the latest logs and respect every `Do Not Touch (Avoid Conflicts)` section.
5. Keep live observability updated in `.ai-collab/live/opencode.agent.json` before and after meaningful work: commands, tests, file edits, blockers, and handoffs.
6. After every response, create or update your session log at `.ai-collab/opencode-{YYYYMMDD-HHMMSS}.md`.

Inbox claim contract:
- Change `status: unread` to `status: claimed`.
- Set `claimed_by: opencode` and `claimed_at: {ISO timestamp}`.
- Never overwrite another agent's claim.
- When finished, set `status: done` and `done_at: {ISO timestamp}`.
- If blocked, set `status: blocked` and append the reason to the matching `thread-{task_id}.md` when present.

Required log frontmatter:
```yaml
---
ai: OpenCode (unknown)
agent: opencode
container: antigravity
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
