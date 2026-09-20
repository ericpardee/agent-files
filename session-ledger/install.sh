#!/bin/bash
# Idempotent installer for session-ledger. Safe to run twice.
# Usage: ./install.sh /path/to/ledger.md   (or export LEDGER_FILE first)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ENV_FILE="$CLAUDE_DIR/session-ledger.env"
AGENTS_DIR="$HOME/Library/LaunchAgents"

LEDGER_FILE="${1:-${LEDGER_FILE:-}}"

mkdir -p "$CLAUDE_DIR" "$AGENTS_DIR"

# (a) write the per-machine env file if missing
if [ ! -f "$ENV_FILE" ]; then
  if [ -z "$LEDGER_FILE" ]; then
    echo "Usage: $0 /path/to/ledger.md   (or export LEDGER_FILE)" >&2
    exit 1
  fi
  cat >"$ENV_FILE" <<EOF
# session-ledger per-machine config. KEY=VALUE, no shell expansion.
# This file is machine-specific and intentionally NOT in the repo.

# Required. Where ledger entries are appended (any markdown file path,
# e.g. an Obsidian note on the personal Mac or a plain md file at work).
LEDGER_FILE=$LEDGER_FILE

# Model used for distillation and the weekly dream pass.
#MODEL=claude-sonnet-5

# Optional: Pushover alert on any failure exit (both required).
#PUSHOVER_APP_TOKEN=
#PUSHOVER_USER_KEY=

# Where Claude Code stores session transcripts.
#PROJECTS_DIR=$HOME/.claude/projects

# Minimum number of NEW user prompts before a session is (re)distilled.
#MIN_NEW_PROMPTS=1

# Cap on the excerpt sent to the model per session.
#MAX_EXCERPT_CHARS=10000

# Codex CLI sessions are swept too. Default: \$CODEX_HOME/sessions when
# CODEX_HOME is set, else ~/.codex/sessions. "none" ignores Codex.
#CODEX_SESSIONS_DIR=$HOME/.codex/sessions

# Which CLI distills: claude (claude -p with MODEL) or codex (codex exec with
# CODEX_MODEL, empty = Codex's configured default, and CODEX_REASONING_EFFORT).
#DISTILL_TOOL=claude
#CODEX_MODEL=
#CODEX_REASONING_EFFORT=low

# Shell command run after a sweep that distilled something and after a dream.
#POST_SWEEP_CMD=
EOF
  echo "wrote $ENV_FILE"
else
  echo "$ENV_FILE already exists, leaving it alone"
fi

chmod +x "$SCRIPT_DIR/ledger.py" "$SCRIPT_DIR/sessionend-hook.sh" "$SCRIPT_DIR/run-alerted.sh"

# (b) install launchd agents from templates.
# The default install keeps the bare label; a secondary install (running
# with CLAUDE_CONFIG_DIR set elsewhere) gets a suffix derived from its
# config dir so both sets of agents can coexist.
LABEL_SUFFIX=""
if [ "$(cd "$CLAUDE_DIR" && pwd -P)" != "$(cd "$HOME/.claude" && pwd -P)" ]; then
  LABEL_SUFFIX=".$(basename "$(dirname "$CLAUDE_DIR")")"
fi
# Only a secondary install pins CLAUDE_CONFIG_DIR in the plist. The default
# install must leave it unset: Claude Code keys its Keychain credential on
# whether the variable is present, so exporting it (even as ~/.claude) makes
# `claude -p` under launchd report "Not logged in" and every distill fails.
for base in com.claude-session-ledger.nightly com.claude-session-ledger.dream; do
  label="$base$LABEL_SUFFIX"
  src="$SCRIPT_DIR/$base.plist"
  dst="$AGENTS_DIR/$label.plist"
  SED_ARGS=(-e "s|__REPO_DIR__|$REPO_DIR|g" -e "s|__HOME__|$HOME|g"
            -e "s|__CLAUDE_DIR__|$CLAUDE_DIR|g" -e "s|__LABEL__|$label|g")
  if [ -z "$LABEL_SUFFIX" ]; then
    # drop the <key>CLAUDE_CONFIG_DIR</key> line and the <string> after it
    SED_ARGS+=(-e '/<key>CLAUDE_CONFIG_DIR<\/key>/{N;d;}')
  fi
  sed "${SED_ARGS[@]}" "$src" >"$dst"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dst"
  echo "installed $label"
done

# (c) merge the SessionEnd hook into ~/.claude/settings.json
python3 - "$CLAUDE_DIR/settings.json" "$SCRIPT_DIR/sessionend-hook.sh" <<'PYEOF'
import json, os, sys

settings_path, hook_cmd = sys.argv[1], sys.argv[2]
settings = {}
if os.path.isfile(settings_path):
    with open(settings_path) as fh:
        settings = json.load(fh)

hooks = settings.setdefault("hooks", {})
session_end = hooks.setdefault("SessionEnd", [])

if any("session-ledger" in json.dumps(entry) for entry in session_end):
    print("SessionEnd hook already present in settings.json")
else:
    session_end.append({
        "hooks": [{"type": "command", "command": hook_cmd}]
    })
    tmp = settings_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(settings, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, settings_path)
    print("added SessionEnd hook to settings.json")
PYEOF

# (c2) Codex CLI: a SessionEnd command hook in config.toml. Codex passes the
# same stdin JSON (session_id, cwd, transcript_path) as Claude Code, so the
# one hook script serves both. SessionEnd hooks are capped at three seconds,
# which the detached hook script fits with room to spare.
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_CONFIG="$CODEX_DIR/config.toml"
if [ -f "$CODEX_CONFIG" ]; then
  if grep -q "session-ledger" "$CODEX_CONFIG"; then
    echo "Codex SessionEnd hook already present in $CODEX_CONFIG"
  else
    cat >>"$CODEX_CONFIG" <<TOMLEOF

# session-ledger: distill each session into the ledger when it ends.
[[hooks.SessionEnd]]
matcher = "other"

[[hooks.SessionEnd.hooks]]
type = "command"
command = "bash $SCRIPT_DIR/sessionend-hook.sh"
timeout = 3
TOMLEOF
    echo "added Codex SessionEnd hook to $CODEX_CONFIG"
  fi
  # Codex runs a user-defined hook only once its current hash is recorded as
  # trusted in hooks.state. Ask the app-server for the hook's key and hash and
  # record them; a hook whose command line later changes shows as "modified"
  # and needs this again (rerunning install.sh does it).
  if command -v codex >/dev/null 2>&1; then
    CODEX_HOME="$CODEX_DIR" python3 - "$CODEX_CONFIG" "$HOME" <<'PYEOF'
import json, os, re, subprocess, sys
config, cwd = sys.argv[1], sys.argv[2]
try:
    p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
except OSError as exc:
    sys.exit("could not start codex app-server to trust the hook: %s" % exc)
def send(o):
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
def wait(id_):
    for _ in range(80):
        line = p.stdout.readline()
        if not line:
            return None
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == id_:
            return m
try:
    send({"jsonrpc": "2.0", "method": "initialize", "id": 1,
          "params": {"clientInfo": {"name": "session-ledger", "title": "session-ledger", "version": "1.0"}}})
    wait(1)
    send({"jsonrpc": "2.0", "method": "initialized"})
    send({"jsonrpc": "2.0", "method": "hooks/list", "id": 2, "params": {"cwds": [cwd]}})
    resp = wait(2)
finally:
    try:
        p.stdin.close()
    except OSError:
        pass
    p.terminate()
hooks = [h for d in (resp or {}).get("result", {}).get("data", []) for h in d.get("hooks", [])
         if "session-ledger" in (h.get("command") or "") and h.get("sourcePath") == config]
if not hooks:
    sys.exit("codex app-server did not list the session-ledger hook; trust it from the Codex UI")
h = hooks[0]
if h.get("trustStatus") == "trusted":
    print("Codex hook already trusted")
    sys.exit(0)
text = open(config, encoding="utf-8").read()
block = '[hooks.state."%s"]\nenabled = true\ntrusted_hash = "%s"\n' % (h["key"], h["currentHash"])
pattern = r'\[hooks\.state\."%s"\]\n(?:[^\[\n][^\n]*\n)*' % re.escape(h["key"])
if re.search(pattern, text):
    text = re.sub(pattern, block, text)
else:
    text = text.rstrip("\n") + "\n\n" + block
open(config, "w", encoding="utf-8").write(text)
print("trusted Codex hook %s (was %s)" % (h["key"], h.get("trustStatus")))
PYEOF
  else
    echo "codex not on PATH; trust the hook from the Codex UI or rerun install.sh with codex installed"
  fi
else
  echo "no Codex config at $CODEX_CONFIG; Codex sessions are still swept nightly if the sessions dir exists"
fi

# (d) next steps
echo
echo "session-ledger installed."
echo "Next steps:"
echo "  1. Backfill recent sessions:"
echo "     python3 $SCRIPT_DIR/ledger.py --seed --days 14"
echo "  2. Preview first if you like:"
echo "     python3 $SCRIPT_DIR/ledger.py --seed --days 14 --dry-run"
echo "  3. Nightly sweep runs at 02:30, dream pass Sunday 03:30."
echo "     Logs: $CLAUDE_DIR/session-ledger.log"
