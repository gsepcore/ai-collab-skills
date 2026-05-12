#!/bin/bash
# AI Collab Daemon — watches all .ai-collab/ directories and writes pending notifications
# Managed by launchd — survives sleep, session close, and restarts

NOTIFICATIONS_FILE="$HOME/.ai-collab-notifications.json"
LAST_CHECK_FILE="$HOME/.ai-collab-last-check"
LOG_FILE="/tmp/ai-collab-daemon.log"
WAKEUP_SCRIPT="$HOME/.claude/ai-collab-wakeup.py"
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

    # Phase B — scan inboxes separately from normal log notifications.
    # The Python helper owns frontmatter parsing, retry/backoff, event writes,
    # and failed-state transitions. This keeps the bash daemon small.
    if [ -x "$WAKEUP_SCRIPT" ]; then
      for inbox in "$COLLAB_DIR"/inbox-*.md; do
        [ -f "$inbox" ] || continue
        python3 "$WAKEUP_SCRIPT" "$PROJECT" "$inbox" >/dev/null 2>>"$LOG_FILE" || log "Warning: wakeup scan failed for $inbox"
      done
      for thread in "$COLLAB_DIR"/thread-*.md; do
        [ -f "$thread" ] || continue
        python3 "$WAKEUP_SCRIPT" "$PROJECT" "$thread" >/dev/null 2>>"$LOG_FILE" || log "Warning: thread wakeup scan failed for $thread"
      done
    fi

    for f in "$COLLAB_DIR"/*.md; do
      [ -f "$f" ] || continue
      BASENAME=$(basename "$f")
      [[ "$BASENAME" == claude-* ]] && continue
      [[ "$BASENAME" == PROTOCOL.md ]] && continue
      [[ "$BASENAME" == CONTEXT.md ]] && continue
      [[ "$BASENAME" == TEAM.md ]] && continue
      [[ "$BASENAME" == inbox-* ]] && continue

      MOD=$(STAT_MOD "$f") || continue

      if [ "$MOD" -gt "$LAST_CHECK" ]; then
        # Capture RAW values for the macOS notification (which uses AppleScript, NOT Python escapes)
        AI_RAW=$(grep "^ai:" "$f" 2>/dev/null | head -1 | cut -d' ' -f2- | tr -d '\r\n')
        WORKING_RAW=$(grep -A2 "^## Working On" "$f" 2>/dev/null | grep -v "^## " | head -1 | tr -d '\r\n' | cut -c1-120)
        # Apply Python-safe escaping for the notifications.json write
        AI=$(printf '%s' "$AI_RAW" | sed 's/["\]/\\&/g')
        WORKING=$(printf '%s' "$WORKING_RAW" | sed 's/["\]/\\&/g')
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

        # macOS Notification Center — opt-in via AI_COLLAB_OS_NOTIFY=1
        # Sound is separately opt-in via AI_COLLAB_OS_NOTIFY_SOUND=<name> (e.g. "Tink", "Glass", "Pop")
        if [ "$AI_COLLAB_OS_NOTIFY" = "1" ] && [[ "$OSTYPE" == "darwin"* ]]; then
          # Sanitize for AppleScript: strip quotes/backslashes, collapse whitespace, cap length
          OSC_AI=$(printf '%s' "$AI_RAW" | tr -d '"\\' | tr -s '[:space:]' ' ' | cut -c1-60)
          OSC_PROJECT=$(printf '%s' "$PROJECT" | tr -d '"\\' | tr -s '[:space:]' ' ' | cut -c1-60)
          OSC_WORKING=$(printf '%s' "$WORKING_RAW" | tr -d '"\\' | tr -s '[:space:]' ' ' | cut -c1-200)
          [ -z "$OSC_WORKING" ] && OSC_WORKING="updated $BASENAME"
          OSC_SOUND=""
          if [ -n "$AI_COLLAB_OS_NOTIFY_SOUND" ]; then
            OSC_SOUND_NAME=$(printf '%s' "$AI_COLLAB_OS_NOTIFY_SOUND" | tr -d '"\\' | cut -c1-30)
            OSC_SOUND=" sound name \"$OSC_SOUND_NAME\""
          fi
          # Run in background subshell so it never blocks the daemon loop
          ( osascript -e "display notification \"$OSC_WORKING\" with title \"AI Collab — $OSC_PROJECT\" subtitle \"$OSC_AI\"$OSC_SOUND" 2>/dev/null ) &
        fi

      fi
    done

  done < <(find "$HOME" -maxdepth 6 -type d -name ".ai-collab" -print0 2>/dev/null)

  echo "$NOW" > "$LAST_CHECK_FILE"
done

log "Daemon loop exited"
