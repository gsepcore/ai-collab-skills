<p align="center">
  <img src="assets/logo.png" alt="AI Collab Skill" width="160" />
</p>

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

Paste this command into each other AI at the start of their session. Replace the paths with your actual project path:

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

Use this exact format:
---
ai: [Your AI name and model]
session: [YYYYMMDD-HHMMSS]
project: [project name]
updated: [ISO timestamp]
---
## Working On
[what you are doing right now]
## Files Modified This Session
[files you touched, or "None"]
## Decisions Made
[decisions taken, or "None"]
## Do Not Touch (Avoid Conflicts)
[files you are actively editing]
## Handoff Note
[the most important thing other AIs must know from this session]

PERMANENT RULE — after EVERY response you give me:
Update that log file with what you just did. Do not wait to be asked. Always.

COORDINATION RULE:
- Before editing any file, check the "Do Not Touch" section of other AI logs
- If another AI has a file listed, ask me before touching it
- Write only in English or the language I am using — no mixed alphabets or scripts
```

Or for **permanent setup** (so the AI does this automatically every session), paste the ready-made snippets from `examples/` into their rules files. See the [Supported AI Tools](#supported-ai-tools) table below.

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

**Step 3 — Add the hooks to your project:**
Add this to `.claude/settings.local.json` in your project (merge with existing settings):
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd); CTX=\"$ROOT/.ai-collab/CONTEXT.md\"; NOTIF=\"$HOME/.ai-collab-notifications.json\"; if [ -f \"$CTX\" ]; then echo \"[AI-COLLAB SESSION RECOVERY]\"; echo \"Project: $(basename $ROOT)\"; echo \"---\"; cat \"$CTX\"; echo \"---\"; fi; if [ -f \"$NOTIF\" ]; then CONTENT=$(cat \"$NOTIF\"); if [ \"$CONTENT\" != \"[]\" ] && [ -n \"$CONTENT\" ]; then echo \"[PENDING NOTIFICATIONS FROM OTHER AIs]\"; echo \"$CONTENT\"; echo \"[]\" > \"$NOTIF\"; fi; fi'",
            "timeout": 10
          }
        ]
      }
    ],
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

**`SessionStart` hook** — fires when you open a new session. Reads `CONTEXT.md` and injects full project context before you type a single word. Survives battery death, reboots, and session crashes.

**`Stop` hook** — fires when Claude finishes responding. Auto-regenerates `CONTEXT.md` from all logs using a Python script. Zero tokens, zero user action required.

**`UserPromptSubmit` hook** — fires on every message. Shows pending notifications from other AIs instantly, zero token cost at idle.

**Step 3b — Install the summary script:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/ai-collab-summary.py \
  -o ~/.claude/ai-collab-summary.py
```

Then add the `Stop` hook to your `.claude/settings.local.json`:
```json
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/ai-collab-summary.py 2>/dev/null || true",
        "timeout": 15,
        "async": true
      }
    ]
  }
],
```

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
