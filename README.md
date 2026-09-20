# agent-files

Configuration and tooling shared by Claude Code and Codex CLI: standing rules (`CLAUDE.md`, `AGENTS.md`), skills, a status line, a session ledger, and an external-memory pattern. Both agents read the same rules and the same memory, so switching tools loses nothing.

The memory has four layers. Layers 1 and 2 are the agents' own; this repository supplies 3 and 4 and the glue between them.

| Layer | Holds | Where |
| --- | --- | --- |
| 1. Standing rules | How work should be done | `CLAUDE.md`, `AGENTS.md`, `codex/AGENTS-memory-section.md` |
| 2. Per-project memory | One fact per file plus an index, per repository, shared by both agents | resolved by `bin/agent-memory-dir` |
| 3. External memory hub | The current state of every live initiative, in a private repository per identity | `external-memory/` (pattern, scaffold, lint, hooks) |
| 4. Session ledger | Every session distilled to outcome, artifacts, open threads, resume command | `session-ledger/` |

## What is here

- `session-ledger/`: SessionEnd hook plus a nightly sweep that distills each Claude Code or Codex session into a ledger entry, and a weekly consolidation pass. Tested, portable, launchd-managed on macOS.
- `external-memory/`: a hub-and-spoke memory repository pattern with `scaffold.sh`, a lint that runs as a git hook (line caps, secret shapes, blocked vocabulary), and templates.
- `codex/AGENTS-memory-section.md`: the section of Codex's `AGENTS.md` that describes the whole chain and Codex's step in it.
- `bin/agent-memory-dir`: prints the per-project memory directory both agents share. `bin/claude-title` copies a session's generated title for `/rename`.
- `skills/`: `codex-gate` (cross-model review gate with a fix-or-rebut loop), `codex` (Codex CLI reference), `advanced-prompt-improver`, `deliverable-check`, `codex-web-render`, `freeing-disk-space`.
- `statusline.py`: two-line status bar with an account badge that flags a wrong-account session.
- `settings.json`: the personal Claude Code settings that wire the hook, the status line, and plugins.

## Quick start

```bash
git clone git@github.com:ericpardee/agent-files.git ~/Development/github.com/ericpardee/agent-files
cd ~/Development/github.com/ericpardee/agent-files

# 1. Session ledger: hook, nightly sweep, weekly consolidation
session-ledger/install.sh ~/path/to/ledger.md
python3 session-ledger/ledger.py --seed --days 14

# 2. External memory hub: a private repository the agents keep current
external-memory/scaffold.sh ~/path/to/my-memory "My"

# 3. Skills and helpers
ln -s "$PWD"/skills/* ~/.claude/skills/
ln -s "$PWD"/bin/* ~/.local/bin/
```

Each directory's README covers configuration, secondary installs (a second config dir for a work identity), and uninstall.

## Links

- [Claude Code Local Docs](https://github.com/ericbuess/claude-code-docs), the `PreToolUse` hook in `settings.json`
