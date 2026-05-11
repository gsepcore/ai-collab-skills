# AI Collab Skill

**A Claude Code skill that lets multiple AI assistants work on the same project simultaneously — and actually see each other.**

When you use Claude Code alongside Cursor, Windsurf, Codex, OpenCode, or any other AI tool, they are completely blind to each other. This skill creates a shared filesystem protocol so they can read and write context in real time — no external service, no API, no internet required. Just a `.ai-collab/` directory inside your project.

---

## How it works

Each AI writes a Markdown session log to `{project-root}/.ai-collab/`. Any AI with access to the project filesystem can read those logs instantly. Claude manages its own log via this skill. Other AIs participate via simple snippets added to their rules files (`.cursorrules`, `.windsurfrules`, etc.) — one-time setup per project.

```
your-project/
└── .ai-collab/
    ├── PROTOCOL.md                   ← shared protocol (auto-created)
    ├── claude-20260511-143022.md     ← Claude Code's log
    ├── cursor-20260511-141500.md     ← Cursor's log
    ├── codex-20260511-141000.md      ← Codex's log
    └── opencode-20260511-140500.md   ← OpenCode's log
```

---

## Installation

### Step 1 — Install the skill files

```bash
mkdir -p ~/.claude/skills/collab/references

curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/SKILL.md \
  -o ~/.claude/skills/collab/SKILL.md

curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/references/protocol.md \
  -o ~/.claude/skills/collab/references/protocol.md
```

Or clone and copy manually:

```bash
git clone https://github.com/gsepcore/ai-collab-skills.git
mkdir -p ~/.claude/skills/collab/references
cp ai-collab-skills/SKILL.md ~/.claude/skills/collab/SKILL.md
cp ai-collab-skills/references/protocol.md ~/.claude/skills/collab/references/protocol.md
```

### Step 2 — Set up your project

Open Claude Code inside your project and run:

```
/collab setup
```

This will:
- Create `{project-root}/.ai-collab/`
- Add `.ai-collab/` to `.gitignore` automatically
- Copy the `PROTOCOL.md` into the shared directory
- Ask which other AI tools you use and generate the rules snippets for each one

### Step 3 — Set up other AIs

Tell each other AI assistant:

> *"Read `.ai-collab/PROTOCOL.md` in this project and write your session log following the protocol."*

Or paste the ready-made snippets from `examples/` directly into their rules files. See the [Supported AI Tools](#supported-ai-tools) table below.

---

## Commands

### `/collab read`

Read all session logs from other AIs working on this project.

Shows: AI name, last update time, active/idle/stale status, and the full log content. Highlights files marked as "Do Not Touch" so you know what to avoid.

```
/collab read
```

### `/collab write [optional note]`

Save Claude's current conversation context to the shared directory.

Creates or updates `.ai-collab/claude-{YYYYMMDD-HHMMSS}.md` with what you are working on, files modified, decisions made, bugs found, and anything other AIs should know.

```
/collab write
/collab write "finished auth refactor, starting on tests"
```

### `/collab status`

One-line overview of every AI active on this project — name, last update, and status indicator.

- 🟢 Active — updated less than 1 hour ago
- 🟡 Idle — updated 1–4 hours ago
- 🔴 Stale — updated more than 4 hours ago

```
/collab status
```

### `/collab setup`

First-time setup for a project. Run this once per project.

- Creates `.ai-collab/` directory
- Adds it to `.gitignore`
- Copies `PROTOCOL.md` into the directory
- Asks which AI tools you use and generates the rules snippets
- Writes Claude's first log entry

```
/collab setup
```

### `/collab clear`

Remove stale session logs.

```
/collab clear          # removes logs older than 24 hours
/collab clear --all    # removes all logs except PROTOCOL.md (asks for confirmation)
```

---

## Live monitoring (auto-notify)

To have Claude automatically notify you when another AI updates their log — without you having to ask — run this in Claude Code:

```
/loop Revisa .ai-collab/ y notifica si otra IA actualizó su log
```

This runs a background loop that checks every ~2 minutes and notifies you only when there is something new.

### Stop the live monitor

```
/collab status
```

The status command shows active monitors. To stop one:

```bash
# In Claude Code, use the task ID from /collab status
TaskStop <task-id>
```

Or simply close and restart your Claude Code session — session-only monitors stop automatically.

### Stop the cron job

If you set up a cron-based check with `/loop` and a specific interval (e.g. `/loop 2m ...`), Claude Code will give you a job ID like `47e8b12d`. Cancel it with:

```
CronDelete 47e8b12d
```

Or tell Claude: *"stop the collab cron"* and it will cancel it for you.

---

## Uninstalling

### Remove from a specific project

```bash
rm -rf {project-root}/.ai-collab/
```

And remove the `.ai-collab/` line from `.gitignore` if you added it.

For other AI tools: remove the `## AI Collab Protocol` block from `.cursorrules`, `.windsurfrules`, or `.github/copilot-instructions.md`.

### Remove the skill from Claude Code

```bash
rm -rf ~/.claude/skills/collab/
```

The skill will no longer appear in Claude's available skills. Any `.ai-collab/` directories in your projects are independent — removing the skill does not delete them.

---

## Supported AI tools

| Tool | Rules file | Example snippet |
|------|-----------|----------------|
| **Cursor** | `.cursorrules` | `examples/cursorrules.example` |
| **Windsurf** | `.windsurfrules` | `examples/windsurfrules.example` |
| **GitHub Copilot** | `.github/copilot-instructions.md` | `references/protocol.md` → Copilot section |
| **OpenCode** | System prompt / rules | `references/protocol.md` → OpenCode section |
| **Codex / GPT** | System prompt | `references/protocol.md` → Codex section |
| **Any AI** | Paste the generic snippet | `references/protocol.md` → Generic section |

Want to add support for a new tool? See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## The log format

All AIs write session logs in this structure:

```markdown
---
ai: Claude Code (claude-sonnet-4-6)
session: 20260511-143022
project: my-project
updated: 2026-05-11 14:30:22
---

## Working On
Fixing the authentication timeout in src/auth.ts — JWT tokens expire too early on slow connections.

## Files Modified This Session
- `src/auth.ts` — increased token expiry from 5min to 15min, added refresh logic

## Decisions Made
- 15min JWT expiry — balances security with UX for slow network users

## Issues Identified
- `src/auth.ts:42` — refresh logic does not handle concurrent requests (race condition)

## Still In Progress
- Unit tests for the refresh flow

## Do Not Touch (Avoid Conflicts)
- `src/auth.ts` — currently being refactored, coordinate before editing

## Handoff Note
Auth timeout fix is complete. The refresh race condition on line 42 is the next thing to address — needs a mutex or debounce. Tests are not written yet.
```

---

## Coordination rules

All AIs following this protocol must respect these rules:

1. **Do Not Touch is binding** — if a file appears in another AI's Do Not Touch section, ask the user before editing it
2. **No silent overrides** — if you disagree with another AI's decision, tell the user; do not silently change the code
3. **Announce context at session start** — always tell the user what you found in other AIs' logs
4. **Update your log when things change** — do not wait until the end of the session
5. **Language** — write in English or the user's language; never mix writing systems

---

## Troubleshooting

**`/collab read` shows nothing**
Run `/collab setup` first. The `.ai-collab/` directory may not exist yet.

**Another AI is not writing logs**
Make sure the `## AI Collab Protocol` snippet is in their rules file and they have read `PROTOCOL.md`. Tell them explicitly: *"Write your session log to `.ai-collab/{ai-name}-{timestamp}.md`"*.

**Logs are showing as stale immediately**
The AI tool may be writing logs with an old timestamp in the frontmatter. Check that `updated:` in the log matches the actual modification time.

**Monitor fires too often or not at all**
Adjust the check interval: `/loop 5m revisa .ai-collab/...` for every 5 minutes, or stop and restart with a different interval.

**`.ai-collab/` was committed to git**
Add `.ai-collab/` to your `.gitignore`. Run `git rm -r --cached .ai-collab/` to remove it from tracking without deleting the files locally.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

The most valuable contributions are support for new AI tools — add a snippet to `references/protocol.md`, an example file to `examples/`, and update the table above.

---

## License

MIT — created by [Luis Alfredo Velasquez Duran](https://github.com/LuisvelMarketer) / [gsepcore](https://github.com/gsepcore)
