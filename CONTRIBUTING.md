# Contributing to AI Collab Skill

Thanks for helping make multi-AI collaboration better.

## What to contribute

- **New AI tool support** — add a snippet to `references/protocol.md` and an example file to `examples/`
- **Bug fixes** — edge cases in the skill behavior, broken protocol snippets
- **Improvements** — better log format, new commands, better documentation

## How to add a new AI tool

1. Add a section to `references/protocol.md` with the setup snippet for that tool
2. Add an example file to `examples/{toolname}.example`
3. Update the "Supported AI tools" table in `README.md`
4. Open a PR with a description of the tool and how you tested the snippet

## Skill format

`SKILL.md` follows the Claude Code skill format:
- YAML frontmatter: `name` and `description` (description drives when the skill triggers)
- Markdown body: instructions Claude follows when the skill is invoked

Keep instructions action-oriented. Explain the *why* when something is non-obvious.

## Testing

Test the skill by:
1. Installing it in `~/.claude/skills/collab/`
2. Running `/collab setup` in a test project
3. Adding your tool's snippet to the appropriate rules file
4. Verifying the AI reads and writes logs correctly

## Pull request checklist

- [ ] Snippet tested with the actual AI tool
- [ ] `references/protocol.md` updated
- [ ] `examples/` file added if applicable
- [ ] `README.md` table updated if adding a new tool
- [ ] No breaking changes to the log format (existing logs must still parse)
