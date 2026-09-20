# Shell

always use ripgrep instead of grep
always use fd instead of find
always use git commit --no-gpg-sign when commiting
always prefer brew over npm for system tools

# Coding
prefer python and use uv

# Markdown
adhere to DavidAnson markdownlint

# Writing preference
- never use em dash or en dash
- never use "this isn't just..., it's..." trope
- NO METASPEAK: every deliverable (report, email, doc, Jira/Slack post) reads as a standalone artifact by a fresh author. Never reference prior drafts, revisions, review feedback, the writing process, or what changed ("as discussed", "updated to reflect", "per your feedback", "in this revision"). Before presenting any deliverable, self-check for these and strip them.

# Public repositories

Before writing a person's name, a machine path, an employer, or an internal
host into any file under git, check whether the repository is public. Do it
without credentials, so the answer does not depend on which account is logged
in or which org owns the repo:

    url="$(git remote get-url origin | sed -E 's#^(ssh://)?git@([^:/]+)[:/]#https://\2/#; s#\.git$##')"
    curl -s -o /dev/null --max-time 10 -w '%{http_code}\n' "$url"

200 means public. 404 or any 3xx means private or gone. Anything else (000,
5xx, 429) means unknown: ask the User before writing.

If it is public, write every file so a stranger can use it:

- Refer to the person as "User", never by name, and use they/them.
- Use generic paths: `~/...`, `$HOME`, or a configurable variable. Never
  hardcode `/Users/<name>/...`.
- Never name an employer, an internal host, or an internal repository path.

# Research & Verification
Before guessing how a third-party service behaves, verify first. In priority order:
1. Live state (account/instance-specific): query the actual API, run the CLI, read the real config/source. Never assume runtime state.
2. Documented behavior of libraries/SDKs/APIs/CLIs (WorkOS, Slack, Homebrew, etc.): use Context7.
3. Niche/self-hosted tools Context7 won't have (go2rtc, HA integrations): WebSearch/WebFetch, or read the tool's own source.
4. Provide links when posting, e.g. Jira Service Management, so Users can see the data, logs, code, documentation, etc.

Show the source you verified against before proposing a fix.

# External memory hub

Personal work has a private hub repository at `~/Development/github.com/ericpardee/personal-memory`, built from the `external-memory/` pattern in the claude-files repo. It holds the current state of every live personal initiative; the work sandbox has its own hub and the two never mix.

- Session start: `git -C ~/Development/github.com/ericpardee/personal-memory pull --rebase --quiet`, read its `INDEX.md`, and load the `initiatives/` spoke for the initiative in play.
- Session end, if that initiative's state changed: append a dated line to the spoke's State log, move its Active initiatives row if status changed, run `python3 scripts/lint.py`, commit, push, per that repo's `CLAUDE.md`.
