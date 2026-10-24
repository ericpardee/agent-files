#!/bin/bash
# SessionEnd hook for session-ledger.
# Receives Claude Code hook JSON on stdin (session_id, transcript_path, cwd).
# Must never block or fail session exit: always exits 0, work runs detached.

# SESSION_LEDGER_SKIP=1 keeps throwaway sessions (tests, probes) out of the ledger.
[ -n "${SESSION_LEDGER_SKIP:-}" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
LOG_FILE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/session-ledger.log"

TRANSCRIPT="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("transcript_path") or "")
except Exception:
    pass
' 2>/dev/null)"

if [ -n "$TRANSCRIPT" ]; then
  nohup python3 "$SCRIPT_DIR/ledger.py" --session "$TRANSCRIPT" \
    </dev/null >>"$LOG_FILE" 2>&1 &
  disown 2>/dev/null || true
fi

exit 0
