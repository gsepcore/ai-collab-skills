#!/bin/bash
# AI Collab Skill — Uninstaller
# Removes everything install.sh put in place.
# Does NOT delete .ai-collab/ directories in your projects.

set -e

echo ""
echo "AI Collab Skill — Uninstaller"
echo "=============================="
echo ""
echo "This will remove:"
echo "  - launchd daemon (com.gsepcore.ai-collab)"
echo "  - ~/.claude/ai-collab-daemon.sh"
echo "  - ~/.claude/ai-collab-summary.py"
echo "  - ~/.claude/ai-collab-check-notifications.py"
echo "  - ~/.claude/ai-collab-wakeup.py"
echo "  - ~/.claude/ai-collab-doctor.py"
echo "  - ~/.ai-collab-notifications.json"
echo "  - ~/.ai-collab-last-check"
echo "  - ~/.ai-collab-wakeup-events.json"
echo "  - ~/.ai-collab-wakeup-state.json"
echo "  - ~/.claude/skills/collab/"
echo "  - AI-collab hooks from ~/.claude/settings.json"
echo ""
echo "NOT removed: .ai-collab/ directories in your projects (your logs stay safe)"
echo ""
read -r -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

echo ""

# Stop and unload launchd daemon (macOS)
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.gsepcore.ai-collab.plist"
    if [ -f "$PLIST" ]; then
        launchctl unload "$PLIST" 2>/dev/null && echo "✓ launchd daemon stopped"
        rm -f "$PLIST" && echo "✓ Removed $PLIST"
    fi
fi

# Remove cron job (Linux)
if command -v crontab &>/dev/null; then
    ( crontab -l 2>/dev/null | grep -v "ai-collab-daemon" ) | crontab - 2>/dev/null && true
    echo "✓ Removed cron job (if any)"
fi

# Remove daemon and support scripts
[ -f "$HOME/.claude/ai-collab-daemon.sh" ]               && rm -f "$HOME/.claude/ai-collab-daemon.sh"               && echo "✓ Removed ai-collab-daemon.sh"
[ -f "$HOME/.claude/ai-collab-summary.py" ]              && rm -f "$HOME/.claude/ai-collab-summary.py"              && echo "✓ Removed ai-collab-summary.py"
[ -f "$HOME/.claude/ai-collab-check-notifications.py" ]  && rm -f "$HOME/.claude/ai-collab-check-notifications.py"  && echo "✓ Removed ai-collab-check-notifications.py"
[ -f "$HOME/.claude/ai-collab-wakeup.py" ]               && rm -f "$HOME/.claude/ai-collab-wakeup.py"               && echo "✓ Removed ai-collab-wakeup.py"
[ -f "$HOME/.claude/ai-collab-doctor.py" ]               && rm -f "$HOME/.claude/ai-collab-doctor.py"               && echo "✓ Removed ai-collab-doctor.py"

# Remove notification queue files
[ -f "$HOME/.ai-collab-notifications.json" ] && rm -f "$HOME/.ai-collab-notifications.json" && echo "✓ Removed notifications queue"
[ -f "$HOME/.ai-collab-last-check" ]         && rm -f "$HOME/.ai-collab-last-check"         && echo "✓ Removed last-check file"
[ -f "$HOME/.ai-collab-wakeup-events.json" ] && rm -f "$HOME/.ai-collab-wakeup-events.json" && echo "✓ Removed wakeup events queue"
[ -f "$HOME/.ai-collab-wakeup-state.json" ]  && rm -f "$HOME/.ai-collab-wakeup-state.json"  && echo "✓ Removed wakeup state file"

# Remove skill from Claude Code
SKILL_DIR="$HOME/.claude/skills/collab"
if [ -d "$SKILL_DIR" ]; then
    rm -rf "$SKILL_DIR" && echo "✓ Removed Claude Code skill (~/.claude/skills/collab/)"
fi

# Remove AI-collab hooks from ~/.claude/settings.json
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" << 'PYEOF'
import json, sys, os

settings_file = sys.argv[1]

try:
    with open(settings_file) as f:
        settings = json.load(f)
except Exception:
    print("  Could not read settings.json — skipping hook removal.")
    sys.exit(0)

hooks = settings.get("hooks", {})
changed = False

for event in list(hooks.keys()):
    new_list = []
    for entry in hooks[event]:
        new_hooks = [
            h for h in entry.get("hooks", [])
            if "ai-collab" not in h.get("command", "")
        ]
        if new_hooks:
            new_list.append({**entry, "hooks": new_hooks})
        else:
            changed = True  # removed an entry
    if new_list:
        hooks[event] = new_list
    else:
        del hooks[event]
        changed = True

if changed:
    settings["hooks"] = hooks
    tmp = settings_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_file)
    print("✓ Removed AI-collab hooks from ~/.claude/settings.json")
else:
    print("  No AI-collab hooks found in settings.json")
PYEOF
fi

echo ""
echo "✅ AI Collab Skill uninstalled."
echo ""
echo "To clean up a specific project:"
echo "  rm -rf {project}/.ai-collab/"
echo "  # Remove .ai-collab/ from .gitignore"
echo "  # Remove AI Collab Protocol block from .cursorrules / .windsurfrules"
echo ""
