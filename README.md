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

## Persistent monitoring (survives sleep, session close, and restarts)

For production use — this approach survives Mac sleep/wake, session restarts, and computer reboots.

### How it works

Three components work together:
1. **launchd daemon** — macOS system service that watches `.ai-collab/` every 15 seconds. Runs 24/7, auto-restarts on crash, resumes after sleep.
2. **Notifications file** — `~/.ai-collab-notifications.json` acts as a message queue. The daemon writes here; Claude reads here.
3. **Claude Code hook** — `UserPromptSubmit` hook checks the queue every time you type a message. If there are pending notifications, shows them and clears the queue.

### Setup

**Step 1 — Install the daemon script:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/daemon.sh \
  -o ~/.claude/ai-collab-daemon.sh && chmod +x ~/.claude/ai-collab-daemon.sh
```

**Step 2 — Register the launchd service:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/com.gsepcore.ai-collab.plist \
  -o ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
```

**Step 3 — Add the hook to your project:**
Add this to `.claude/settings.local.json` in your project (merge with existing settings):
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'FILE=\"$HOME/.ai-collab-notifications.json\"; if [ -f \"$FILE\" ]; then CONTENT=$(cat \"$FILE\"); if [ \"$CONTENT\" != \"[]\" ] && [ -n \"$CONTENT\" ]; then echo \"[AI-COLLAB] Pending notifications from other AIs:\"; echo \"$CONTENT\"; echo \"[]\" > \"$FILE\"; fi; fi'",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

After this setup, every time you write something in Claude Code, it automatically shows any pending notifications from other AIs — without polling, without cron jobs, without consuming tokens.

### Manage the daemon

```bash
# Check status
launchctl list | grep ai-collab

# Stop
launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist

# Start
launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist

# View logs
tail -f /tmp/ai-collab-daemon.log
```

### Uninstall daemon

```bash
launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
rm ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
rm ~/.claude/ai-collab-daemon.sh
rm ~/.ai-collab-notifications.json
rm ~/.ai-collab-last-check
```

---

## Live monitoring (auto-notify, zero token cost)

To have Claude automatically notify you the instant another AI updates their log, run:

```
/collab monitor
```

This starts a **persistent bash Monitor** that watches `.ai-collab/` every 20 seconds in the background. It consumes zero tokens while waiting — Claude only activates when a real change is detected. You get notified immediately, without polling and without cost.

> **Why not use `/loop` with a timer?**
> A cron or loop fires on a fixed interval and sends a prompt to Claude every N minutes regardless of whether anything changed. That consumes input tokens each tick — even for empty checks. The Monitor approach runs as a pure bash script and only wakes Claude on an actual file change.

### Stop the monitor

```
/collab status
```

This shows the active monitor task ID. Then:

```
TaskStop <task-id>
```

Or tell Claude: *"stop the collab monitor"* and it will stop it for you.

Closing your Claude Code session also stops the monitor automatically.

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

---

## Built by gsepcore

**[gsepcore](https://github.com/gsepcore)** builds open-source infrastructure for AI agents — tools that make agents more reliable, secure, and collaborative.

AI Collab Skill is part of the gsepcore ecosystem. If it saved you time, check out our other projects.

---

## GSEP — The Security & Evolution Layer for AI Agents

**[GSEP (Genomic Self-Evolving Prompts)](https://gsepcore.com)** is the framework powering the AI behind this skill. It wraps any LLM with 5 security layers and makes agents autonomously improve over time.

- **C3 Content Firewall** — blocks prompt injection before it reaches your LLM (57 patterns)
- **C4 Behavioral Immune System** — detects if your agent's response was manipulated
- **C5 Action Firewall** — prevents destructive actions (rm -rf, DROP TABLE, etc.) before they execute
- **Autonomous evolution** — agents improve their own prompts based on feedback, no retraining needed

**[GSEP-MCP](https://github.com/gsepcore/gsep-mcp)** — drop GSEP security into any AI agent in 2 minutes via the Model Context Protocol. Works with Claude Desktop, Cursor, Windsurf, n8n, and any MCP-compatible client.

```json
{
  "mcpServers": {
    "gsep": {
      "command": "npx",
      "args": ["-y", "@gsep/mcp@latest"]
    }
  }
}
```

→ [gsepcore.com](https://gsepcore.com) · [GitHub](https://github.com/gsepcore/gsep) · [npm](https://www.npmjs.com/package/@gsep/core)
