# agent-files

Configuration and tooling shared by Claude Code and Codex CLI: standing rules (`CLAUDE.md`, `AGENTS.md`), skills, a status line, the session ledger, and the external-memory pattern. Both agents read the same rules and the same memory, so switching tools loses nothing.

- `session-ledger/`: every coding-agent session distilled into a ledger entry (outcome, artifacts, open threads, resume command), with a nightly sweep and a weekly consolidation.
- `external-memory/`: a hub-and-spoke memory repository pattern with a scaffold, a lint that runs as a git hook, and templates.
- `codex/`: the shared memory section for Codex's `AGENTS.md`.
- `bin/agent-memory-dir`: resolves the per-project memory directory both agents share.
- `skills/`: skills usable from either agent.

## Links

- [Claude Code Local Docs](https://github.com/ericbuess/claude-code-docs)
- [Solatis Agents based off Southbridge Research's analysis of Claude Code prompts](https://github.com/solatis/claude-config)
