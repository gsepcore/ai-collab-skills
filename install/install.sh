#!/bin/bash
# ============================================================
#  AI Collab Skill — One-command installer
#  https://github.com/gsepcore/ai-collab-skills
#
#  What this installs:
#    1. Claude Code skill        → ~/.claude/skills/collab/
#    2. Daemon script            → ~/.claude/ai-collab-daemon.sh
#    3. Summary script           → ~/.claude/ai-collab-summary.py
#    4. Notifications script     → ~/.claude/ai-collab-check-notifications.py
#    5. Wakeup detector script   → ~/.claude/ai-collab-wakeup.py
#    6. Doctor script            → ~/.claude/ai-collab-doctor.py
#    7. Background daemon        → launchd (macOS) / cron (Linux)
#    8. Claude Code hooks        → ~/.claude/settings.json  (global, all projects)
#
#  Usage (from cloned repo):
#    bash install/install.sh
#
#  Usage (one-liner, no clone needed):
#    curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/install.sh | bash
# ============================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REPO="https://github.com/gsepcore/ai-collab-skills"
RAW="https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main"
SKILL_DIR="$HOME/.claude/skills/collab"
CLAUDE_DIR="$HOME/.claude"
PLIST_LABEL="com.gsepcore.ai-collab"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
YES="${AI_COLLAB_YES:-}"          # set to 1 to skip confirmations
SKIP_DAEMON="${AI_COLLAB_NO_DAEMON:-}"  # set to 1 to skip daemon

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()    { printf '\033[1m%s\033[0m\n' "$*"; }
green()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
yellow()  { printf '\033[33m⚠\033[0m  %s\n' "$*"; }
info()    { printf '  %s\n' "$*"; }
ask()     { [[ -n "$YES" ]] && return 0; read -r -p "$1 [Y/n] " _ans; [[ "$_ans" =~ ^[Nn]$ ]] && return 1 || return 0; }
need()    { command -v "$1" &>/dev/null || { echo "Error: $1 is required but not installed."; exit 1; }; }
xml_escape() { printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g; s/'"'"'/\&apos;/g'; }

# ── Detect source location ────────────────────────────────────────────────────
# Works whether run from the cloned repo OR piped via curl
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-install.sh}")" 2>/dev/null && pwd || echo "")"
REPO_DIR="$(cd "$SCRIPT_DIR/.." 2>/dev/null && pwd || echo "")"

# If SKILL.md exists locally, use it; otherwise download from GitHub
USE_LOCAL=0
[[ -f "$REPO_DIR/SKILL.md" ]] && USE_LOCAL=1

download() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if command -v curl &>/dev/null; then
    curl -fsSL "$RAW/$src" -o "$dst"
  elif command -v wget &>/dev/null; then
    wget -qO "$dst" "$RAW/$src"
  else
    echo "Error: curl or wget is required to download files."; exit 1
  fi
}

copy_or_download() {
  local rel="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [[ $USE_LOCAL -eq 1 ]]; then
    cp "$REPO_DIR/$rel" "$dst"
  else
    download "$rel" "$dst"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
bold "AI Collab Skill — Installer"
echo "  Connects multiple AI assistants to the same project in real time"
echo "  $REPO"
echo ""

# ── Checks ────────────────────────────────────────────────────────────────────
need python3
need bash

if [[ ! -d "$CLAUDE_DIR" ]]; then
  yellow "~/.claude/ not found — Claude Code may not be installed."
  info "Install Claude Code first: https://claude.ai/code"
  ask "Continue anyway?" || exit 0
  mkdir -p "$CLAUDE_DIR"
fi

# ── 1. Install Claude Code skill ─────────────────────────────────────────────
echo ""
bold "Step 1/5 — Installing Claude Code skill"

mkdir -p "$SKILL_DIR/references"
copy_or_download "SKILL.md"                      "$SKILL_DIR/SKILL.md"
copy_or_download "references/protocol.md"        "$SKILL_DIR/references/protocol.md"

green "Claude Code skill installed → $SKILL_DIR/"
info  "Use /collab read, /collab write, /collab setup, /collab assign, etc."

# ── 2. Install daemon + summary scripts ─────────────────────────────────────
echo ""
bold "Step 2/5 — Installing background scripts"

copy_or_download "install/daemon.sh"                          "$CLAUDE_DIR/ai-collab-daemon.sh"
copy_or_download "install/ai-collab-summary.py"               "$CLAUDE_DIR/ai-collab-summary.py"
copy_or_download "install/ai-collab-check-notifications.py"   "$CLAUDE_DIR/ai-collab-check-notifications.py"
copy_or_download "install/ai-collab-wakeup.py"                "$CLAUDE_DIR/ai-collab-wakeup.py"
copy_or_download "install/ai-collab-doctor.py"                "$CLAUDE_DIR/ai-collab-doctor.py"
chmod +x "$CLAUDE_DIR/ai-collab-daemon.sh"
chmod +x "$CLAUDE_DIR/ai-collab-wakeup.py"
chmod +x "$CLAUDE_DIR/ai-collab-doctor.py"

green "Daemon script        → $CLAUDE_DIR/ai-collab-daemon.sh"
green "CONTEXT.md script    → $CLAUDE_DIR/ai-collab-summary.py"
green "Notifications script → $CLAUDE_DIR/ai-collab-check-notifications.py"
green "Wakeup detector      → $CLAUDE_DIR/ai-collab-wakeup.py"
green "Doctor script        → $CLAUDE_DIR/ai-collab-doctor.py"

# ── 3. Start background daemon ───────────────────────────────────────────────
echo ""
bold "Step 3/5 — Starting background daemon"

if [[ -n "$SKIP_DAEMON" ]]; then
  yellow "Daemon skipped (AI_COLLAB_NO_DAEMON=1)"

elif [[ "$OSTYPE" == "darwin"* ]]; then
  # macOS — launchd
  mkdir -p "$HOME/Library/LaunchAgents"

  # Ask about macOS Notification Center banners (proactive even when Claude is closed)
  ENV_ITEMS=""
  add_plist_env() {
    local key="$1" value="$2"
    [ -n "$value" ] || return 0
    ENV_ITEMS="${ENV_ITEMS}        <key>${key}</key>
        <string>$(xml_escape "$value")</string>
"
  }

  if [[ -z "$YES" ]]; then
    echo ""
    info "macOS Notification Center: fire a banner when another AI completes a task?"
    info "  Survives Claude Code closing, Mac sleep, restart, and shutdown."
    info "  You can change this later by editing the plist file."
    if ask "  Enable macOS notifications?"; then
      add_plist_env "AI_COLLAB_OS_NOTIFY" "1"
      green "macOS notifications enabled — banners will appear on AI activity"
    else
      info "macOS notifications disabled — set AI_COLLAB_OS_NOTIFY=1 in the plist later if you change your mind"
    fi
  elif [[ "${AI_COLLAB_OS_NOTIFY:-}" = "1" ]]; then
    add_plist_env "AI_COLLAB_OS_NOTIFY" "1"
  fi

  # launchd does not inherit the user's interactive shell PATH, so node/nvm
  # /homebrew binaries (opencode, codex, claude) are unreachable to the daemon
  # by default. Capture the user's current PATH plus common toolchain dirs so
  # CLI and visible adapters can spawn those binaries when triggered.
  AI_COLLAB_DAEMON_PATH="${AI_COLLAB_DAEMON_PATH:-$PATH}"
  for extra in \
    "$HOME/.nvm/versions/node/$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)/bin" \
    "/opt/homebrew/bin" \
    "/usr/local/bin"
  do
    case ":$AI_COLLAB_DAEMON_PATH:" in
      *":$extra:"*) : ;;
      *) [[ -n "$extra" && -d "$extra" ]] && AI_COLLAB_DAEMON_PATH="$extra:$AI_COLLAB_DAEMON_PATH" ;;
    esac
  done
  add_plist_env "PATH" "$AI_COLLAB_DAEMON_PATH"

  # Persist optional wakeup adapter settings into launchd. launchd does not
  # inherit the user's interactive shell env, so opt-in automation must be
  # written here at install time or edited into the plist later.
  add_plist_env "AI_COLLAB_WAKEUP_ADAPTER" "${AI_COLLAB_WAKEUP_ADAPTER:-}"
  add_plist_env "AI_COLLAB_WAKEUP_MAX_ATTEMPTS" "${AI_COLLAB_WAKEUP_MAX_ATTEMPTS:-}"
  add_plist_env "AI_COLLAB_WAKEUP_ADAPTER_TIMEOUT" "${AI_COLLAB_WAKEUP_ADAPTER_TIMEOUT:-}"
  add_plist_env "AI_COLLAB_WAKEUP_CLI_PROJECTS" "${AI_COLLAB_WAKEUP_CLI_PROJECTS:-}"
  add_plist_env "AI_COLLAB_WAKEUP_CLI_TARGETS" "${AI_COLLAB_WAKEUP_CLI_TARGETS:-}"
  add_plist_env "AI_COLLAB_WAKEUP_VISIBLE_TARGETS" "${AI_COLLAB_WAKEUP_VISIBLE_TARGETS:-}"
  add_plist_env "AI_COLLAB_WAKEUP_DRY_RUN" "${AI_COLLAB_WAKEUP_DRY_RUN:-}"
  add_plist_env "AI_COLLAB_CODEX_BIN" "${AI_COLLAB_CODEX_BIN:-}"
  add_plist_env "AI_COLLAB_OPENCODE_BIN" "${AI_COLLAB_OPENCODE_BIN:-}"
  add_plist_env "AI_COLLAB_CLAUDE_BIN" "${AI_COLLAB_CLAUDE_BIN:-}"
  add_plist_env "AI_COLLAB_OPENCODE_PORTS" "${AI_COLLAB_OPENCODE_PORTS:-}"
  add_plist_env "AI_COLLAB_ANTIGRAVITY_BIN" "${AI_COLLAB_ANTIGRAVITY_BIN:-}"
  add_plist_env "AI_COLLAB_ANTIGRAVITY_MODE" "${AI_COLLAB_ANTIGRAVITY_MODE:-}"

  ENV_BLOCK=""
  if [[ -n "$ENV_ITEMS" ]]; then
    ENV_BLOCK="    <key>EnvironmentVariables</key>
    <dict>
${ENV_ITEMS}    </dict>
"
  fi

  cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
${ENV_BLOCK}    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${CLAUDE_DIR}/ai-collab-daemon.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/tmp/ai-collab-daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ai-collab-daemon.err</string>
</dict>
</plist>
PLIST

  # Stop previous instance if running
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load -w "$PLIST_PATH"

  green "launchd daemon loaded → auto-starts on login, survives sleep"
  info  "Logs: /tmp/ai-collab-daemon.log"
  info  "Stop: launchctl unload ~/Library/LaunchAgents/${PLIST_LABEL}.plist"

elif command -v crontab &>/dev/null; then
  # Linux / WSL — cron fallback
  CRON_CMD="*/1 * * * * /bin/bash $CLAUDE_DIR/ai-collab-daemon.sh"
  ( crontab -l 2>/dev/null | grep -v "ai-collab-daemon"; echo "$CRON_CMD" ) | crontab -
  green "Cron job installed (checks every minute)"
  yellow "Note: cron does not survive sleep on laptops. launchd (macOS) is recommended."
else
  yellow "No daemon started — launchd and cron not available."
  info  "Start manually: bash $CLAUDE_DIR/ai-collab-daemon.sh &"
fi

# ── 4. Install global Claude Code hooks ─────────────────────────────────────
echo ""
bold "Step 4/5 — Installing Claude Code hooks (global)"
info  "These hooks work automatically in ALL your projects."

SETTINGS_FILE="$CLAUDE_DIR/settings.json"

python3 - "$SETTINGS_FILE" << 'PYEOF'
import json, sys, os, pathlib

settings_file = sys.argv[1]
home = str(pathlib.Path.home())

# Hooks to install
NEW_HOOKS = {
  "SessionStart": [{
    "hooks": [{
      "type": "command",
      "timeout": 10,
      "command": (
        "bash -c '"
        "ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd); "
        "CTX=\"$ROOT/.ai-collab/CONTEXT.md\"; "
        "NOTIF=\"$HOME/.ai-collab-notifications.json\"; "
        "if [ -f \"$CTX\" ]; then "
          "echo \"[AI-COLLAB SESSION RECOVERY]\"; "
          "echo \"Project: $(basename $ROOT)\"; "
          "echo \"---\"; cat \"$CTX\"; echo \"---\"; "
        "fi; "
        "if [ -f \"$NOTIF\" ]; then "
          "CONTENT=$(cat \"$NOTIF\"); "
          "if [ \"$CONTENT\" != \"[]\" ] && [ -n \"$CONTENT\" ]; then "
            "echo \"[PENDING NOTIFICATIONS FROM OTHER AIs]\"; "
            "echo \"$CONTENT\"; "
            "echo \"[]\" > \"$NOTIF\"; "
          "fi; "
        "fi'"
      )
    }]
  }],
  "Stop": [{
    "hooks": [{
      "type": "command",
      "command": "python3 ~/.claude/ai-collab-summary.py 2>/dev/null || true",
      "timeout": 15,
      "async": True
    }]
  }],
  "UserPromptSubmit": [{
    "hooks": [{
      "type": "command",
      "timeout": 5,
      "command": "python3 ~/.claude/ai-collab-check-notifications.py 2>/dev/null || true"
    }]
  }]
}

# Load existing settings
try:
    with open(settings_file) as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except json.JSONDecodeError:
    print(f"Warning: {settings_file} has invalid JSON — creating backup and starting fresh.")
    os.rename(settings_file, settings_file + ".bak")
    settings = {}

# Merge hooks — add AI-collab hooks, preserve everything else
existing_hooks = settings.get("hooks", {})

for event, new_list in NEW_HOOKS.items():
    if event not in existing_hooks:
        existing_hooks[event] = new_list
    else:
        # Only add if no AI-collab hook already exists for this event
        existing_cmds = [
            h.get("command", "")
            for entry in existing_hooks[event]
            for h in entry.get("hooks", [])
        ]
        already_installed = any("ai-collab" in cmd for cmd in existing_cmds)
        if not already_installed:
            existing_hooks[event].extend(new_list)
        else:
            # Update existing ai-collab hook to latest version
            for entry in existing_hooks[event]:
                for h in entry.get("hooks", []):
                    if "ai-collab" in h.get("command", ""):
                        h.update(new_list[0]["hooks"][0])
            print(f"  Updated existing {event} hook.")

settings["hooks"] = existing_hooks

# Atomic write
tmp = settings_file + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_file)

print(f"  Hooks written to {settings_file}")
PYEOF

green "SessionStart hook  → loads CONTEXT.md + notifications on every new session"
green "UserPromptSubmit   → shows pending notifications before each message"
green "Stop hook          → auto-generates CONTEXT.md after every response"

# ── 5. Initialize .ai-collab/ in current project (optional) ─────────────────
echo ""
bold "Step 5/5 — Project setup (optional)"

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")

if [[ -n "$PROJECT_ROOT" ]]; then
  info "Detected project: $PROJECT_ROOT"
  COLLAB_DIR="$PROJECT_ROOT/.ai-collab"

  if ask "Set up .ai-collab/ in this project now?"; then
    mkdir -p "$COLLAB_DIR"

    # Copy PROTOCOL.md
    if [[ $USE_LOCAL -eq 1 ]]; then
      cp "$REPO_DIR/references/protocol.md" "$COLLAB_DIR/PROTOCOL.md"
    else
      download "references/protocol.md" "$COLLAB_DIR/PROTOCOL.md"
    fi

    # Add to .gitignore
    GITIGNORE="$PROJECT_ROOT/.gitignore"
    if [[ -f "$GITIGNORE" ]]; then
      if ! grep -q "^\.ai-collab" "$GITIGNORE"; then
        echo ".ai-collab/" >> "$GITIGNORE"
        green "Added .ai-collab/ to .gitignore"
      else
        info ".ai-collab/ already in .gitignore"
      fi
    else
      echo ".ai-collab/" > "$GITIGNORE"
      green "Created .gitignore with .ai-collab/"
    fi

    # Onboarding welcome — give the first AI that opens this project
    # a concrete unread task so the protocol kicks in automatically.
    WELCOME_FILE="$COLLAB_DIR/inbox-all.md"
    if [[ ! -f "$WELCOME_FILE" ]]; then
      PROJECT_NAME=$(basename "$PROJECT_ROOT")
      NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
      cat > "$WELCOME_FILE" << INBOX
---
from: Claude Code (setup)
to: all
priority: normal
updated: $NOW
status: unread
---

## Welcome to the multi-AI team for this project

You are joining a shared collaboration protocol. The orchestrating director
is Claude Code. Other AIs may already be active on this codebase.

### First-response checklist
1. Read \`.ai-collab/CONTEXT.md\` if it exists; otherwise read \`.ai-collab/PROTOCOL.md\`.
2. Check your specific inbox: \`.ai-collab/inbox-{your-slug}.md\` (slug = lowercase tool name, e.g. \`cursor\`, \`opencode\`, \`codex\`, \`windsurf\`, \`copilot\`).
3. Save your first session log at \`.ai-collab/{your-slug}-{YYYYMMDD-HHMMSS}.md\` using the standard frontmatter + sections format (see PROTOCOL.md).
4. Tell the user what role you are taking in the team and what you read from existing logs.

### Project context
- Project: $PROJECT_NAME
- Project root: $PROJECT_ROOT
- Director: Claude Code
- Coordination: filesystem-based via \`.ai-collab/\`

When you have completed the onboarding, change \`status: unread\` → \`status: done\` via atomic write (temp file + rename).
INBOX
      green "Onboarding inbox-all.md created — first AI to open this project will self-orient"
    else
      info "inbox-all.md already present — onboarding message not overwritten"
    fi

    green ".ai-collab/ ready → $COLLAB_DIR/"
  fi
else
  info "Not in a git repo — skipping project setup."
  info "Run 'git init' and then '/collab setup' inside Claude Code to set up a project."
fi

# ── Ask which AI tools to add snippets for ──────────────────────────────────
echo ""
bold "AI Tool Snippets"
info "Add collab protocol snippets to your other AI tools:"
echo ""
info "  Cursor    → add to .cursorrules"
info "  Windsurf  → add to .windsurfrules"
info "  VS Code   → add to .github/copilot-instructions.md"
info "  OpenCode  → add to system prompt"
info "  Codex     → add to system prompt"
info "  Other     → see SKILL.md or run /collab setup inside Claude Code"
echo ""
info "The snippets are in: $SKILL_DIR/references/protocol.md"
info "Or run '/collab setup' in Claude Code — it will do this interactively."

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
bold "✅ AI Collab Skill installed!"
echo ""
echo "  What's running:"
echo "    🔄 Background daemon   — watches .ai-collab/ across all projects"
echo "    🪝 SessionStart hook   — auto-loads context on every Claude session"
echo "    🪝 UserPromptSubmit    — shows notifications before each message"
echo "    🪝 Stop hook           — auto-generates CONTEXT.md after each response"
echo "    📨 Wakeup detector     — detects unread inbox tasks"
echo "    🩺 Doctor script       — verifies install health"
echo "    📚 /collab skill       — 8 commands available in Claude Code"
echo ""
echo "  Try it now — open Claude Code and type:"
echo "    /collab setup          — set up a new project"
echo "    /collab read           — see what other AIs have been working on"
echo "    /collab write          — save your current context"
echo "    /collab assign codex [task]  — send a task to another AI"
echo ""
echo "  Check install health:"
echo "    python3 ~/.claude/ai-collab-doctor.py"
echo ""
echo "  Uninstall:"
if [[ $USE_LOCAL -eq 1 ]]; then
  echo "    bash $SCRIPT_DIR/uninstall.sh"
else
  echo "    curl -fsSL $RAW/install/uninstall.sh | bash"
fi
echo ""
echo "  Docs: $REPO"
echo ""
