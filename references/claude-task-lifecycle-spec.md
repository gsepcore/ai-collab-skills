---
ai: Claude Code (with Señor LUIS)
session: 20260512-task-lifecycle
project: gsep / ai-collab-skills
updated: 2026-05-12T12:30:00Z
status: v1-for-team-review
---

# Task Lifecycle Spec — Director Semantics

Companion to `PLAN-ai-collab-wakeup.md` v2. Formalizes the state machine and the rules that govern transitions, so daemons, adapters, and directors don't accidentally overwrite each other.

## 1. States

| State     | Meaning                                                                 |
|-----------|-------------------------------------------------------------------------|
| `unread`  | Task is available. No agent has picked it up.                           |
| `claimed` | An agent has reserved the task and is preparing to execute.             |
| `running` | The agent has actively started executing the task.                      |
| `blocked` | The agent cannot proceed without human or director decision.            |
| `done`    | The agent completed the task and wrote the outcome.                     |
| `failed`  | The task failed permanently (max retries hit, or non-recoverable error).|

## 2. Required frontmatter

```yaml
---
from: <author slug>
to: <target slug>
task_id: YYYYMMDD-HHMMSS-<slug>-<short-description>
priority: low | normal | high | critical
updated: <ISO-8601 UTC>
status: unread | claimed | running | blocked | done | failed
attempts: <int, default 0>
last_attempt: <ISO-8601 UTC or empty>
claimed_by: <slug or empty>
claimed_at: <ISO-8601 UTC or empty>
done_at: <ISO-8601 UTC or empty>
---
```

`task_id` is the durable identifier. Once written, it never changes. All other fields are mutable under the rules below.

## 3. Transitions (state machine)

```
                ┌─────────────────────────────────────────────────┐
                │                                                 │
                ▼                                                 │
[init] ──► unread ──claim──► claimed ──start──► running ──ok──► done
              │                  │                  │
              │                  │                  ├──block──► blocked ──unblock──► unread
              │                  │                  │
              │                  │                  └──fail (retries left)──► unread
              │                  │
              │                  └──timeout/abandon──► unread
              │
              └──fail (retries exhausted, any state)──► failed
```

### 3.1 Allowed transitions

| From      | To        | Who                                       | Rule                                                                                  |
|-----------|-----------|-------------------------------------------|---------------------------------------------------------------------------------------|
| `unread`  | `claimed` | Target agent (or daemon on its behalf)    | Must set `claimed_by` and `claimed_at` atomically.                                    |
| `claimed` | `running` | Same agent that claimed                   | Must increment `attempts` and set `last_attempt`.                                     |
| `running` | `done`    | Same agent                                | Must set `done_at`. Outcome is logged separately.                                     |
| `running` | `blocked` | Same agent                                | Must append blocker reason in a `thread-{task_id}.md` (post-MVP) or body annotation.  |
| `running` | `unread`  | Same agent or daemon (transient failure)  | Allowed only if `attempts < MAX_ATTEMPTS`. Else go to `failed`.                       |
| `blocked` | `unread`  | Director                                  | Allowed only if director provides a new `updated` timestamp and reason.               |
| `claimed` | `unread`  | Daemon (stale claim timeout)              | Allowed after `STALE_CLAIM_TIMEOUT` (default 30 min). Logs requeue reason.            |
| any       | `failed`  | Daemon                                    | Allowed when `attempts >= MAX_ATTEMPTS` (default 3) and no successful transition.     |

### 3.2 Forbidden transitions

- `done` → anything. Done is terminal. A new task needs a new `task_id`.
- `failed` → anything except via director-issued new task (new `task_id`, references old).
- Skipping states (e.g. `unread` → `running` without `claimed`) is forbidden — the claim step is the lock.
- Overwriting `claimed_by` when state is `claimed` or `running` by a different agent is a conflict (see §5).

## 4. Director semantics

The director (Claude by default, but any agent can be designated) has elevated permissions:

### 4.1 Director can

- **Reassign a `claimed` task** if the original claimer has been silent past `STALE_CLAIM_TIMEOUT`. Director writes a `thread-{task_id}.md` note explaining the reassignment, resets state to `unread`, clears `claimed_by` / `claimed_at`, increments `attempts` does NOT reset.
- **Move `blocked` back to `unread`** when the blocker is resolved. Director must add a `reason` in the task body or thread file.
- **Mark `claimed` or `running` as `failed`** when the agent reports it cannot recover and there are no retries left.
- **Create new tasks** for any slug. Only the director can create cross-agent tasks; agents themselves can only create tasks targeting themselves or the director.
- **Override priority** to escalate or de-escalate.

### 4.2 Director cannot

- Overwrite a `running` task that is making progress (logs being updated, mtime fresh). Wait for stale timeout instead.
- Mutate `task_id` after creation.
- Move a `done` task back to any other state. Issue a new task that references the old one.
- Move a `failed` task back to `unread` without issuing a new `task_id`. Failed is terminal for that ID.

### 4.3 Director identity

The director slug is declared in `.ai-collab/TEAM.md` under a `director:` key (default: `claude`). If no director is declared, the daemon refuses cross-agent reassignment and logs a warning.

## 5. Conflict resolution

Two agents racing to claim the same `unread` task:

1. The atomic write of `status: claimed` + `claimed_by` + `claimed_at` is the lock. The daemon uses `fcntl.flock` (or platform equivalent) on the inbox file during the write.
2. The second writer reads the current state before writing. If state is no longer `unread`, the write is aborted and the second agent logs a "lost-race" event.
3. If both writes somehow land (lock failure), the agent with the **earlier `claimed_at`** wins. The other agent rolls back: re-reads inbox, sees it lost, releases.
4. If `claimed_at` is identical to the second, the lexicographically smaller slug wins. This is deterministic and tie-free.

The daemon validates after every write that there's exactly one `claimed_by` and one `claimed_at`. If it sees corruption, it logs to `/tmp/ai-collab-wakeup.log` and notifies the director.

## 6. Retry & failure policy

Aligned with `PLAN-ai-collab-wakeup.md` v2 §3.2:

- `MAX_ATTEMPTS = 3` (env: `AI_COLLAB_WAKEUP_MAX_ATTEMPTS`).
- Exponential backoff: `5s, 25s, 125s` (env: `AI_COLLAB_WAKEUP_BACKOFF_BASE`, `AI_COLLAB_WAKEUP_BACKOFF_FACTOR`).
- Every wake attempt: `attempts++`, set `last_attempt = now()`, log to `/tmp/ai-collab-wakeup.log`.
- After `MAX_ATTEMPTS` with no `claimed`: daemon writes `status: failed` and logs reason `wakeup-exhausted`.
- After `running` → `running` stall (no log update for `STALE_RUNNING_TIMEOUT`, default 15 min): daemon escalates by writing to director's inbox.

## 7. Stale-claim timeout

- `STALE_CLAIM_TIMEOUT` default: 30 min (env: `AI_COLLAB_STALE_CLAIM_TIMEOUT_SEC`).
- Trigger: state is `claimed` or `running` and `last_attempt + STALE_CLAIM_TIMEOUT < now()` AND no log file from `claimed_by` has been touched in that window.
- Action: daemon resets to `unread`, clears claim fields, increments a separate `requeue_count` (not `attempts`), writes a thread note `requeued-stale-claim`.
- If `requeue_count >= 2`, escalate to director inbox instead of auto-requeue.

## 8. Audit trail

Every state transition writes a line to:

```
/tmp/ai-collab-wakeup.log
```

Format:

```
<ISO-8601> <task_id> <from-state> -> <to-state> by=<slug> reason=<short>
```

For Phase F (post-MVP), the same audit goes into `.ai-collab/thread-{task_id}.md` for human-readable history.

## 9. Director assignment via `/collab assign`

When Claude (or any director) runs `/collab assign <slug> <task>`, the skill MUST emit a frontmatter that conforms to §2 with:

- Fresh `task_id` generated from current timestamp + slug + slug-of-task.
- `status: unread`, `attempts: 0`, all claim/done fields empty.
- `from:` set to director identity.
- `to:` set to target slug.
- `updated:` set to creation time.

The skill MUST NOT overwrite an existing inbox file. If `inbox-<slug>.md` already exists and its current `status` is not `done` or `failed`, the skill warns the director and asks before overwriting.

Once Phase B daemon support is live, the skill SHOULD also append the new task to a per-project task log (`.ai-collab/tasks.jsonl`) for fast scanning without parsing every inbox.

## 10. Open items (not blocking MVP)

- Whether `thread-{task_id}.md` should be created at task-creation time or lazily on first comment. (Decide in Phase F.)
- Whether `requeue_count` should be visible in frontmatter or only in the audit log. (Lean: frontmatter, for visibility.)
- Whether the daemon should support a `paused` state for tasks the director wants to keep visible but not auto-wake. (Likely yes; defer to Phase E.)

## 11. Compliance checklist for adapter implementers

Any wakeup adapter MUST:

- Read inbox state atomically before acting.
- Refuse to wake a task already in `running`, `done`, or `failed`.
- Increment `attempts` and set `last_attempt` on every wake call, success or fail.
- Honor the lock (fcntl.flock or equivalent) when writing.
- Log every wake to `/tmp/ai-collab-wakeup.log`.
- Return `{status, message, adapter_name}` matching the adapter contract.

Adapter MUST NOT:

- Mutate fields other than `status`, `attempts`, `last_attempt`, `claimed_by`, `claimed_at`, `done_at`.
- Wake a task whose `to:` does not match the adapter's target slug.
- Retry locally — retries are the daemon's job.

---

**Owner:** Claude Code
**Reviewers:** Cody (Codex), Thomas (OpenCode)
**Status:** v1-for-team-review. After Cody and Thomas sign off, this gets folded into Phase B exit criteria.
