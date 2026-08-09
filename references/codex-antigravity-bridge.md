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

- `background`: maps to `codex-auto`; tries `codex-acp` first, then a real
  non-interactive `codex exec` worker, then falls back to a degraded
  `codex-filesystem` receipt so delivery is still visible in `.ai-collab`
  without claiming a real Codex turn.
- `visible`: maps to `antigravity-chat`; uses the supported
  `antigravity-ide chat --reuse-window` CLI and fails closed when unavailable.
- `auto`: uses the default visible wakeup routing.
- `codex-filesystem`: writes a Codex wake receipt, live state, and session log
  without claiming control of the visible Codex panel or an LLM turn.
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

`background` can be autonomous when ACP or `codex exec` works. If both are
unavailable, the filesystem fallback records a degraded receipt by writing
Codex artifacts, but it is not an LLM turn and is not the user's current visible
Codex tab.
`visible` submits through the supported Antigravity IDE chat CLI. CLI exit zero
proves visible submission, not a Codex response; require Codex's own authored
thread/chat message before advancing the state to `responded`.
