<div align="center">

<img src="assets/logo.png" alt="AI Collab Skill" width="480">

<br>

# AI Collab Skill

[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/gsepcore/ai-collab-skills?style=for-the-badge&logo=github)](https://github.com/gsepcore/ai-collab-skills)
[![Works With](https://img.shields.io/badge/Works_With-Claude_Code-blueviolet?style=for-the-badge)](https://claude.ai/code)
[![Built By](https://img.shields.io/badge/Built_by-gsepcore-blue?style=for-the-badge)](https://gsepcore.com)

**Let multiple AI assistants work on the same project simultaneously — and actually see each other.**

Created by **Luis Alfredo Velasquez Duran** | Germany, 2025-2026

[GitHub](https://github.com/gsepcore/ai-collab-skills) · [Install in 1 command](#installation) · [gsepcore.com](https://gsepcore.com)

🇪🇸 **¿Hablas español?** Lee el [README en español](README.es.md) — guía completa para hispanohablantes.

</div>

---

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

### One command — installs everything

```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/install.sh | bash
```

**Or from a cloned repo:**

```bash
git clone https://github.com/gsepcore/ai-collab-skills.git
bash ai-collab-skills/install/install.sh
```

That's it. The installer sets up **all five components** automatically:

| Component | What it does | Where |
|-----------|-------------|-------|
| 📚 Claude Code skill | `/collab` commands available in all sessions | `~/.claude/skills/collab/` |
| 🔄 Background daemon | Watches every `.ai-collab/` directory 24/7 | launchd (macOS) / cron (Linux) |
| 🪝 `SessionStart` hook | Loads `CONTEXT.md` + notifications on session open | `~/.claude/settings.json` |
| 🪝 `UserPromptSubmit` hook | Shows pending AI notifications before each message | `~/.claude/settings.json` |
| 🪝 `Stop` hook | Auto-regenerates `CONTEXT.md` after each Claude response | `~/.claude/settings.json` |

The hooks are installed **globally** (`~/.claude/settings.json`) so they work in **every project** automatically — no per-project configuration needed.

### After installing — set up your project

Open Claude Code inside your project and run:

```
/collab setup
```

This creates `.ai-collab/`, adds it to `.gitignore`, copies `PROTOCOL.md`, and walks you through adding snippets to your other AI tools.

### Set up other AIs

For permanent setup, paste the ready-made snippets from `references/protocol.md` into each tool's rules file. See the [Supported AI Tools](#supported-ai-tools) table below.

For a one-time session, paste this into any AI at session start:

```
You are part of a multi-AI team working on this project simultaneously.

STEP 1 — Read these files now:
- {project-root}/.ai-collab/PROTOCOL.md
- {project-root}/.ai-collab/CONTEXT.md
- Any other .md file in {project-root}/.ai-collab/ (those are logs from other AIs)

STEP 2 — Confirm you read them with a 3-line summary:
- What the other AIs are working on
- What files you must NOT touch
- The most critical pending issue

STEP 3 — Write your first log to:
{project-root}/.ai-collab/{your-ai-name}-{YYYYMMDD-HHMMSS}.md

PERMANENT RULE: after EVERY response you give, update that log. Do not wait to be asked.
COORDINATION RULE: check "Do Not Touch" sections before editing any file.
```

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

### `/collab monitor`

Start a zero-cost background monitor that notifies you the instant another AI updates their log. Runs as a persistent bash script — no tokens consumed while waiting.

```
/collab monitor
```

To stop it: tell Claude *"stop the collab monitor"* or run `TaskStop <id>` with the ID shown in `/collab status`.

---

### `/collab summary`

Generate `.ai-collab/CONTEXT.md` — a clean synthesis of all AI logs into a single onboarding file.

This is the **context bootstrapping** command. Run it after any significant session. Any new AI joining the project reads this one file and is fully up to speed in seconds — what was built, what decisions were made, what files were touched, known bugs, active locks, and a one-paragraph brief.

```
/collab summary
```

**The flow:**
```
All AI sessions → /collab summary → CONTEXT.md
New AI joins   → reads CONTEXT.md → instant full context
```

---

### `/collab clear`

Remove stale session logs.

```
/collab clear          # removes logs older than 24 hours
/collab clear --all    # removes all logs except PROTOCOL.md (asks for confirmation)
```

---

## How the background system works

> **This is all set up automatically by the installer.** No manual steps.

Three components keep Claude informed 24/7:

1. **launchd daemon** (macOS) / **cron** (Linux) — watches every `.ai-collab/` directory on your machine every 15 seconds. Auto-starts on login, survives sleep and reboots.
2. **Notification queue** — `~/.ai-collab-notifications.json` is a lightweight message queue. The daemon writes to it; the hooks read from it.
3. **Three Claude Code hooks** installed globally in `~/.claude/settings.json`:
   - `SessionStart` — injects `CONTEXT.md` before your first message in every new session
   - `UserPromptSubmit` — shows pending notifications before each message, zero token cost at idle
   - `Stop` — auto-regenerates `CONTEXT.md` after every Claude response using a Python script

### Manage the daemon

```bash
# Check if running
launchctl list | grep ai-collab

# View logs
tail -f /tmp/ai-collab-daemon.log

# Stop
launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist

# Restart
launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
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

### Remove everything the installer added

```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/uninstall.sh | bash
```

This removes the daemon, hooks, scripts, and skill. Your `.ai-collab/` project directories are untouched — your logs stay safe.

### Remove from a specific project only

```bash
rm -rf {project-root}/.ai-collab/
# Also remove .ai-collab/ from .gitignore
# Also remove the AI Collab Protocol block from .cursorrules / .windsurfrules
```

---

## Supported AI tools

| Tool | Rules file | Example |
|------|-----------|---------|
| **Cursor** | `.cursorrules` | `examples/cursorrules.example` |
| **Windsurf** | `.windsurfrules` | `examples/windsurfrules.example` |
| **Antigravity IDE** | System prompt / rules | `examples/antigravity.example` |
| **VS Code (Copilot)** | `.github/copilot-instructions.md` | `examples/vscode-copilot.example` |
| **GitHub Copilot** | `.github/copilot-instructions.md` | `examples/vscode-copilot.example` |
| **OpenCode / Minimax** | System prompt / rules | `references/protocol.md` → OpenCode section |
| **Codex / GPT** | System prompt | `references/protocol.md` → Codex section |
| **Hermes** | System prompt / rules | `examples/hermes.example` |
| **Any AI / Any agent** | Paste the generic snippet | `examples/generic-any-ai.example` |

Every snippet includes the **automatic log rule** — each AI saves its log after every response by default, without the user asking. This is what enables real-time collaboration.

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
