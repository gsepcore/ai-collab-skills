# Codex / Antigravity Bridge Contract

This bridge is a localhost API facade for addressing Codex from other agents.
It does not claim private access to the visible Codex panel. It provides a
stable contract that can route to the best available backend today and can be
swapped to a real Antigravity/Codex API later.

## Start the bridge

```bash
python3 ~/.claude/ai-collab-codex-bridge.py serve --host 127.0.0.1 --port 8765
```

Optional auth:

```bash
AI_COLLAB_CODEX_BRIDGE_TOKEN=secret \
python3 ~/.claude/ai-collab-codex-bridge.py serve
```

Clients then send `Authorization: Bearer secret`.

## Health

```http
GET /health
```

Returns supported modes and the current visible-session status.

## Wake Codex

```http
POST /v1/codex/message
Content-Type: application/json
```

Body:

```json
{
  "project_path": "/path/to/project",
  "from_agent": "opencode",
  "topic": "Need Codex decision",
  "message": "@codex please review the latest proposal",
  "mode": "background"
}
```

Modes:

- `background`: maps to `codex-acp`; launches an autonomous Codex worker.
- `visible`: maps to `antigravity-chat`; best-effort visible Antigravity chat.
- `auto`: uses the default visible wakeup routing.
- `notify-only`: records the wake event without executing an adapter.

The bridge writes a normal `.ai-collab/discussions/*.md` thread first, then
invokes `ai-collab-wakeup.py` so the rest of the protocol stays observable.

## CLI equivalent

```bash
python3 ~/.claude/ai-collab-codex-bridge.py send \
  --project /path/to/project \
  --from-agent opencode \
  --topic "Need Codex decision" \
  --message "@codex please review the latest proposal" \
  --mode background
```

## Visibility guarantee

`background` can be autonomous but is not the user's current visible Codex tab.
`visible` can try Antigravity chat, but exact visible-tab injection remains
degraded until Antigravity/Codex exposes a supported inbound prompt API.
