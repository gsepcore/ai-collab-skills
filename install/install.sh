#!/bin/bash
# ============================================================
#  AI Collab Skill — One-command installer
#  https://github.com/gsepcore/ai-collab-skills
#
#  What this installs:
#    1. Claude/Codex skills      → ~/.claude/skills/collab/ + ~/.codex/skills/collab/
#    2. Daemon script            → ~/.claude/ai-collab-daemon.sh
#    3. Summary script           → ~/.claude/ai-collab-summary.py
#    4. Notifications script     → ~/.claude/ai-collab-check-notifications.py
#    5. Wakeup detector script   → ~/.claude/ai-collab-wakeup.py
#    6. Auto-onboard script      → ~/.claude/ai-collab-auto-onboard.py
#    7. Unified setup/migrator   → ~/.claude/ai-collab-setup.py
#    8. Project onboarding       → ~/.claude/ai-collab-project-setup.py
#    9. Multi-agent orchestrator → ~/.claude/ai-collab-orchestrate.py
#   10. Team role onboarding     → ~/.claude/ai-collab-team.py
#   11. Natural conversation CLI → ~/.claude/ai-collab-converse.py
#   12. Bounded debate mode      → ~/.claude/ai-collab-debate.py
#   13. Live observer            → ~/.claude/ai-collab-observer.py
#   14. Doctor script            → ~/.claude/ai-collab-doctor.py
#   15. Self-updater             → ~/.claude/ai-collab-update.py
#   16. Reboot recovery          → ~/.claude/ai-collab-recover.py
#   17. Codex bridge API         → ~/.claude/ai-collab-codex-bridge.py
#   18. Visible IDE bridge       → VS Code/Antigravity/Cursor/Windsurf extension
#   19. OCR engine               → tesseract auto-install when possible
#   20. Background daemon        → launchd (macOS) / cron (Linux)
#   21. Claude Code hooks        → ~/.claude/settings.json  (global, all projects)
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
CLAUDE_SKILL_DIR="$HOME/.claude/skills/collab"
CODEX_BASE_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_SKILL_DIR="$CODEX_BASE_DIR/skills/collab"
CLAUDE_DIR="$HOME/.claude"
PLIST_LABEL="com.gsepcore.ai-collab"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
BRIDGE_PLIST_LABEL="com.gsepcore.ai-collab-codex-bridge"
BRIDGE_PLIST_PATH="$HOME/Library/LaunchAgents/${BRIDGE_PLIST_LABEL}.plist"
YES="${AI_COLLAB_YES:-}"          # set to 1 to skip confirmations
SKIP_DAEMON="${AI_COLLAB_NO_DAEMON:-}"  # set to 1 to skip daemon
SKIP_CODEX_BRIDGE="${AI_COLLAB_NO_CODEX_BRIDGE:-}"  # set to 1 to skip Codex bridge API
INSTALL_OCR="${AI_COLLAB_INSTALL_OCR:-1}"  # set to 0 to skip OCR engine install
SKIP_IDE_BRIDGE="${AI_COLLAB_NO_IDE_BRIDGE:-}"  # set to 1 to skip visible integrated-terminal bridge
SKIP_PROJECT_SETUP="${AI_COLLAB_SKIP_PROJECT_SETUP:-}"  # internal: unified setup prevents installer recursion

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()    { printf '\033[1m%s\033[0m\n' "$*"; }
green()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
yellow()  { printf '\033[33m⚠\033[0m  %s\n' "$*"; }
info()    { printf '  %s\n' "$*"; }
ask()     { [[ -n "$YES" ]] && return 0; read -r -p "$1 [Y/n] " _ans; [[ "$_ans" =~ ^[Nn]$ ]] && return 1 || return 0; }
need()    { command -v "$1" &>/dev/null || { echo "Error: $1 is required but not installed."; exit 1; }; }
xml_escape() { printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g; s/'"'"'/\&apos;/g'; }
have()    { command -v "$1" &>/dev/null; }
sanitize_path() {
  local raw="$1"
  local clean="" part
  IFS=':' read -r -a _path_parts <<< "$raw"
  for part in "${_path_parts[@]}"; do
    [[ -n "$part" && -d "$part" ]] || continue
    case ":$clean:" in
      *":$part:"*) : ;;
      *) clean="${clean:+$clean:}$part" ;;
    esac
  done
  printf '%s' "$clean"
}

install_ocr_engine() {
  if [[ "${AI_COLLAB_OBSERVER_SEMANTIC_OCR:-1}" = "0" || "$INSTALL_OCR" = "0" || "${AI_COLLAB_NO_OCR_INSTALL:-}" = "1" ]]; then
    info "OCR engine install skipped — semantic vision will use metadata-only mode unless tesseract is already available."
    return 0
  fi

  if have tesseract; then
    green "OCR engine ready → $(command -v tesseract)"
    return 0
  fi

  if [[ "$OSTYPE" == "darwin"* ]]; then
    if have brew; then
      info "Installing OCR engine for semantic vision: brew install tesseract"
      if brew install tesseract; then
        green "OCR engine installed → $(command -v tesseract || echo tesseract)"
      else
        yellow "OCR engine install failed; AI Collab will continue in metadata-only mode."
        info "Run later: brew install tesseract"
      fi
    else
      yellow "Homebrew not found; OCR engine could not be installed automatically."
      info "AI Collab still works. To enable OCR later: install Homebrew, then run 'brew install tesseract'."
    fi
    return 0
  fi

  if [[ "$OSTYPE" == "linux"* ]]; then
    local prefix=()
    if [[ "$(id -u)" != "0" ]]; then
      if have sudo; then
        prefix=(sudo)
      else
        yellow "OCR auto-install needs root or sudo; continuing in metadata-only mode."
        return 0
      fi
    fi

    info "Installing OCR engine for semantic vision"
    if have apt-get; then
      if "${prefix[@]}" apt-get update && "${prefix[@]}" apt-get install -y tesseract-ocr; then
        green "OCR engine installed → $(command -v tesseract || echo tesseract)"
      else
        yellow "OCR engine install failed; AI Collab will continue in metadata-only mode."
      fi
    elif have dnf; then
      if "${prefix[@]}" dnf install -y tesseract; then
        green "OCR engine installed → $(command -v tesseract || echo tesseract)"
      else
        yellow "OCR engine install failed; AI Collab will continue in metadata-only mode."
      fi
    elif have yum; then
      if "${prefix[@]}" yum install -y tesseract; then
        green "OCR engine installed → $(command -v tesseract || echo tesseract)"
      else
        yellow "OCR engine install failed; AI Collab will continue in metadata-only mode."
      fi
    elif have pacman; then
      if "${prefix[@]}" pacman -Sy --noconfirm tesseract; then
        green "OCR engine installed → $(command -v tesseract || echo tesseract)"
      else
        yellow "OCR engine install failed; AI Collab will continue in metadata-only mode."
      fi
    else
      yellow "No supported package manager found for OCR auto-install."
    fi
  fi
}

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

install_visible_ide_bridge() {
  if [[ -n "$SKIP_IDE_BRIDGE" ]]; then
    yellow "Visible IDE bridge skipped (AI_COLLAB_NO_IDE_BRIDGE=1)"
    return 0
  fi
  local source_dir="$CLAUDE_DIR/ai-collab-vscode-bridge"
  local vsix="$CLAUDE_DIR/ai-collab-visible-bridge.vsix"
  mkdir -p "$source_dir"
  copy_or_download "install/build-vscode-bridge.py" "$CLAUDE_DIR/ai-collab-build-vscode-bridge.py"
  copy_or_download "install/vscode-ai-collab-bridge/package.json" "$source_dir/package.json"
  copy_or_download "install/vscode-ai-collab-bridge/extension.js" "$source_dir/extension.js"
  chmod +x "$CLAUDE_DIR/ai-collab-build-vscode-bridge.py"
  python3 "$CLAUDE_DIR/ai-collab-build-vscode-bridge.py" --source "$source_dir" --output "$vsix" >/dev/null

  local installed=0 cli seen=""
  local candidates=(
    "$(command -v antigravity-ide 2>/dev/null || true)"
    "/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide"
    "$(command -v code 2>/dev/null || true)"
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
    "$(command -v cursor 2>/dev/null || true)"
    "/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
    "$(command -v windsurf 2>/dev/null || true)"
    "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf"
  )
  for cli in "${candidates[@]}"; do
    [[ -n "$cli" && -x "$cli" ]] || continue
    case ":$seen:" in *":$cli:"*) continue ;; esac
    seen="${seen:+$seen:}$cli"
    if "$cli" --install-extension "$vsix" --force >/dev/null 2>&1; then
      green "Visible IDE bridge installed → $cli"
      installed=1
    else
      yellow "Could not install visible IDE bridge through $cli"
    fi
  done
  if [[ $installed -eq 0 ]]; then
    yellow "No supported VS Code-compatible IDE CLI was found; Claude Code visible wakeups will fail closed."
  else
    info "Reload each open IDE window once so the visible bridge activates."
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
bold "Step 1/6 — Installing Claude Code skill"

mkdir -p "$CLAUDE_SKILL_DIR/references" "$CODEX_SKILL_DIR/references"
copy_or_download "SKILL.md"                      "$CLAUDE_SKILL_DIR/SKILL.md"
copy_or_download "references/protocol.md"        "$CLAUDE_SKILL_DIR/references/protocol.md"
copy_or_download "SKILL.md"                      "$CODEX_SKILL_DIR/SKILL.md"
copy_or_download "references/protocol.md"        "$CODEX_SKILL_DIR/references/protocol.md"

green "Claude Code skill installed → $CLAUDE_SKILL_DIR/"
green "Codex skill installed       → $CODEX_SKILL_DIR/"
info  "Use /collab read, /collab write, /collab setup, /collab assign, etc."

# ── 2. Install daemon + summary scripts ─────────────────────────────────────
echo ""
bold "Step 2/6 — Installing background scripts"

copy_or_download "install/daemon.sh"                          "$CLAUDE_DIR/ai-collab-daemon.sh"
copy_or_download "install/ai-collab-summary.py"               "$CLAUDE_DIR/ai-collab-summary.py"
copy_or_download "install/ai-collab-check-notifications.py"   "$CLAUDE_DIR/ai-collab-check-notifications.py"
copy_or_download "install/ai-collab-wakeup.py"                "$CLAUDE_DIR/ai-collab-wakeup.py"
copy_or_download "install/ai-collab-auto-onboard.py"          "$CLAUDE_DIR/ai-collab-auto-onboard.py"
copy_or_download "install/ai-collab-setup.py"                 "$CLAUDE_DIR/ai-collab-setup.py"
copy_or_download "install/ai-collab-project-setup.py"         "$CLAUDE_DIR/ai-collab-project-setup.py"
copy_or_download "install/ai-collab-orchestrate.py"           "$CLAUDE_DIR/ai-collab-orchestrate.py"
copy_or_download "install/ai-collab-team.py"                  "$CLAUDE_DIR/ai-collab-team.py"
copy_or_download "install/ai-collab-session.py"               "$CLAUDE_DIR/ai-collab-session.py"
copy_or_download "install/ai-collab-turn.py"                  "$CLAUDE_DIR/ai-collab-turn.py"
copy_or_download "install/ai-collab-converse.py"              "$CLAUDE_DIR/ai-collab-converse.py"
copy_or_download "install/ai-collab-debate.py"                "$CLAUDE_DIR/ai-collab-debate.py"
copy_or_download "install/ai-collab-observer.py"              "$CLAUDE_DIR/ai-collab-observer.py"
copy_or_download "install/ai-collab-see.py"                   "$CLAUDE_DIR/ai-collab-see.py"
copy_or_download "install/ai-collab-doctor.py"                "$CLAUDE_DIR/ai-collab-doctor.py"
copy_or_download "install/ai-collab-update.py"                "$CLAUDE_DIR/ai-collab-update.py"
copy_or_download "install/ai-collab-recover.py"               "$CLAUDE_DIR/ai-collab-recover.py"
copy_or_download "install/ai-collab-codex-bridge.py"          "$CLAUDE_DIR/ai-collab-codex-bridge.py"
chmod +x "$CLAUDE_DIR/ai-collab-daemon.sh"
chmod +x "$CLAUDE_DIR/ai-collab-wakeup.py"
chmod +x "$CLAUDE_DIR/ai-collab-auto-onboard.py"
chmod +x "$CLAUDE_DIR/ai-collab-setup.py"
chmod +x "$CLAUDE_DIR/ai-collab-project-setup.py"
chmod +x "$CLAUDE_DIR/ai-collab-orchestrate.py"
chmod +x "$CLAUDE_DIR/ai-collab-team.py"
chmod +x "$CLAUDE_DIR/ai-collab-session.py"
chmod +x "$CLAUDE_DIR/ai-collab-turn.py"
chmod +x "$CLAUDE_DIR/ai-collab-converse.py"
chmod +x "$CLAUDE_DIR/ai-collab-debate.py"
chmod +x "$CLAUDE_DIR/ai-collab-observer.py"
chmod +x "$CLAUDE_DIR/ai-collab-see.py"
chmod +x "$CLAUDE_DIR/ai-collab-doctor.py"
chmod +x "$CLAUDE_DIR/ai-collab-update.py"
chmod +x "$CLAUDE_DIR/ai-collab-recover.py"
chmod +x "$CLAUDE_DIR/ai-collab-codex-bridge.py"

green "Daemon script        → $CLAUDE_DIR/ai-collab-daemon.sh"
green "CONTEXT.md script    → $CLAUDE_DIR/ai-collab-summary.py"
green "Notifications script → $CLAUDE_DIR/ai-collab-check-notifications.py"
green "Wakeup detector      → $CLAUDE_DIR/ai-collab-wakeup.py"
green "Auto-onboard script  → $CLAUDE_DIR/ai-collab-auto-onboard.py"
green "Unified setup        → $CLAUDE_DIR/ai-collab-setup.py"
green "Project onboarding   → $CLAUDE_DIR/ai-collab-project-setup.py"
green "Run orchestrator     → $CLAUDE_DIR/ai-collab-orchestrate.py"
green "Team role onboarding → $CLAUDE_DIR/ai-collab-team.py"
green "Session identity     → $CLAUDE_DIR/ai-collab-session.py"
green "Always-on turn router → $CLAUDE_DIR/ai-collab-turn.py"
green "Conversation helper  → $CLAUDE_DIR/ai-collab-converse.py"
green "Bounded debate mode  → $CLAUDE_DIR/ai-collab-debate.py"
green "Live observer        → $CLAUDE_DIR/ai-collab-observer.py"
green "Doctor script        → $CLAUDE_DIR/ai-collab-doctor.py"
green "Self-updater         → $CLAUDE_DIR/ai-collab-update.py"
green "Reboot recovery      → $CLAUDE_DIR/ai-collab-recover.py"
green "Codex bridge API     → $CLAUDE_DIR/ai-collab-codex-bridge.py"

install_visible_ide_bridge

# ── 3. Install semantic vision OCR engine ───────────────────────────────────
echo ""
bold "Step 3/6 — Installing semantic vision OCR"
install_ocr_engine

# ── 4. Start background daemon ───────────────────────────────────────────────
echo ""
bold "Step 4/6 — Starting background daemon"

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
  AI_COLLAB_DAEMON_PATH="$(sanitize_path "${AI_COLLAB_DAEMON_PATH:-$PATH}")"
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
  add_plist_env "AI_COLLAB_OPENCODE_SYNTHETIC" "${AI_COLLAB_OPENCODE_SYNTHETIC:-}"
  add_plist_env "AI_COLLAB_ANTIGRAVITY_BIN" "${AI_COLLAB_ANTIGRAVITY_BIN:-}"
  add_plist_env "AI_COLLAB_ANTIGRAVITY_MODE" "${AI_COLLAB_ANTIGRAVITY_MODE:-}"
  add_plist_env "AI_COLLAB_OBSERVER" "${AI_COLLAB_OBSERVER:-1}"
  add_plist_env "AI_COLLAB_OBSERVER_ACTIVE_SECONDS" "${AI_COLLAB_OBSERVER_ACTIVE_SECONDS:-}"
  add_plist_env "AI_COLLAB_OBSERVER_STALE_CLAIM_SECONDS" "${AI_COLLAB_OBSERVER_STALE_CLAIM_SECONDS:-}"
  add_plist_env "AI_COLLAB_OBSERVER_MAX_EVENTS" "${AI_COLLAB_OBSERVER_MAX_EVENTS:-}"
  add_plist_env "AI_COLLAB_OBSERVER_SCREENSHOTS" "${AI_COLLAB_OBSERVER_SCREENSHOTS:-1}"
  add_plist_env "AI_COLLAB_OBSERVER_SCREENSHOT_MODE" "${AI_COLLAB_OBSERVER_SCREENSHOT_MODE:-project}"
  add_plist_env "AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL" "${AI_COLLAB_OBSERVER_SCREENSHOT_INTERVAL:-}"
  add_plist_env "AI_COLLAB_OBSERVER_SCREENSHOT_ACTIVE_ONLY" "${AI_COLLAB_OBSERVER_SCREENSHOT_ACTIVE_ONLY:-0}"
  add_plist_env "AI_COLLAB_OBSERVER_SCREENSHOT_MAX_KEEP" "${AI_COLLAB_OBSERVER_SCREENSHOT_MAX_KEEP:-}"
  add_plist_env "AI_COLLAB_OBSERVER_SEMANTIC_OCR" "${AI_COLLAB_OBSERVER_SEMANTIC_OCR:-1}"
  add_plist_env "AI_COLLAB_OBSERVER_TESSERACT_BIN" "${AI_COLLAB_OBSERVER_TESSERACT_BIN:-}"
  add_plist_env "AI_COLLAB_PROJECT_ALIASES" "${AI_COLLAB_PROJECT_ALIASES:-}"
  add_plist_env "AI_COLLAB_AUTO_UPDATE" "${AI_COLLAB_AUTO_UPDATE:-1}"
  add_plist_env "AI_COLLAB_UPDATE_INTERVAL_SECONDS" "${AI_COLLAB_UPDATE_INTERVAL_SECONDS:-21600}"
  add_plist_env "AI_COLLAB_UPDATE_RAW_BASE" "${AI_COLLAB_UPDATE_RAW_BASE:-}"
  update_local_source="${AI_COLLAB_UPDATE_LOCAL_SOURCE:-}"
  if [[ -z "$update_local_source" && $USE_LOCAL -eq 1 ]]; then
    update_local_source="$REPO_DIR"
  fi
  add_plist_env "AI_COLLAB_UPDATE_LOCAL_SOURCE" "$update_local_source"
  add_plist_env "AI_COLLAB_UPDATE_MAX_DEPTH" "${AI_COLLAB_UPDATE_MAX_DEPTH:-}"
  add_plist_env "AI_COLLAB_RECOVERY" "${AI_COLLAB_RECOVERY:-1}"
  add_plist_env "AI_COLLAB_RECOVERY_INTERVAL_SECONDS" "${AI_COLLAB_RECOVERY_INTERVAL_SECONDS:-300}"
  add_plist_env "AI_COLLAB_RECOVERY_CONTEXT_MAX_AGE" "${AI_COLLAB_RECOVERY_CONTEXT_MAX_AGE:-3600}"

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

  if [[ -z "$SKIP_CODEX_BRIDGE" ]]; then
    BRIDGE_ENV_ITEMS=""
    add_bridge_env() {
      local key="$1" value="$2"
      [ -n "$value" ] || return 0
      BRIDGE_ENV_ITEMS="${BRIDGE_ENV_ITEMS}        <key>${key}</key>
        <string>$(xml_escape "$value")</string>
"
    }
    add_bridge_env "AI_COLLAB_CODEX_BRIDGE_MODE" "${AI_COLLAB_CODEX_BRIDGE_MODE:-background}"
    add_bridge_env "AI_COLLAB_CODEX_BRIDGE_TOKEN" "${AI_COLLAB_CODEX_BRIDGE_TOKEN:-}"
    add_bridge_env "AI_COLLAB_CODEX_BRIDGE_TIMEOUT" "${AI_COLLAB_CODEX_BRIDGE_TIMEOUT:-}"
    add_bridge_env "AI_COLLAB_WAKEUP_CLI_PROJECTS" "${AI_COLLAB_WAKEUP_CLI_PROJECTS:-}"
    add_bridge_env "AI_COLLAB_CODEX_ACP_COMMAND" "${AI_COLLAB_CODEX_ACP_COMMAND:-}"
    BRIDGE_ENV_BLOCK=""
    if [[ -n "$BRIDGE_ENV_ITEMS" ]]; then
      BRIDGE_ENV_BLOCK="    <key>EnvironmentVariables</key>
    <dict>
${BRIDGE_ENV_ITEMS}    </dict>
"
    fi
    BRIDGE_PORT="${AI_COLLAB_CODEX_BRIDGE_PORT:-8765}"
    cat > "$BRIDGE_PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${BRIDGE_PLIST_LABEL}</string>
${BRIDGE_ENV_BLOCK}    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${CLAUDE_DIR}/ai-collab-codex-bridge.py</string>
        <string>serve</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>${BRIDGE_PORT}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/tmp/ai-collab-codex-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ai-collab-codex-bridge.err</string>
</dict>
</plist>
PLIST
    launchctl unload "$BRIDGE_PLIST_PATH" 2>/dev/null || true
    launchctl load -w "$BRIDGE_PLIST_PATH"
    green "Codex bridge loaded → http://127.0.0.1:${BRIDGE_PORT}/health"
    info  "Bridge logs: /tmp/ai-collab-codex-bridge.log"
    info  "Stop: launchctl unload ~/Library/LaunchAgents/${BRIDGE_PLIST_LABEL}.plist"
  else
    yellow "Codex bridge skipped (AI_COLLAB_NO_CODEX_BRIDGE=1)"
  fi

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

# ── 5. Install global Claude Code hooks ─────────────────────────────────────
echo ""
bold "Step 5/6 — Installing Claude Code hooks (global)"
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
        "CAP=\"$ROOT/.ai-collab/capabilities.json\"; "
        "NOTIF=\"$HOME/.ai-collab-notifications.json\"; "
        "if [ -f \"$CTX\" ]; then "
          "echo \"[AI-COLLAB SESSION RECOVERY]\"; "
          "echo \"Project: $(basename $ROOT)\"; "
          "echo \"---\"; cat \"$CTX\"; echo \"---\"; "
        "fi; "
        "if [ -f \"$CAP\" ]; then "
          "echo \"[AI-COLLAB CAPABILITY MATRIX — READ BEFORE DELIVERY]\"; "
          "cat \"$CAP\"; "
          "python3 \"$HOME/.claude/ai-collab-turn.py\" preflight --root \"$ROOT\" --agent claude-code --surface-kind terminal 2>/dev/null || true; "
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
      "command": "bash -c 'ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd); python3 ~/.claude/ai-collab-turn.py preflight --root \"$ROOT\" --agent claude-code --surface-kind terminal --hook-stdin 2>/dev/null || true; python3 ~/.claude/ai-collab-check-notifications.py 2>/dev/null || true'"
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

# Allow only the collaboration/vision helpers needed for autonomous visible
# replies. This avoids broad bypass-permissions mode while preventing a real
# agent turn from stalling on the command that writes its shared-thread reply.
permissions = settings.get("permissions")
if not isinstance(permissions, dict):
    permissions = {}
allow = permissions.get("allow")
if not isinstance(allow, list):
    allow = []
collab_rules = [
    "Bash(python3 ~/.claude/ai-collab-converse.py *)",
    "Bash(python3 ~/.claude/ai-collab-see.py *)",
    "Bash(python3 ~/.claude/ai-collab-observer.py *)",
    "Read(~/.claude/ai-collab-converse.py)",
    "Read(~/.claude/ai-collab-see.py)",
    "Read(~/.claude/ai-collab-observer.py)",
]
for rule in collab_rules:
    if rule not in allow:
        allow.append(rule)
permissions["allow"] = allow
settings["permissions"] = permissions

# Atomic write
tmp = settings_file + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_file)

print(f"  Hooks written to {settings_file}")
PYEOF

green "SessionStart hook  → loads CONTEXT.md + capability matrix + notifications"
green "UserPromptSubmit   → injects always-on routing + pending notifications"
green "Stop hook          → auto-generates CONTEXT.md after every response"
green "Claude permissions → limited allow rules for AI Collab conversation/vision helpers"

# ── 6. Initialize .ai-collab/ in current project (optional) ─────────────────
echo ""
bold "Step 6/6 — Project onboarding (optional)"

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")

if [[ -n "$SKIP_PROJECT_SETUP" ]]; then
  info "Project setup skipped — unified setup will migrate the current project after the global reinstall."
elif [[ -n "$PROJECT_ROOT" ]]; then
  info "Detected project: $PROJECT_ROOT"
  if ask "Run agent-first onboarding for this project now?"; then
    if [[ -n "$YES" ]]; then
      python3 "$CLAUDE_DIR/ai-collab-project-setup.py" --root "$PROJECT_ROOT" --non-interactive
    else
      python3 "$CLAUDE_DIR/ai-collab-project-setup.py" --root "$PROJECT_ROOT"
    fi
    green "Project onboarded → $PROJECT_ROOT/.ai-collab/"
  fi
else
  info "Not in a git repo — skipping project setup."
  info "Run 'git init' and then '/collab setup' inside Claude Code to set up a project."
fi

# ── Agent setup note ────────────────────────────────────────────────────────
echo ""
bold "Agent Runtime Setup"
info "AI Collab is agent-first: choose agents, then record their IDE/container and LLM model."
echo ""
info "  Claude Code      → CLAUDE.md"
info "  OpenCode         → .opencode/rules/ai-collab.md + AGENTS.md"
info "  Codex            → AGENTS.md"
info "  Cursor native    → .cursorrules"
info "  Windsurf native  → .windsurfrules"
info "  Copilot Chat     → .github/copilot-instructions.md"
echo ""
info "Run this any time inside a project:"
info "  python3 ~/.claude/ai-collab-setup.py"

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
echo "    🧭 Auto-onboard        — registers new agents after their first log"
echo "    🧩 Unified setup       — updates globals, migrates the project, verifies health"
echo "    🎛️  Run orchestrator    — director-selected multi-agent implementation runs"
echo "    💬 Conversation helper — natural agent questions, proposals, decisions"
echo "    👁️  Live observer       — writes .ai-collab/live semantic state snapshots"
echo "    🔎 OCR engine          — powers screenshot text reading when available"
echo "    🩺 Doctor script       — verifies install health"
echo "    🧭 Reboot recovery     — restores CONTEXT.md + wakeup retries after restart"
echo "    📚 /collab skill       — collaboration commands available in Claude Code"
echo ""
echo "  Try it now — open Claude Code and type:"
echo "    /collab setup          — install/update globally and migrate this project"
echo "    /collab read           — see what other AIs have been working on"
echo "    /collab write          — save your current context"
echo "    /collab assign codex [task]  — send a task to another AI"
echo "    /collab orchestrate    — plan and run a directed multi-agent implementation"
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
