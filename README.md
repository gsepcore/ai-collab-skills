# AI Collab Skill

**A Claude Code skill that lets multiple AI assistants work on the same project simultaneously — and actually see each other.**

When you're using Claude Code alongside Cursor, Windsurf, Codex, OpenCode, or any other AI tool, they're completely blind to each other. This skill creates a shared filesystem protocol so they can read and write context in real time — no external service, no API, just a `.ai-collab/` directory inside your project.

---

## How it works

Each AI writes a Markdown session log to `{project-root}/.ai-collab/`. Any AI with access to the project filesystem can read those logs. Claude manages its log via this skill. Other AIs participate via simple snippets added to their rules files (`.cursorrules`, `.windsurfrules`, etc.) — one-time setup per project.

```
your-project/
└── .ai-collab/
    ├── PROTOCOL.md                   ← shared protocol (auto-created by /collab setup)
    ├── claude-20260511-143022.md     ← Claude Code's log
    ├── cursor-20260511-141500.md     ← Cursor's log
    └── codex-20260511-141000.md      ← Codex's log
```

---

## Installation

### 1. Install the skill in Claude Code

```bash
mkdir -p ~/.claude/skills/collab/references
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/SKILL.md \
  -o ~/.claude/skills/collab/SKILL.md
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/references/protocol.md \
  -o ~/.claude/skills/collab/references/protocol.md
```

Or manually: copy `SKILL.md` to `~/.claude/skills/collab/SKILL.md` and `references/protocol.md` to `~/.claude/skills/collab/references/protocol.md`.

### 2. Set up your project

In Claude Code, inside your project:

```
/collab setup
```

This creates `.ai-collab/`, adds it to `.gitignore`, and generates the rules snippets for whatever other AI tools you confirm using.

### 3. Set up other AIs

Tell each other AI:

> *"Read `.ai-collab/PROTOCOL.md` in this project and write your session log following the protocol."*

Or add the ready-made snippets from `examples/` to their rules files (`.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`).

---

## Commands

| Command | What it does |
|---------|-------------|
| `/collab read` | Read all logs from other AIs — shows who's active, what they're working on, what files to avoid |
| `/collab write` | Save Claude's current context to `.ai-collab/` |
| `/collab status` | One-line status of every AI active on the project |
| `/collab setup` | First-time setup: creates directory, gitignore entry, rules snippets for other AI tools |
| `/collab clear` | Remove stale logs (>24h). Use `--all` to clear everything |

---

## Supported AI tools

| Tool | Setup file | Example |
|------|-----------|---------|
| **Cursor** | `.cursorrules` | `examples/cursorrules.example` |
| **Windsurf** | `.windsurfrules` | `examples/windsurfrules.example` |
| **GitHub Copilot** | `.github/copilot-instructions.md` | `references/protocol.md` |
| **OpenCode** | System prompt / rules | `references/protocol.md` |
| **Codex / GPT** | System prompt | `references/protocol.md` |
| **Any AI** | Paste the generic snippet | `references/protocol.md` |

---

## Live monitoring (optional)

To have Claude automatically notify you when another AI updates their log — without you asking:

In Claude Code:
```
/loop Cada 2 minutos revisa .ai-collab/ y notifica si otra IA actualizó su log
```

---

## The log format

All AIs write logs in this structure:

```markdown
---
ai: Claude Code (claude-sonnet-4-6)
session: 20260511-143022
project: my-project
updated: 2026-05-11 14:30:22
---

## Working On
[Current task — specific]

## Files Modified This Session
- `src/auth.ts` — fixed timeout bug on line 42

## Decisions Made
- Use JWT expiry of 15min — security requirement

## Do Not Touch (Avoid Conflicts)
- `src/auth.ts` — currently being refactored

## Handoff Note
[What the next AI needs to know]
```

---

## Why this exists

When you work with multiple AI tools simultaneously:
- They duplicate work without knowing it
- They make conflicting edits to the same files
- You lose context switching between them
- You have to manually repeat context to each one

This skill solves all four problems with a simple shared file convention.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

To add support for a new AI tool: add a snippet to `references/protocol.md` and an example file to `examples/`.

---

## License

MIT — created by [Luis Alfredo Velasquez Duran](https://github.com/LuisvelMarketer) / [gsepcore](https://github.com/gsepcore)
