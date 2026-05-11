#!/bin/bash
# AI Collab Daemon — watches all .ai-collab/ directories and writes pending notifications
# Managed by launchd — survives sleep, session close, and restarts

NOTIFICATIONS_FILE="$HOME/.ai-collab-notifications.json"
LAST_CHECK_FILE="$HOME/.ai-collab-last-check"

# Initialize files
[ -f "$NOTIFICATIONS_FILE" ] || echo "[]" > "$NOTIFICATIONS_FILE"
[ -f "$LAST_CHECK_FILE" ] || date +%s > "$LAST_CHECK_FILE"

while true; do
  sleep 15

  LAST_CHECK=$(cat "$LAST_CHECK_FILE" 2>/dev/null || date +%s)
  NOW=$(date +%s)

  # Find all .ai-collab directories under home (covers all projects)
  while IFS= read -r -d '' COLLAB_DIR; do
    PROJECT=$(basename "$(dirname "$COLLAB_DIR")")

    for f in "$COLLAB_DIR"/*.md; do
      [ -f "$f" ] || continue
      BASENAME=$(basename "$f")
      [[ "$BASENAME" == claude-* ]] && continue
      [[ "$BASENAME" == PROTOCOL.md ]] && continue

      MOD=$(stat -f "%m" "$f" 2>/dev/null) || continue

      if [ "$MOD" -gt "$LAST_CHECK" ]; then
        AI=$(grep "^ai:" "$f" 2>/dev/null | head -1 | cut -d' ' -f2- | tr -d '\r\n' | sed 's/["\]/\\&/g')
        WORKING=$(grep -A2 "^## Working On" "$f" 2>/dev/null | grep -v "^## " | head -1 | tr -d '\r\n' | cut -c1-120 | sed 's/["\]/\\&/g')
        TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

        # Append to notifications JSON using python3
        python3 -c "
import json, sys
try:
    with open('$NOTIFICATIONS_FILE', 'r') as f:
        data = json.load(f)
except:
    data = []
data.append({
    'ai': '$AI',
    'file': '$BASENAME',
    'project': '$PROJECT',
    'working': '$WORKING',
    'timestamp': '$TIMESTAMP'
})
with open('$NOTIFICATIONS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null
      fi
    done

  done < <(find "$HOME" -maxdepth 4 -type d -name ".ai-collab" -print0 2>/dev/null)

  # Update last check time
  echo "$NOW" > "$LAST_CHECK_FILE"
done
