#!/bin/bash
# AI Collab Daemon — watches all .ai-collab/ directories and writes pending notifications
# Managed by launchd — survives sleep, session close, and restarts

NOTIFICATIONS_FILE="$HOME/.ai-collab-notifications.json"
LAST_CHECK_FILE="$HOME/.ai-collab-last-check"
LOG_FILE="/tmp/ai-collab-daemon.log"
MAX_NOTIFICATIONS=50

log() { echo "[AI-COLLAB] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" >> "$LOG_FILE"; }

# Fix #3 (OpenCode) — trap crashes and log them
trap 'log "CRASH: daemon exited unexpectedly (exit $?)"' EXIT

log "Daemon started (PID $$)"

# Initialize files
[ -f "$NOTIFICATIONS_FILE" ] || echo "[]" > "$NOTIFICATIONS_FILE"
[ -f "$LAST_CHECK_FILE" ]    || date +%s > "$LAST_CHECK_FILE"

# Fix #4 — detect stat command (macOS vs Linux)
if stat -f "%m" /dev/null 2>/dev/null; then
    STAT_MOD() { stat -f "%m" "$1" 2>/dev/null; }
else
    STAT_MOD() { stat -c "%Y" "$1" 2>/dev/null; }
fi

while true; do
  sleep 15

  LAST_CHECK=$(cat "$LAST_CHECK_FILE" 2>/dev/null || date +%s)
  NOW=$(date +%s)

  # Fix #3 — increased maxdepth from 4 to 6 for deeper project structures
  while IFS= read -r -d '' COLLAB_DIR; do
    PROJECT=$(basename "$(dirname "$COLLAB_DIR")")

    for f in "$COLLAB_DIR"/*.md; do
      [ -f "$f" ] || continue
      BASENAME=$(basename "$f")
      [[ "$BASENAME" == claude-* ]] && continue
      [[ "$BASENAME" == PROTOCOL.md ]] && continue
      [[ "$BASENAME" == CONTEXT.md ]] && continue

      MOD=$(STAT_MOD "$f") || continue

      if [ "$MOD" -gt "$LAST_CHECK" ]; then
        AI=$(grep "^ai:" "$f" 2>/dev/null | head -1 | cut -d' ' -f2- | tr -d '\r\n' | sed 's/["\]/\\&/g')
        WORKING=$(grep -A2 "^## Working On" "$f" 2>/dev/null | grep -v "^## " | head -1 | tr -d '\r\n' | cut -c1-120 | sed 's/["\]/\\&/g')
        TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

        # Fix #8 — skip malformed logs with empty AI name
        [ -z "$AI" ] && log "Warning: skipped $BASENAME — missing 'ai:' frontmatter field" && continue

        # Fix #1 + #6 — atomic write via temp file + os.replace() prevents race conditions and data loss
        python3 - "$NOTIFICATIONS_FILE" "$MAX_NOTIFICATIONS" "$AI" "$BASENAME" "$PROJECT" "$WORKING" "$TIMESTAMP" << 'PYEOF'
import json, sys, os

notifications_file, max_n, ai, fname, project, working, timestamp = sys.argv[1:]
max_n = int(max_n)

for attempt in range(3):
    try:
        try:
            with open(notifications_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = []
        except json.JSONDecodeError as e:
            print(f'[AI-COLLAB] Warning: notifications file corrupted ({e}), resetting', file=sys.stderr)
            data = []

        data.append({
            'ai': ai, 'file': fname, 'project': project,
            'working': working, 'timestamp': timestamp
        })

        # Fix #7 — cap size to prevent unbounded growth
        if len(data) > max_n:
            data = data[-max_n:]

        # Atomic write: write to temp, then rename (os.replace is atomic on POSIX)
        tmp = notifications_file + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, notifications_file)
        break
    except IOError as e:
        import time
        if attempt < 2:
            time.sleep(0.1 * (attempt + 1))
        else:
            print(f'[AI-COLLAB] Error writing notification: {e}', file=sys.stderr)
PYEOF

        # Fix #5 — run osascript in background so it never blocks the daemon
        osascript -e "display notification \"$WORKING\" with title \"AI Collab — $AI\" subtitle \"Proyecto: $PROJECT\" sound name \"Tink\"" 2>/dev/null &

      fi
    done

  done < <(find "$HOME" -maxdepth 6 -type d -name ".ai-collab" -print0 2>/dev/null)

  echo "$NOW" > "$LAST_CHECK_FILE"
done

log "Daemon loop exited"
