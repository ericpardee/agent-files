# session-ledger

Automatic catalog of every Claude Code and Codex CLI session. Each session gets a short
distilled entry (outcome, artifacts, open threads, resume command) appended
to a single markdown ledger file, newest first. A nightly sweep catches
sessions the SessionEnd hook missed, and a weekly "dream" pass consolidates
the file.

Excerpts pair each typed user prompt with its latest public assistant response.
Earlier completed work remains visible when a later turn changes topic. When
the excerpt exceeds its character cap, space is shared across turns with more
room reserved for responses than prompts. Tool output and internal reasoning
are excluded, and summaries treat omitted details as unknown.

Portable across machines: all machine-specific values (including the ledger
destination) live in `~/.claude/session-ledger.env`, which is never
committed. On a Mac with Obsidian, point `LEDGER_FILE` at a vault note; on a
work Mac, point it at any plain markdown file.

## Install on a new machine

```bash
git clone git@github.com:ericpardee/claude-files.git ~/Development/github.com/ericpardee/claude-files
cd ~/Development/github.com/ericpardee/claude-files/session-ledger
./install.sh /path/to/ledger.md
python3 ledger.py --seed --days 14
```

Already cloned? `git pull` then rerun `./install.sh` (it is idempotent).

The installer:

1. Writes `~/.claude/session-ledger.env` if missing (commented template).
2. Installs two launchd agents (nightly sweep 02:30, weekly dream Sunday
   03:30) with logs going to `~/.claude/session-ledger.log`.
3. Merges a SessionEnd hook into `~/.claude/settings.json` (skipped if
   already present).

## Secondary installs

Run `install.sh` with `CLAUDE_CONFIG_DIR` exported to a second config dir
(for example a work sandbox) and it installs a separate pair of agents whose
labels carry a suffix taken from that dir's parent, with their own env file,
state, log and lock under that dir. Those plists pin `CLAUDE_CONFIG_DIR` so
the agents run against the right install.

The default install deliberately leaves `CLAUDE_CONFIG_DIR` out of its
plists. Claude Code keys its Keychain credential on whether the variable is
present, so exporting it, even as `~/.claude`, makes `claude -p` under
launchd report "Not logged in" and every distill fails.

`LEDGER_FILE` may live inside an iCloud-synced Obsidian vault
(`~/Library/Mobile Documents/...`). macOS TCC attributes a launchd agent's
file access to the agent's program and refuses iCloud Drive paths to a
`python3` program with `Operation not permitted`, so the plists start
`ledger.py` through `/bin/bash -c 'exec /usr/bin/python3 ...'`, which is
allowed. Keep that wrapper if you edit the templates.

A sweep in which every distill attempt fails exits non-zero, so
`launchctl print gui/$(id -u)/com.claude-session-ledger.nightly` shows a
non-zero last exit code the next morning.

## Codex CLI

Codex sessions are part of the same ledger. The sweep reads Codex rollouts
(`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`, `~/.codex` by default)
next to Claude transcripts; entries for them end in `codex resume <id>`.
Only interactive sessions count: `codex exec` runs and spawned subagent
threads are skipped, and Codex's injected context blocks are not prompts.

The installer appends a `SessionEnd` command hook to `config.toml` (set
`CODEX_HOME` first for a secondary install). Codex passes the same stdin
JSON as Claude Code (`session_id`, `cwd`, `transcript_path`), so the one
hook script serves both. SessionEnd hooks are capped at three seconds; the
hook only spawns the distill and returns. Codex runs a user-defined hook
only once its hash is recorded as trusted in `hooks.state`; the installer
asks `codex app-server` for the hook's key and hash and records them, and
rerunning `install.sh` re-trusts a hook whose command line changed.

Distillation can run on either CLI. `DISTILL_TOOL=codex` uses
`codex exec --ephemeral -s read-only` with `CODEX_MODEL` (empty means the
model configured in Codex) at `CODEX_REASONING_EFFORT` (default `low`).
Ephemeral matters: a non-ephemeral run would leave a rollout that the next
sweep would treat as a session.

## Config reference

`~/.claude/session-ledger.env`, `KEY=VALUE` lines. Process environment
variables with the same names override the file.

| Key | Default | Meaning |
| --- | --- | --- |
| `LEDGER_FILE` | required | Markdown file entries are appended to |
| `MODEL` | `claude-haiku-4-5-20251001` | Model for distill and dream calls |
| `PROJECTS_DIR` | `~/.claude/projects` | Claude Code transcript location |
| `MIN_NEW_PROMPTS` | `1` | New user prompts needed before redistilling |
| `MAX_EXCERPT_CHARS` | `10000` | Cap on excerpt sent to the model |
| `CODEX_SESSIONS_DIR` | `$CODEX_HOME/sessions` or `~/.codex/sessions` | Codex rollouts to sweep; `none` disables |
| `DISTILL_TOOL` | `claude` | `claude` (claude -p) or `codex` (codex exec) |
| `CODEX_MODEL` | unset | Model for `codex exec` distills; unset uses Codex's configured model |
| `CODEX_REASONING_EFFORT` | `low` | Reasoning effort for `codex exec` distills |
| `BACKFILL_MAX_AGE_DAYS` | `30` | A never-seen session older than this is marked stale, not distilled; `--seed --days N` backfills deliberately |
| `POST_SWEEP_CMD` | unset | Shell command run after a sweep that distilled at least one session, and after a successful dream (for example a script that commits and pushes the ledger). 300s timeout, exit code logged |

Other files:

- `~/.claude/session-ledger.state.json` tracks per-session progress
- `~/.claude/session-ledger.log` one line per action or skip
- `~/.claude/session-ledger.lock` prevents hook and cron collisions
- `~/.claude/session-ledger-runs/` cwd for `claude -p` calls (recursion
  guard: sessions created there are never cataloged)

## Modes

```bash
python3 ledger.py --sweep              # distill sessions with new activity
python3 ledger.py --session <path|id>  # distill one session (hook uses this)
python3 ledger.py --seed --days 14     # backfill recent sessions
python3 ledger.py --dream              # weekly consolidation
python3 ledger.py --sweep --dry-run    # any mode: print, write nothing
```

## How the dream pass works

Weekly, `--dream` sends the whole ledger to `claude -p` and rewrites it in
place: duplicate or related entries merge, resolved threads get marked
closed, and every `claude --resume <id>` line is preserved. It also puts a
short `## Promote to memory` section at the top with durable facts worth
adding to long-term memory, for human review. The previous version is kept
at `LEDGER_FILE.bak`. A model response that is malformed, or that drops any
`claude --resume <id>` present in the input, leaves the ledger untouched and
the run exits non-zero.

## Cost

Haiku by default. Roughly one short call per active session per day (the
excerpt is capped at 10k characters), plus one larger call per week for the
dream pass. A day with five active sessions costs a few cents.

Distill calls get five minutes each. The dream call rewrites the whole file
and is allowed thirty minutes; a ledger of around 80 entries needs more than
five, so expect the Sunday run to take a while.

## Uninstall

```bash
launchctl bootout "gui/$(id -u)/com.claude-session-ledger.nightly"
launchctl bootout "gui/$(id -u)/com.claude-session-ledger.dream"
rm ~/Library/LaunchAgents/com.claude-session-ledger.*.plist
```

Then remove the `session-ledger` entry from `hooks.SessionEnd` in
`~/.claude/settings.json`, and optionally delete
`~/.claude/session-ledger.{env,state.json,log}` and
`~/.claude/session-ledger-runs/`.
