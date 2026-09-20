# external-memory

A hub-and-spoke memory for a coding agent, kept as plain markdown in a git
repository. One hub file the agent always reads, one spoke file per live
workstream, a pointer registry for credentials, and a linter that runs as a
git hook so the repository can never hold a secret or grow past what an agent
can read in one sitting.

It is the fourth layer of a memory stack; the other three are elsewhere in
this repository:

| Layer | Holds | Where |
| --- | --- | --- |
| 1. Standing rules | How you want work done | `CLAUDE.md`, `AGENTS.md` |
| 2. Per-project memory | One fact per file plus an index, auto-loaded per repo | `<config home>/projects/<key>/memory/` (see `bin/agent-memory-dir`) |
| 3. Session ledger | What happened in each session: outcome, artifacts, open threads, resume command | `session-ledger/` |
| 4. External memory hub | The current state of every live initiative, curated by the agent at session end | this pattern, one repo per identity |

Layers 1 to 3 are automatic or per-repository. Layer 4 is the one that
answers "what am I working on, and where did we leave it" across every
repository and machine, and it is the one people usually do not have.

## Shape of a hub

```text
<hub>/
  CLAUDE.md                 the ritual and hard rules (60 lines max)
  INDEX.md                  people, systems, conventions, active initiatives (150 max)
  initiatives/<name>.md     one spoke per workstream: facts on top, dated state log below (100 max)
  initiatives/_TEMPLATE.md
  ACCESS.md                 where each credential lives; pointers only, never values (200 max)
  LOG.md                    dated events worth remembering, newest first (150 max)
  lint-vocabulary.txt       regexes for content that must never enter this repo
  scripts/lint.py           caps, secret shapes, vocabulary; exit 1 blocks the commit
  hooks/pre-commit, hooks/commit-msg
  *.local.md                machine-specific notes, gitignored
```

## The ritual

Written into the hub's `CLAUDE.md`, which both Claude Code and Codex read as
a project document:

- Session start: `git pull --rebase --quiet`, read `INDEX.md`. Load one spoke
  only when working that initiative.
- Session end, if state changed: append a dated line to the spoke's State
  log, move the row in the Active initiatives table if status changed, run
  the lint, commit, push.
- New initiative: copy `_TEMPLATE.md`. Cross-cutting facts go in `INDEX.md`;
  anything belonging to one workstream goes in its spoke.
- The deletion test for every line: would removing it cause a future session
  to make a mistake? If not, leave it out.

## Why these choices

- **Markdown in git, not a database.** The agent reads the whole hub every
  session, so the format must be readable in one pass and diffable when the
  agent edits it. When search outgrows `rg`, derive a disposable index; never
  make the index the source of truth.
- **Line caps enforced by a hook.** Unbounded memory becomes unread memory.
  When a cap hits, prune resolved state and derivable facts. The caps are the
  forcing function that keeps the hub curated instead of accreted.
- **Pointers, never secrets.** `ACCESS.md` says which env var, profile, or
  vault item holds a credential. The linter rejects token shapes and inline
  assignments before they can be committed, and again in the commit message.
- **A vocabulary blocklist.** Some content belongs somewhere else on purpose
  (candid notes about people, anything a synced clone must never carry).
  `lint-vocabulary.txt` names the words that mark it, and the hook refuses
  the commit. Keep it in step with wherever that content is supposed to live.
- **One hub per identity.** Work and personal get separate repositories with
  separate git identities, so a clone on one machine never carries the other
  side's state.

## Scaffold a hub

```bash
./external-memory/scaffold.sh ~/path/to/my-memory "My"
cd ~/path/to/my-memory
gh repo create <owner>/my-memory --private --source . --push
```

`scaffold.sh` copies the templates, substitutes the name, installs the lint
and hooks, sets `core.hooksPath=hooks`, runs the lint, and makes the first
commit. Fill `INDEX.md`, add a spoke per live initiative, and point your
agents at it.

## Wire it into your agents

Add to the global `CLAUDE.md` (Claude Code) and `AGENTS.md` (Codex), with the
hub's real path:

```markdown
# External memory hub

- Session start: `git -C ~/path/to/my-memory pull --rebase --quiet`, read its
  `INDEX.md`, load the spoke for the initiative in play.
- Session end, if state changed: append to the spoke, lint, commit, push, per
  that repo's `CLAUDE.md`.
```

## Read it from another machine

A consumer (an assistant on a home server, a second laptop) needs a read-only
deploy key and a timer. GitHub: `gh repo deploy-key add key.pub --title
<machine> --repo <owner>/my-memory` (read-only by default). On the consumer:

```ini
# ~/.ssh/config
Host github-my-memory
  HostName github.com
  User git
  IdentityFile ~/.ssh/my-memory-ro
```

```bash
git clone git@github-my-memory:<owner>/my-memory.git ~/my-memory
```

```ini
# ~/.config/systemd/user/my-memory-pull.service
[Service]
Type=oneshot
ExecStart=/usr/bin/git -C %h/my-memory pull -q --ff-only

# ~/.config/systemd/user/my-memory-pull.timer
[Timer]
OnCalendar=*-*-* 03,09,15,21:20:00
Persistent=true
[Install]
WantedBy=timers.target
```

The consumer only ever pulls. A machine that must stay isolated (a
company-managed laptop) can still push to the hub through GitHub without
any other machine reaching it.
