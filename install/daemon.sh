#!/bin/bash
# AI Collab Daemon — watches all .ai-collab/ directories and writes pending notifications
# Managed by launchd — survives sleep, session close, and restarts

NOTIFICATIONS_FILE="$HOME/.ai-collab-notifications.json"
LAST_CHECK_FILE="$HOME/.ai-collab-last-check"
LOG_FILE="/tmp/ai-collab-daemon.log"
WAKEUP_SCRIPT="$HOME/.claude/ai-collab-wakeup.py"
AUTO_ONBOARD_SCRIPT="$HOME/.claude/ai-collab-auto-onboard.py"
OBSERVER_SCRIPT="$HOME/.claude/ai-collab-observer.py"
UPDATE_SCRIPT="$HOME/.claude/ai-collab-update.py"
RECOVERY_SCRIPT="$HOME/.claude/ai-collab-recover.py"
LAST_UPDATE_FILE="$HOME/.ai-collab-last-update"
LAST_RECOVERY_FILE="$HOME/.ai-collab-last-recovery"
UPDATE_INTERVAL_SECONDS="${AI_COLLAB_UPDATE_INTERVAL_SECONDS:-21600}"
RECOVERY_INTERVAL_SECONDS="${AI_COLLAB_RECOVERY_INTERVAL_SECONDS:-300}"
MAX_NOTIFICATIONS=50

log() { echo "[AI-COLLAB] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" >> "$LOG_FILE"; }

# Fix #3 (OpenCode) — trap crashes and log them
trap 'log "CRASH: daemon exited unexpectedly (exit $?)"' EXIT

log "Daemon started (PID $$)"

# Initialize files
[ -f "$NOTIFICATIONS_FILE" ] || echo "[]" > "$NOTIFICATIONS_FILE"
[ -f "$LAST_CHECK_FILE" ]    || date +%s > "$LAST_CHECK_FILE"
[ -f "$LAST_UPDATE_FILE" ]   || echo 0 > "$LAST_UPDATE_FILE"
[ -f "$LAST_RECOVERY_FILE" ] || echo 0 > "$LAST_RECOVERY_FILE"

# Fix #4 — detect stat command (macOS vs Linux)
if stat -f "%m" /dev/null 2>/dev/null; then
    STAT_MOD() { stat -f "%m" "$1" 2>/dev/null; }
else
    STAT_MOD() { stat -c "%Y" "$1" 2>/dev/null; }
fi

while true; do
  sleep 15

  LAST_CHECK=$(cat "$LAST_CHECK_FILE" 2>/dev/null || date +%s)
  LAST_UPDATE=$(cat "$LAST_UPDATE_FILE" 2>/dev/null || echo 0)
  LAST_RECOVERY=$(cat "$LAST_RECOVERY_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)

  # Self-update — refreshes ~/.claude install files and managed project rule
  # blocks. Disabled with AI_COLLAB_AUTO_UPDATE=0; interval defaults to 6h.
  if [ -x "$UPDATE_SCRIPT" ] && [ "${AI_COLLAB_AUTO_UPDATE:-1}" != "0" ]; then
    if [ $((NOW - LAST_UPDATE)) -ge "${UPDATE_INTERVAL_SECONDS:-21600}" ]; then
      if python3 "$UPDATE_SCRIPT" >/tmp/ai-collab-update.log 2>>"$LOG_FILE"; then
        log "Self-update completed"
      else
        log "Warning: self-update failed; see /tmp/ai-collab-update.log"
      fi
      echo "$NOW" > "$LAST_UPDATE_FILE"
    fi
  fi

  # Reboot/session recovery — refreshes project CONTEXT.md files and clears
  # stale wakeup dedupe entries for unfinished inbox tasks. This keeps agents
  # oriented after sleep, crash, logout, or machine reboot.
  if [ -x "$RECOVERY_SCRIPT" ] && [ "${AI_COLLAB_RECOVERY:-1}" != "0" ]; then
    if [ $((NOW - LAST_RECOVERY)) -ge "${RECOVERY_INTERVAL_SECONDS:-300}" ]; then
      if python3 "$RECOVERY_SCRIPT" >/tmp/ai-collab-recover.log 2>>"$LOG_FILE"; then
        log "Recovery completed"
      else
        log "Warning: recovery failed; see /tmp/ai-collab-recover.log"
      fi
      echo "$NOW" > "$LAST_RECOVERY_FILE"
    fi
  fi

  # Discover once per tick and skip trees that cannot contain user projects.
  # Wakeups run for every project before slower observer/screenshot work so a
  # busy project cannot delay conversations in another one.
  COLLAB_DIRS=()
  while IFS= read -r -d '' COLLAB_DIR; do
    COLLAB_DIRS+=("$COLLAB_DIR")
  done < <(
    find "$HOME" -maxdepth 6 \
      \( -type d \( -name .git -o -name node_modules -o -name Library -o -name .Trash -o -name .cache -o -name .npm \) -prune \) \
      -o -type d -name ".ai-collab" -print0 2>/dev/null
  )

  for COLLAB_DIR in "${COLLAB_DIRS[@]}"; do
    PROJECT=$(basename "$(dirname "$COLLAB_DIR")")

    if [ -x "$WAKEUP_SCRIPT" ]; then
      for inbox in "$COLLAB_DIR"/inbox-*.md; do
        [ -f "$inbox" ] || continue
        python3 "$WAKEUP_SCRIPT" "$PROJECT" "$inbox" >/dev/null 2>>"$LOG_FILE" || log "Warning: wakeup scan failed for $inbox"
      done
      for thread in "$COLLAB_DIR"/thread-*.md "$COLLAB_DIR"/discussions/*.md; do
        [ -f "$thread" ] || continue
        python3 "$WAKEUP_SCRIPT" "$PROJECT" "$thread" >/dev/null 2>>"$LOG_FILE" || log "Warning: thread wakeup scan failed for $thread"
      done
      # Daemon safety-net: dispatch review wakes for agents that completed
      # non-trivial work but did not fire a review request themselves.
      python3 "$WAKEUP_SCRIPT" --scan-reviews "$COLLAB_DIR/.." >/dev/null 2>>"$LOG_FILE" || log "Warning: review safety-net scan failed for $PROJECT"
    fi
  done

  for COLLAB_DIR in "${COLLAB_DIRS[@]}"; do
    PROJECT=$(basename "$(dirname "$COLLAB_DIR")")

    # Live observer — writes project-local .ai-collab/live/*.json snapshots,
    # health.json, screenshot semantic sidecars, process hints, git dirtiness,
    # stale-claim alerts, and project-scoped automatic screenshots. It can be
    # disabled with AI_COLLAB_OBSERVER=0.
    if [ -x "$OBSERVER_SCRIPT" ] && [ "${AI_COLLAB_OBSERVER:-1}" != "0" ]; then
      python3 "$OBSERVER_SCRIPT" "$COLLAB_DIR" >/dev/null 2>>"$LOG_FILE" || log "Warning: observer scan failed for $COLLAB_DIR"
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
        # Auto-onboard newly arriving agents from their first session log.
        # The helper is idempotent and owns rules/TEAM.md merging.
        if [ -x "$AUTO_ONBOARD_SCRIPT" ]; then
          python3 "$AUTO_ONBOARD_SCRIPT" "$PROJECT" "$f" >>"$LOG_FILE" 2>>"$LOG_FILE" || log "Warning: auto-onboard failed for $BASENAME"
        fi

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

  done

  echo "$NOW" > "$LAST_CHECK_FILE"
done

log "Daemon loop exited"
