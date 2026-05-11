#!/bin/bash
# AI Collab Skill — Uninstaller
# Removes daemon, hooks support files, and skill from Claude Code.
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
echo "  - ~/.claude/ai-collab-icon.png"
echo "  - ~/.ai-collab-notifications.json"
echo "  - ~/.ai-collab-last-check"
echo "  - ~/.claude/skills/collab/"
echo ""
echo "NOT removed: .ai-collab/ directories in your projects (your logs stay safe)"
echo ""
read -r -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

echo ""

# Stop and unload launchd daemon (macOS only)
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.gsepcore.ai-collab.plist"
    if [ -f "$PLIST" ]; then
        launchctl unload "$PLIST" 2>/dev/null && echo "✓ launchd daemon stopped"
        rm -f "$PLIST" && echo "✓ Removed $PLIST"
    fi
fi

# Remove daemon and support scripts
[ -f "$HOME/.claude/ai-collab-daemon.sh" ]  && rm -f "$HOME/.claude/ai-collab-daemon.sh"  && echo "✓ Removed ai-collab-daemon.sh"
[ -f "$HOME/.claude/ai-collab-summary.py" ] && rm -f "$HOME/.claude/ai-collab-summary.py" && echo "✓ Removed ai-collab-summary.py"
[ -f "$HOME/.claude/ai-collab-icon.png" ]   && rm -f "$HOME/.claude/ai-collab-icon.png"   && echo "✓ Removed ai-collab-icon.png"

# Remove notification queue files
[ -f "$HOME/.ai-collab-notifications.json" ] && rm -f "$HOME/.ai-collab-notifications.json" && echo "✓ Removed notifications queue"
[ -f "$HOME/.ai-collab-last-check" ]         && rm -f "$HOME/.ai-collab-last-check"         && echo "✓ Removed last-check file"

# Remove skill from Claude Code
SKILL_DIR="$HOME/.claude/skills/collab"
if [ -d "$SKILL_DIR" ]; then
    rm -rf "$SKILL_DIR" && echo "✓ Removed Claude Code skill (~/.claude/skills/collab/)"
fi

echo ""
echo "✅ AI Collab Skill uninstalled."
echo ""
echo "To remove from a specific project:"
echo "  rm -rf {project-root}/.ai-collab/"
echo "  # Also remove .ai-collab/ from .gitignore"
echo "  # Also remove the AI Collab Protocol block from .cursorrules / .windsurfrules"
echo ""
