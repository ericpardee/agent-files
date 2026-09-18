---
name: codex
description: Use when the user asks to run Codex CLI (codex exec, codex resume) or references OpenAI Codex for code analysis, refactoring, or automated editing
---

# Codex Skill Guide

## Running a Task

1. If unclear, ask the user (via AskUserQuestion) what they want reviewed or changed.

2. Assemble the codex command with appropriate options:
   - `-m, --model gpt-5.6-sol` (default model; pair with `-c model_reasoning_effort="xhigh"`. Fallback if unavailable: `gpt-5.6-terra`, also at xhigh)
   - `-c model_reasoning_effort="xhigh"` (default reasoning effort; options: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`)
   - `--sandbox <mode>` - use `read-only` for reviews, `workspace-write` for edits, `danger-full-access` for network/broad access
   - `--full-auto` - only for write operations, not needed for read-only
   - `-C, --cd <DIR>` - run from a different directory

3. When continuing a previous session, use resume syntax:
   ```
   codex exec resume --last "your prompt here" </dev/null 2>/dev/null
   ```
   Add `--skip-git-repo-check` if running outside a git repo.
   Do not use configuration flags when resuming unless explicitly requested - the session inherits original settings.

4. For code reviews, prefer the dedicated review subcommand:
   ```
   codex exec review --base main </dev/null 2>/dev/null
   ```
   Options: `--uncommitted` (staged/unstaged/untracked), `--base <branch>`, `--commit <sha>`.

   **The scope flags cannot be combined with custom review instructions.** Each of
   `--base`, `--commit`, and `--uncommitted` conflicts with the `[PROMPT]` positional
   and fails arg parsing (`the argument '--base <BRANCH>' cannot be used with
   '[PROMPT]'`, verified on codex-cli 0.146.1). So pick one:
   - Codex's default review instructions on a specific scope: use `codex exec review`
     with the scope flag and no prompt, as above.
   - Your own review instructions: use plain `codex exec` and state the scope in the
     prompt, e.g. `codex exec --sandbox read-only "<instructions>. Review only the
     changes on this branch relative to main; see them with git diff main...HEAD."`

5. **IMPORTANT**: Append `2>/dev/null` to suppress thinking tokens (stderr). Only show stderr if debugging is needed.

6. **Always redirect stdin from `/dev/null`.** `codex exec` appends stdin to the
   prompt, so without the redirect it blocks on an open terminal and looks exactly
   like a slow reasoning pass, hanging until killed. The tell is empty stdout with
   `Reading additional input from stdin...` on stderr.

7. Run the command, summarize the outcome for the user.

8. **After Codex completes**, inform the user: "You can resume this Codex session at any time by saying 'codex resume'."

## Quick Reference

| Use case | Command example |
| --- | --- |
| Code review | `codex exec review --base main </dev/null 2>/dev/null` |
| Review uncommitted | `codex exec review --uncommitted </dev/null 2>/dev/null` |
| Review a commit | `codex exec review --commit abc123 </dev/null 2>/dev/null` |
| Review with your own instructions | `codex exec --sandbox read-only "<instructions> Review only <scope>." </dev/null 2>/dev/null` |
| Apply edits | `codex exec --sandbox workspace-write --full-auto "Refactor..." </dev/null 2>/dev/null` |
| Full access | `codex exec --sandbox danger-full-access --full-auto "..." </dev/null 2>/dev/null` |
| Resume | `codex exec resume --last "continue with..." </dev/null 2>/dev/null` |
| Different dir | `codex exec -C /path/to/dir --sandbox read-only "..." </dev/null 2>/dev/null` |

## Following Up

- When output includes actionable findings or the user might want changes applied, offer to resume the session.
- When resuming, pass the new prompt as an argument - the session keeps its original model, reasoning effort, and sandbox mode.

## Auto-Fixing Critical Bugs in PR Reviews

When codex identifies **HIGH severity** bugs during PR reviews, automatically fix them without asking for permission:

1. **Identify severity**: Parse codex output for "High" or "HIGH" severity bugs
2. **Auto-fix workflow**:
   ```bash
   # Resume codex session with fix instructions
   codex exec resume --last --sandbox workspace-write --full-auto "Fix all HIGH severity bugs identified in the review. For each bug, apply the necessary code changes." </dev/null 2>/dev/null
   ```
3. **Commit fixes**: After codex applies fixes, commit with descriptive message
4. **Report**: Tell user what was fixed

**Severity guidelines:**
- **HIGH**: Auto-fix (data loss, security holes, correctness bugs, broken functionality)
- **MEDIUM**: Ask user first (performance issues, tech debt, unclear impact)
- **LOW**: Report only (style suggestions, minor improvements)

**Safety notes:**
- Only auto-fix in review/PR context (not exploratory coding)
- Always commit fixes immediately after applying
- User can revert commits if needed
- If codex fix fails or is unclear, stop and ask user

**Example:**
```
Codex found: "High - Date constraints never reach Qdrant"
→ Automatically resume codex to fix
→ Commit: "Fix: Push date constraints to Qdrant query"
→ Report: "Fixed HIGH severity date filtering bug in query.py"
```

## Error Handling

- Stop and report failures when `codex` exits non-zero; request direction before retrying.
- Before using `--full-auto`, `--sandbox danger-full-access`, or `--skip-git-repo-check`, ask for user permission unless already given.
- When output includes warnings or partial results, summarize and ask how to proceed.
