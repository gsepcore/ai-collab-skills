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

When you use Claude Code alongside OpenCode, Codex, Cursor native chat, Windsurf native chat, Copilot Chat, or any other AI coding agent, they are completely blind to each other. This skill creates a shared filesystem protocol so they can read and write context in real time — no external service, no API, no internet required. Just a `.ai-collab/` directory inside your project.

---

## How it works

Each AI writes a Markdown session log to `{project-root}/.ai-collab/`. Any AI with access to the project filesystem can read those logs instantly. Claude manages its own log via this skill. Other agents participate via snippets added to the files their runtime actually reads — one-time setup per project.

**Important:** AI Collab is agent-first. The agent is the runtime doing work (`codex`, `opencode`, `claude-code`). The container is where it is visible (`antigravity`, `cursor`, `vscode`, `terminal`). The model is metadata (`openai/gpt-5.5`, `minimax/m2.7`, `anthropic/claude-opus-4.7`). The setup stores all three so agents can recognize each other across projects.

```
your-project/
└── .ai-collab/
    ├── PROTOCOL.md                   ← shared protocol (auto-created)
    ├── TEAM.md                       ← registered agents + container/model/rules
    ├── agents.json                   ← machine-readable agent manifest
    ├── inbox-all.md                  ← broadcast tasks for any AI
    ├── inbox-codex.md                ← tasks assigned specifically to Codex
    ├── inbox-opencode.md             ← tasks assigned specifically to OpenCode
    ├── thread-20260512-task.md       ← agent-to-agent conversation for a task
    ├── live/                         ← semantic observer snapshots + automatic screenshots
    │   ├── summary.json              ← current status for every registered agent
    │   ├── opencode.json             ← inferred live state for OpenCode
    │   ├── opencode.agent.json       ← OpenCode's self-reported command/edit state
    │   └── screenshots/              ← automatic screenshots, ignored by git
    ├── claude-code-20260511-143022.md ← Claude Code's log
    ├── cursor-native-20260511-141500.md ← Cursor native chat log
    ├── codex-20260511-141000.md      ← Codex's log
    └── opencode-20260511-140500.md   ← OpenCode's log
```

---

## Architecture: director, autonomous workers, project isolation

Three principles make this skill work the way it does. Read these before installing — they explain the design and what to expect.

### 1. Claude Code is the default director

You usually interact with **Claude Code** as the orchestrating AI. Claude is the only assistant that:

- Has live `UserPromptSubmit` / `Stop` / `SessionStart` hooks that surface notifications and regenerate `CONTEXT.md` automatically.
- Owns the `/collab` slash commands — `/collab assign`, `/collab read`, `/collab monitor`, etc.
- Writes task assignments to `.ai-collab/inbox-{ai}.md` for the worker AIs to pick up.

The other AIs (OpenCode, Codex, Cursor native chat, Windsurf native chat, Copilot Chat, Hermes, etc.) are **workers** by default. They participate by reading their agent rules file and the `.ai-collab/` directory — no hooks, no slash commands.

Worker AIs *can* technically read each other's logs and edit any file too, but task delegation flows from Claude outward. This keeps coordination centralized and avoids ambiguous "who owns this decision" situations.

For large implementation plans, the user can start a **directed run** and choose the director for that run (`claude-code`, `codex`, `opencode`, or another registered agent). The selected director gets a run lock in `.ai-collab/runs/{run_id}/director.json`; every other agent treats that run as worker-owned until the lock is released. This lets Codex direct one run while Claude Code directs another without the two overwriting each other's decisions.

### 1.5 Director knows the team from session start

For Claude to delegate well, it needs to know who else is on the project. The `Stop` hook regenerates `.ai-collab/CONTEXT.md` after every Claude response, and `CONTEXT.md` includes a **`## Team` section** built from three sources:

1. **`.ai-collab/TEAM.md`** and **`.ai-collab/agents.json`** (explicit manifest, takes precedence) — generated by `/collab setup` or the installer. Lists every agent, container, model, and rules file even before they have written a log.
2. **Unique rules files** in project root — `.cursorrules` → `cursor-native`, `.windsurfrules` → `windsurf-native`, `.github/copilot-instructions.md` → `copilot-chat`, `.aider.conf.yml` → `aider`.
3. **Existing logs** in `.ai-collab/` — any `{slug}-*.md` file means that AI has been active here at least once.

`AGENTS.md` is shared by OpenCode, Codex, Aider, Continue, and others — its presence alone is ambiguous, so those AIs only show up in the Team section once they have written a log OR they are explicitly listed in `TEAM.md`. A footnote below the roster reminds you that more AGENTS.md-compatible AIs may join.

This means the next time Claude opens a project, it sees a roster like:

```
## Team
- **claude-code** — director (Claude Code skill) · last seen 12min ago
- **cursor-native** — registered via `.cursorrules` · no logs yet
- **opencode** — registered via `AGENTS.md` · last seen 3min ago
- **codex** — declared in `TEAM.md` · last seen 4h ago
```

…and can confidently assign tasks via `/collab assign codex …` without first asking the user "is Codex on this project?".

### 2. Workers react autonomously to assignments and mentions

You never have to copy a task from Claude's window into OpenCode's window. The protocol handles it via the filesystem and, when a wakeup adapter is configured, the background daemon can also activate the target worker:

```
You → Claude: "refactor auth and have Codex publish v1.1.0"
       │
       ↓
Claude writes .ai-collab/inbox-codex.md with status: unread
       │
       ↓
Daemon sees inbox-codex.md → writes a wake event for codex
       │
       ↓
Configured adapter wakes Codex, or notify-only records the event safely
       │
       ↓
Codex reads its rules file → reads inbox-codex.md → status: unread detected
       │
       ↓
Codex executes the task → writes codex-{timestamp}.md log →
       marks inbox status: done
       │
       ↓
Daemon detects the new log → writes ~/.ai-collab-notifications.json entry
       │
       ↓
Next time you send any prompt in Claude → UserPromptSubmit hook injects
       "Codex just published v1.1.0" into your context →
       Claude tells you it's done
```

Every worker AI's rules file (created by `/collab setup` or pasted from `references/protocol.md`) contains two mandatory behaviors:

1. **Inbox check before every response** — re-read `inbox-{ai}.md` and `inbox-all.md`, execute any `status: unread` task, mark it `status: done` via atomic write.
2. **Automatic log after every response** — save to `.ai-collab/{ai}-{timestamp}.md` with frontmatter and standard sections.

These are non-negotiable rules in every snippet so workers self-orient without the user prompting.

When `/collab setup` runs on a fresh project, it also seeds `.ai-collab/inbox-all.md` with a **welcome onboarding task** — the first worker AI to open the project gets a concrete first instruction instead of an empty inbox.

### 2.5 Per-agent monitors and task threads

The daemon treats each worker slug as addressable. Direct assignments wake `inbox-{slug}.md`; threaded conversation wakes workers through `@slug` mentions in `.ai-collab/thread-{task_id}.md`.

```text
inbox-codex.md         direct task mailbox for Codex
inbox-opencode.md      direct task mailbox for OpenCode
thread-{task_id}.md    append-only discussion around a task
@codex                 wake Codex from the latest thread message
@opencode              wake OpenCode from the latest thread message
```

Thread mentions create wake events with `source_type: thread`, `reason: thread-mention`, `source_path`, `thread_path`, and the target slug. They do not claim or mutate the inbox task. The inbox remains the canonical task state; the thread is the conversation layer agents use to ask questions, assign review, and report progress to each other.

### 3. Each project is its own isolation bubble

Everything lives inside the project. Open a different project tomorrow → contexts do not mix.

- `.ai-collab/CONTEXT.md`, inboxes, logs, `PROTOCOL.md` — all inside `{project-root}/.ai-collab/`
- `AGENTS.md`, `.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md` — all in the project root
- The `SessionStart` hook resolves the project from `git rev-parse --show-toplevel` per session
- The `~/.ai-collab-notifications.json` global queue is **filtered by project** at read time: the `UserPromptSubmit` hook only injects notifications whose `project` field matches the active project. Notifications from other projects are preserved untouched in the file until you open Claude inside that project.

**Cross-project mode** (opt-in): set `AI_COLLAB_CROSS_PROJECT=1` in your environment to see notifications from all projects in one stream. The output format then becomes `[ai/project]` per line so you can tell them apart. Useful when you intentionally orchestrate work across multiple repos from the same Claude session.

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

That's it. The installer sets up **all eleven components** automatically:

| Component | What it does | Where |
|-----------|-------------|-------|
| 📚 Claude Code skill | `/collab` commands available in all sessions | `~/.claude/skills/collab/` |
| 🔄 Background daemon | Watches every `.ai-collab/` directory 24/7 | launchd (macOS) / cron (Linux) |
| 📨 Wakeup detector | Detects unread inbox tasks and dispatches the configured adapter | `~/.claude/ai-collab-wakeup.py` |
| 🧭 Auto-onboard | Detects a new agent's first log and appends its rules snippet + TEAM entry | `~/.claude/ai-collab-auto-onboard.py` |
| 🧩 Project onboarding | Registers agents, IDE/container, model, TEAM, inbox, and rules files | `~/.claude/ai-collab-project-setup.py` |
| 🎛️ Run orchestrator | Creates director-selected implementation runs, safe tasks, and agent threads | `~/.claude/ai-collab-orchestrate.py` |
| 👁️ Live observer | Writes `.ai-collab/live/` semantic snapshots, alerts, and automatic screenshots | `~/.claude/ai-collab-observer.py` |
| 🩺 Doctor script | Verifies installed files, hooks, daemon, and queues | `~/.claude/ai-collab-doctor.py` |
| 🪝 `SessionStart` hook | Loads `CONTEXT.md` + notifications on session open | `~/.claude/settings.json` |
| 🪝 `UserPromptSubmit` hook | Shows pending AI notifications before each message | `~/.claude/settings.json` |
| 🪝 `Stop` hook | Auto-regenerates `CONTEXT.md` after each Claude response | `~/.claude/settings.json` |

The hooks are installed **globally** (`~/.claude/settings.json`) so they work in **every project** automatically — no per-project configuration needed.

### After installing — set up your project

Open Claude Code inside your project and run:

```
/collab setup
```

This runs the same onboarding helper as the installer. It asks which IDE/container you are using, which agents will participate, and which LLM model each agent uses. Then it creates `.ai-collab/`, adds it to `.gitignore`, copies `PROTOCOL.md`, writes `TEAM.md` and `agents.json`, seeds `inbox-all.md`, and appends agent-specific rule blocks.

### Set up other agents

For permanent setup, prefer the onboarding helper:

```bash
python3 ~/.claude/ai-collab-project-setup.py
```

It writes to the right agent runtime files:

| Agent runtime | Rules target |
|---|---|
| Claude Code | `CLAUDE.md` |
| OpenCode | `.opencode/rules/ai-collab.md` + `AGENTS.md` |
| Codex | `AGENTS.md` |
| Cursor native chat | `.cursorrules` |
| Windsurf native chat | `.windsurfrules` |
| Copilot Chat | `.github/copilot-instructions.md` |

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

Creates or updates `.ai-collab/claude-code-{YYYYMMDD-HHMMSS}.md` with what you are working on, files modified, decisions made, bugs found, and anything other AIs should know.

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

### `/collab assign [ai-name] [task description]`

Delegate a task to another AI without leaving your Claude session. Writes `.ai-collab/inbox-{ai-name}.md` with `status: unread`. The daemon records a wake event for that agent and **by default dispatches the `visible` adapter automatically** — for OpenCode it now writes the prompt into the running TUI and submits it, so the user can see the task arrive before the worker reads the inbox, executes the task, and marks it `status: done`. No manual activation needed after `curl … | bash`.

```
/collab assign codex publish v1.2.0 to npm and tag the release on GitHub
/collab assign opencode add integration tests for the auth flow
/collab assign all run your test suites and report failures here
```

The third form (`/collab assign all ...`) writes to `inbox-all.md` so every worker AI sees it.

**Why this matters:** you do not have to copy a prompt from Claude's window into Codex's or OpenCode's window. The worker AI self-orients from its inbox, and the daemon can wake addressable agents through inboxes or `@slug` thread mentions. See [Architecture](#architecture-director-autonomous-workers-project-isolation) for the full flow.

### `/collab orchestrate`

Run a large implementation as a directed multi-agent execution. The user chooses one active director for that run — for example Claude Code or Codex — and the director decomposes the work into bounded tasks, assigns owners, manages agent-to-agent questions, validates results, and writes a final summary.

Directed runs are stored under:

```text
.ai-collab/runs/{run_id}/
  PLAN.md
  director.json
  tasks.json
  status.md
  final-summary.md
```

Task conversations still use top-level `thread-{task_id}.md` files so the existing daemon can wake agents when a message mentions `@codex`, `@opencode`, or another registered slug.

The helper enforces the safety rules that keep autonomy controlled:

- One active director per run (`director_lock: active`)
- One owner per task
- Explicit allowed files and do-not-touch boundaries
- No overwrite of active inboxes unless the user/director forces it deliberately
- All agent questions and answers go through task threads
- Finalization requires terminal task states and validation evidence

Example:

```bash
python3 ~/.claude/ai-collab-orchestrate.py init \
  --goal "Implement the billing settings page end to end" \
  --director codex \
  --agents claude-code,opencode \
  --title billing-settings

python3 ~/.claude/ai-collab-orchestrate.py add-task \
  --run-id 20260527-120000-billing-settings \
  --actor codex \
  --task-id billing-ui \
  --title "Build settings UI" \
  --owner opencode \
  --allowed-files "src/app/billing/**,src/components/billing/**" \
  --description "Implement the billing settings UI and update the task thread with decisions."

python3 ~/.claude/ai-collab-orchestrate.py assign \
  --run-id 20260527-120000-billing-settings \
  --actor codex \
  --task-id billing-ui
```

Agents can talk to each other naturally in the task thread:

```bash
python3 ~/.claude/ai-collab-orchestrate.py thread \
  --run-id 20260527-120000-billing-settings \
  --task-id billing-ui \
  --author opencode \
  --message "@codex I need a decision: should invoice export live in this task or a follow-up?"
```

### `/collab setup`

First-time setup for a project. Run this once per project.

- Creates `.ai-collab/` directory
- Adds it to `.gitignore`
- Copies `PROTOCOL.md` into the directory
- Asks which AI tools you use and generates the rules snippets
- **Seeds `inbox-all.md` with a welcome onboarding task** — first worker AI to open this project self-orients automatically (preserved unchanged if file already exists)
- Writes Claude's first log entry
- Starts Claude's live `/collab monitor` automatically for this project when the Claude Code runtime supports persistent Monitor/Task execution

```
/collab setup
```

### `/collab monitor`

Restart the live monitor for the current Claude Code session. Normal users should not need to run this after `/collab setup`; setup starts it automatically. Use this command only after closing/reopening Claude Code, debugging, or intentionally stopping the monitor.

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

Ten components keep Claude informed and able to dispatch inbox tasks:

1. **launchd daemon** (macOS) / **cron** (Linux) — watches every `.ai-collab/` directory on your machine every 15 seconds. Tags each notification with the `project` field (basename of the project root) so notifications can be filtered downstream. Auto-starts on login, survives sleep and reboots.
2. **Notification queue** — `~/.ai-collab-notifications.json` is a lightweight, capped (50 entries) message queue written atomically (`tempfile + os.replace`) to survive concurrent writes. The daemon writes to it; the hooks read from it.
3. **Notification reader script** — `~/.claude/ai-collab-check-notifications.py` is invoked by the `UserPromptSubmit` hook. It uses an `fcntl` lock to coordinate with the daemon, **filters notifications by active project**, caps output to 10 items / 500 chars per message / 4 KB total, drops notifications older than 24 h, defends against malformed JSON, and always exits 0 (never blocks your prompt). All limits are tunable — see [Environment variables](#environment-variables) below.
4. **Wakeup detector** — `~/.claude/ai-collab-wakeup.py` scans `inbox-*.md` and `thread-*.md` separately from normal log notifications. It writes durable wake events to `~/.ai-collab-wakeup-events.json`, tracks retry/dedupe state in `~/.ai-collab-wakeup-state.json`, logs to `/tmp/ai-collab-wakeup.log`, and dispatches the configured adapter. Direct inbox tasks use `reason: unread-inbox`; thread mentions use `reason: thread-mention`. **By default it uses `visible`** so OpenCode/Codex get invisible synthetic wakeups in their running panels with zero manual activation; you can downgrade to `cli` (headless execution) or `notify-only` (safe logging only) via `AI_COLLAB_WAKEUP_ADAPTER`.
5. **Live observer** — `~/.claude/ai-collab-observer.py` writes `.ai-collab/live/{agent}.json` snapshots every daemon tick. It combines inbox status, latest logs, agent self-reports, running process hints, git dirtiness, stale-claim alerts, and automatic screenshots.
6. **Project onboarding** — `~/.claude/ai-collab-project-setup.py` creates the agent-first project manifest (`TEAM.md`, `agents.json`), welcome inbox, and runtime rules files. It records agent, container, and model explicitly so a fresh project does not depend on guesswork.
7. **Auto-onboard detector** — `~/.claude/ai-collab-auto-onboard.py` runs from the daemon when a new `{slug}-{YYYYMMDD-HHMMSS}.md` log appears. Known slugs get an agent-specific `AI-COLLAB-START agent={slug}` rules block if missing, plus a merged `.ai-collab/TEAM.md` roster entry. Unknown slugs produce a low-priority `.ai-collab/inbox-all.md` notice telling Claude Code to run `/collab onboard {slug}`. The operation is idempotent and never overwrites existing rules content.
8. **Run orchestrator** — `~/.claude/ai-collab-orchestrate.py` creates `.ai-collab/runs/{run_id}/`, records the selected director, writes safe task assignments to normal inboxes, and appends task-thread messages that agents can answer naturally.
9. **Doctor script** — `~/.claude/ai-collab-doctor.py` verifies the installed scripts, skill files, hooks, daemon registration, and JSON queues. It is read-only and safe to run any time.
10. **Three Claude Code hooks** installed globally in `~/.claude/settings.json`:
   - `SessionStart` — injects `CONTEXT.md` before your first message in every new session
   - `UserPromptSubmit` — runs `ai-collab-check-notifications.py` to show pending notifications for the **active project only**, zero token cost at idle
   - `Stop` — auto-regenerates `CONTEXT.md` after every Claude response using a Python script

### macOS notifications (survives Claude close, Mac sleep, and restart)

The launchd daemon already watches your `.ai-collab/` directories 24/7, but its notifications normally wait in the queue until you open Claude Code and submit a prompt. If you want **proactive banners that fire even when Claude is closed** — for example so you can leave Codex publishing a release overnight and get a Notification Center banner when it finishes — opt in to macOS notifications.

The installer prompts you about this during step 3. To toggle it later, edit `~/Library/LaunchAgents/com.gsepcore.ai-collab.plist` and add (or remove) this block before `<key>ProgramArguments</key>`:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>AI_COLLAB_OS_NOTIFY</key>
    <string>1</string>
</dict>
```

Then reload the daemon: `launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist && launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist`.

**Banner format:** `AI Collab — {project}` / `{ai-name}` / `{the AI's Working On line}`.

**Optional sound:** add `<key>AI_COLLAB_OS_NOTIFY_SOUND</key><string>Tink</string>` to play a sound with each banner. Other valid names: `Glass`, `Pop`, `Hero`, `Bottle`, `Frog`, `Funk`, `Morse`, `Ping`, `Purr`, `Sosumi`, `Submarine`. Leave unset for silent banners (recommended if you work with multiple active AIs).

**First time:** macOS may ask permission to send notifications from the script. Grant it once via System Settings → Notifications. If you never see banners, check there.

**Disable mid-session:** edit the plist to remove the `EnvironmentVariables` block and reload. The daemon keeps writing to the notification queue (so in-Claude notifications via `UserPromptSubmit` still work) — only the OS banner stops.

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

### Doctor / health check

```bash
# Check install health without failing on warnings
python3 ~/.claude/ai-collab-doctor.py

# CI or release verification: fail if required files/settings are broken
AI_COLLAB_DOCTOR_STRICT=1 python3 ~/.claude/ai-collab-doctor.py
```

### Environment variables

All optional. Set them in your shell rc file (`~/.zshrc`, `~/.bashrc`, etc.) to tune behavior across sessions.

| Variable | Default | What it controls |
|----------|---------|-----------------|
| `AI_COLLAB_PROJECT` | _(auto-detected)_ | Override active project name. By default the script uses `git rev-parse --show-toplevel` basename, falling back to `cwd` basename. |
| `AI_COLLAB_CROSS_PROJECT` | _(off)_ | Set to `1` to receive notifications from **all projects** in one stream. Output becomes `[ai/project]` per line. Useful when you orchestrate multiple repos from one Claude session. |
| `AI_COLLAB_LOCK_TIMEOUT` | `3.0` | How long (seconds) the reader waits for the daemon's file lock before giving up silently. Notifications are preserved on timeout — they will surface on the next prompt. |
| `AI_COLLAB_MAX_AGE_HOURS` | `24` | Notifications older than this are dropped on read. Tune up if you want longer history across long breaks. |
| `AI_COLLAB_MAX_ITEMS` | `10` | Maximum number of notifications to inject per prompt. The rest are summarized as "...and N more". |
| `AI_COLLAB_MAX_NOTE_CHARS` | `500` | Per-notification character cap. Anything longer is truncated with `...[truncated]`. |
| `AI_COLLAB_MAX_OUTPUT` | `4000` | Total stdout character cap. Hard ceiling protecting Claude's context. |
| `AI_COLLAB_YES` | _(off)_ | Set to `1` to skip installer confirmation prompts (useful in CI / Dockerfile installs). |
| `AI_COLLAB_NO_DAEMON` | _(off)_ | Set to `1` to skip starting the background daemon during install (file-watching feature disabled). |
| `AI_COLLAB_OS_NOTIFY` | _(off)_ | Set to `1` (in the daemon's launchd plist `EnvironmentVariables`) to fire macOS Notification Center banners when other AIs complete tasks. Persistent layer that works even when Claude Code is closed — see [macOS notifications](#macos-notifications-survives-claude-close-mac-sleep-and-restart). |
| `AI_COLLAB_OS_NOTIFY_SOUND` | _(off)_ | macOS sound name (e.g. `Tink`, `Glass`, `Pop`, `Hero`) to play with each banner. Only effective when `AI_COLLAB_OS_NOTIFY=1`. Leave unset for silent banners. |
| `AI_COLLAB_DOCTOR_STRICT` | _(off)_ | Set to `1` so `ai-collab-doctor.py` exits nonzero when required install files/settings are broken. Warnings remain non-fatal. |
| `AI_COLLAB_OBSERVER` | `1` | Enables semantic live snapshots in `.ai-collab/live/`. Set `0` to disable the observer while keeping the daemon running. |
| `AI_COLLAB_OBSERVER_ACTIVE_SECONDS` | `300` | How recently a log or self-report must update before an agent is considered active. |
| `AI_COLLAB_OBSERVER_STALE_CLAIM_SECONDS` | `1800` | Claimed/running inbox age before the observer emits a stale-claim director alert. |
| `AI_COLLAB_OBSERVER_MAX_EVENTS` | `200` | Maximum observer JSONL events kept per `.ai-collab/live/{agent}.events.jsonl` file. |
| `AI_COLLAB_OBSERVER_SCREENSHOTS` | `1` | Automatic macOS screenshots in `.ai-collab/live/screenshots/`. Set `0` to disable. |
| `AI_COLLAB_OBSERVER_SCREENSHOT_MODE` | `project` | Screenshot mode: `project` captures a visible window whose title matches the current project; `frontmost` captures the front window; `screen` captures the full screen. |
| `AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL` | `300` | Minimum seconds between automatic screenshots per project. |
| `AI_COLLAB_OBSERVER_SCREENSHOT_ACTIVE_ONLY` | `1` | Only capture when at least one agent is active/waiting/blocked/running. Set `0` to capture on every observer interval. |
| `AI_COLLAB_OBSERVER_SCREENSHOT_MAX_KEEP` | `20` | Maximum screenshots retained per project before older PNGs are pruned. |
| `AI_COLLAB_WAKEUP_ADAPTER` | `visible` | Wakeup adapter mode. Default `visible` targets active visible panels when supported. Other options: `opencode-visible`, `kilo-visible`, `hermes-uri`, `antigravity-chat`, `acp`, `codex-acp`, `kimi-acp`, `kilo-acp`, `hermes-acp`, `cli`, or `notify-only`. |
| `AI_COLLAB_WAKEUP_MAX_ATTEMPTS` | `3` | Maximum wake attempts before an unread inbox auto-transitions to `failed`. |
| `AI_COLLAB_WAKEUP_ADAPTER_TIMEOUT` | `120` | Seconds before a CLI adapter run is considered failed. |
| `AI_COLLAB_WAKEUP_CLI_PROJECTS` | _(empty = all projects)_ | Optional allowlist for executable adapters. By default, any project with a `.ai-collab/` directory is allowed — the user opted in by setting it up there. Set this only if you want to restrict the daemon to specific projects: comma-separated basenames or absolute paths. |
| `AI_COLLAB_WAKEUP_CLI_TARGETS` | `codex,opencode,claude,claude-code,hermes,kimi,kilo` | Optional comma-separated target allowlist for CLI execution. |
| `AI_COLLAB_WAKEUP_VISIBLE_TARGETS` | `codex,opencode,kilo,hermes` | Optional comma-separated target allowlist for visible adapters. Falls back to `AI_COLLAB_WAKEUP_CLI_TARGETS` if set. |
| `AI_COLLAB_WAKEUP_DRY_RUN` | _(off)_ | Set to `1` to record what would be woken without executing any CLI command. |
| `AI_COLLAB_CODEX_BIN` | _(auto-detected)_ | Override the `codex` executable path used by the CLI adapter. |
| `AI_COLLAB_OPENCODE_BIN` | _(auto-detected)_ | Override the `opencode` executable path used by the CLI adapter. |
| `AI_COLLAB_CLAUDE_BIN` | _(auto-detected)_ | Override the `claude` executable path used by the CLI adapter. |
| `AI_COLLAB_KIMI_BIN` | _(auto-detected)_ | Override the `kimi` executable path used by CLI/ACP adapters. Packaged Antigravity extension binaries are auto-detected. |
| `AI_COLLAB_KILO_BIN` | _(auto-detected)_ | Override the `kilo` executable path used by CLI/ACP adapters. Packaged Antigravity extension binaries are auto-detected. |
| `AI_COLLAB_HERMES_BIN` | _(auto-detected)_ | Override the `hermes` executable path used by the ACP adapter. |
| `AI_COLLAB_CODEX_ACP_COMMAND` | `npx -y @zed-industries/codex-acp@latest` | Override the command used by the opt-in `codex-acp` adapter. |
| `AI_COLLAB_KIMI_ACP_COMMAND` | `kimi acp` | Override the command used by `kimi-acp` / generic `acp`. |
| `AI_COLLAB_KILO_ACP_COMMAND` | `kilo acp` | Override the command used by `kilo-acp` / generic `acp`. |
| `AI_COLLAB_HERMES_ACP_COMMAND` | `hermes acp` | Override the command used by `hermes-acp` / generic `acp`. |
| `AI_COLLAB_OPENCODE_PORTS` | _(auto-detected)_ | Optional comma-separated OpenCode TUI ports for `opencode-visible`. Normally auto-detected from running `opencode --port` processes. |
| `AI_COLLAB_OPENCODE_SYNTHETIC` | _(off)_ | Set to `1` to restore hidden OpenCode wakeup prompts (`synthetic: true`). Default is off so delegated tasks appear in the OpenCode UI instead of completing behind the user's back. |
| `AI_COLLAB_KILO_PORTS` | _(auto-detected)_ | Optional comma-separated Kilo server ports for `kilo-visible`. Normally auto-detected from running `kilo serve --port` processes. |
| `AI_COLLAB_KILO_BASIC_AUTH` | _(empty)_ | Optional `user:password` for local Kilo servers that return HTTP 401. |
| `AI_COLLAB_KILO_BEARER_TOKEN` | _(empty)_ | Optional bearer token for local Kilo servers that require token auth. |
| `AI_COLLAB_HERMES_URI_TEMPLATE` | `vscode://layerdynamics.hermes-vscode?prompt={prompt}` | URI template for `hermes-uri`. `{prompt}` is URL-encoded and prefilled into the Hermes chat panel; the user may still need to press send. |
| `AI_COLLAB_ANTIGRAVITY_BIN` | _(auto-detected)_ | Override the `antigravity` executable used by `antigravity-chat`. |
| `AI_COLLAB_ANTIGRAVITY_MODE` | `agent` | Mode passed to `antigravity chat --mode` for visible Codex/Antigravity wakeups. |

### Semantic live observer and screenshots

The daemon now gives the director "semantic eyes" for every onboarded project. Every 15 seconds it writes live project-local state:

```text
.ai-collab/live/
  summary.json
  opencode.json
  opencode.agent.json
  opencode.agent.events.jsonl
  opencode.events.jsonl
  director-alerts.jsonl
  screenshots/
```

`{agent}.json` is the observer's merged view: inbox status, current task, latest log sections, self-reported command/edit phase from `{agent}.agent.json`, recent command/test/edit events from `{agent}.agent.events.jsonl`, project-scoped process hints, git dirty files, thread mentions, and alerts. `{agent}.events.jsonl` is observer-owned history for status changes, process changes, dirty-file changes, and screenshots.

The onboarding snippets instruct each agent to self-report before commands and edits:

```json
{
  "agent": "opencode",
  "updated": "2026-06-15T12:00:00Z",
  "phase": "command",
  "current_command": "python3 -m unittest install/test_wakeup.py",
  "task_id": "20260615-opencode-fix-tests",
  "files_in_scope": ["install/ai-collab-wakeup.py"]
}
```

Screenshots are **on by default**. To disable them on a machine or project:

```bash
AI_COLLAB_OBSERVER_SCREENSHOTS=0 \
bash install/install.sh
```

On macOS, the first capture may trigger the normal Screen Recording permission prompt. If permission is denied, semantic snapshots still work; only screenshot events report failure. Screenshots are project-aware by default: if the front visible Antigravity/Codex/OpenCode window belongs to another project, the observer records `status: skipped` instead of capturing the wrong workspace. Screenshots are throttled by `AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL` and pruned by `AI_COLLAB_OBSERVER_SCREENSHOT_MAX_KEEP`.

### Visible wakeup (default)

**Active by default.** A bare `curl … | bash` install enables the visible adapter for supported panels, allows all projects with `.ai-collab/`, and seeds the daemon `PATH` so node/nvm/homebrew/extension binaries are reachable. No env vars required for OpenCode.

Behavior:

- `@opencode` or `inbox-opencode.md` uses the OpenCode TUI endpoints: `POST /tui/clear-prompt`, `POST /tui/append-prompt`, then `POST /tui/submit-prompt`. This targets the visible prompt box instead of a background session, so the user can see the delegated task arrive. Set `AI_COLLAB_OPENCODE_SYNTHETIC=1` only if you explicitly prefer hidden prompts through `POST /session/{id}/prompt_async` and accept that this can feel like background/headless work when no visible response appears.
- `@kilo` or `inbox-kilo.md` uses the same visible TUI endpoint pattern when a Kilo server is open. If Kilo returns HTTP 401, set `AI_COLLAB_KILO_BASIC_AUTH` or `AI_COLLAB_KILO_BEARER_TOKEN`.
- `@hermes` or `inbox-hermes.md` can open/prefill the Hermes chat through `AI_COLLAB_HERMES_URI_TEMPLATE`. This is visible but may require the user to press send.
- `@codex` or `inbox-codex.md` uses `antigravity chat --reuse-window --mode agent`. **Codex visible-tab wakeup remains degraded** — see "Known limitations" below.
- `@kimi` supports ACP/CLI wakeups, but no verified visible-panel injection endpoint has been found yet.
- If no visible panel/port/session exists, the adapter fails safely and normal retry/backoff applies.

### Known limitations — Codex visible-tab wakeup

On 2026-05-13 three independent reviewers (Codex, OpenCode, Claude Code) confirmed that **waking the Codex panel already open inside Antigravity is blocked upstream**. The reasons:

- `openai.chatgpt` extension exposes no public API (`exportsType: undefined`).
- The active `codex app-server` is connected only by private stdio pipes inherited from Antigravity helper processes — no public socket.
- VS Code proposed chat APIs require an explicit allowlist that third-party companion extensions cannot enter.
- ACP is a viable protocol, but only when spawning a *new* Codex worker — it cannot attach to the existing visible session.
- FD hijacking is technically possible but indistinguishable from malware; refused on security grounds.

Until OpenAI or Antigravity publishes a supported injection surface, Codex has three real modes today:

| Mode | What it does | Tradeoff |
|---|---|---|
| `visible` (default) | `antigravity chat --reuse-window` best-effort | May or may not reach the visible tab. Degraded. |
| `codex-acp` (opt-in) | Spawns a fresh ACP Codex worker, 100% reliable execution | Runs invisibly, not in the user's open panel |
| Manual (1 click) | User types "lee tu inbox" in the tab | 100% reliable + visible, requires one human click |

OpenCode and Claude Code remain automatable, but AI Collab treats visible trust as the default: OpenCode wakeups are visible unless `AI_COLLAB_OPENCODE_SYNTHETIC=1` is explicitly enabled. Full investigation: `claude-acp-active-codex-analysis.md`, `codex-bridge-blocker.md`, `codex-acp-investigation.md`, `opencode-codex-bridge-investigation.md` (these live in the `.ai-collab/` of any project where the team has investigated — they document the dead-end analysis so future contributors do not retry the same paths).

### Codex ACP wakeup (opt-in)

`AI_COLLAB_WAKEUP_ADAPTER=codex-acp` starts a fresh invisible Codex ACP worker through `@zed-industries/codex-acp`, opens an ACP session for the project, and sends the inbox task with `session/prompt`.

This is different from the visible Antigravity Codex tab: ACP gives the daemon a real JSON-RPC/stdio control path for a new Codex worker, but it does not attach to an already-open Codex panel inside Antigravity. Use it when you prefer autonomous invisible execution over visible-tab continuity.

To restrict or downgrade after install, edit `~/Library/LaunchAgents/com.gsepcore.ai-collab.plist` and reload, or re-run the installer with custom env vars:

```bash
# Example: only allow a single project, downgrade to notify-only
AI_COLLAB_WAKEUP_ADAPTER=notify-only \
AI_COLLAB_WAKEUP_CLI_PROJECTS=/path/to/project \
bash install/install.sh
```

---

## Live monitoring (auto-started by setup)

`/collab setup` starts a **persistent bash Monitor** for the current Claude Code session when the runtime supports persistent Monitor/Task execution. It watches `.ai-collab/` every 20 seconds in the background. It consumes zero tokens while waiting — Claude only activates when a real change is detected.

The installed launchd daemon is always active after install and handles filesystem notifications, inbox wakeups, and worker activation. The Claude live monitor is the extra in-session layer that makes Claude speak up immediately while that Claude Code session is open.

> **Why not use `/loop` with a timer?**
> A cron or loop fires on a fixed interval and sends a prompt to Claude every N minutes regardless of whether anything changed. That consumes input tokens each tick — even for empty checks. The Monitor approach runs as a pure bash script and only wakes Claude on an actual file change.

### Restart or stop the monitor

To restart it manually:

```
/collab monitor
```

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

Every snippet includes three built-in behaviors:
- **Automatic log** after every response — each AI saves its session log without the user asking
- **Inbox check** at session start AND before every response — workers pick up `/collab assign` tasks autonomously
- **Atomic status updates** — `status: unread` → `status: done` via temp file + rename, no torn writes

This is what enables real-time, autonomous multi-AI collaboration.

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
