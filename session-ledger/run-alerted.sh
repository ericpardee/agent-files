#!/bin/bash
# launchd entry point for the scheduled modes. Runs ledger.py as a child of
# this bash (macOS TCC then allows iCloud Drive paths, see the plist comment)
# and reports any failure ledger.py could not report itself (to the alert router when
# ALERT_ROUTER_URL is set, else Pushover):
# python3 missing or refusing to start (an unaccepted Xcode license exits 69),
# or a crash before ledger.py reached its own alert.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cdir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
marker="$cdir/session-ledger.alerted"
logf="$cdir/session-ledger.log"
start=$(date +%s)

/usr/bin/python3 "$here/ledger.py" "$@"
rc=$?
[ "$rc" -eq 0 ] && exit 0

# ledger.py already alerted for this run: its marker is newer than our start.
if [ -f "$marker" ] && [ "$(stat -f %m "$marker" 2>/dev/null || stat -c %Y "$marker")" -ge "$start" ]; then
  exit "$rc"
fi

rurl=$(sed -n 's/^ALERT_ROUTER_URL=//p' "$cdir/session-ledger.env" 2>/dev/null | tail -1)
rtok=$(sed -n 's/^ALERT_ROUTER_TOKEN=//p' "$cdir/session-ledger.env" 2>/dev/null | tail -1)
label=$(sed -n 's/^INSTALL_LABEL=//p' "$cdir/session-ledger.env" 2>/dev/null | tail -1)
if [ -n "$rurl" ]; then
  recent=$(tail -5 "$logf" 2>/dev/null)
  payload=$(printf '%s' "$recent" | /usr/bin/python3 -c 'import json,sys; print(json.dumps({"source":"session-ledger","check":sys.argv[1] or "default","state":"fail","human_required":False,"detail":"ledger.py exited "+sys.argv[2]+" before it could report. Recent log: "+sys.stdin.read()[:800]}))' "$label" "$rc")
  curl -s -o /dev/null --max-time 10 -H "Authorization: Bearer $rtok" -H "Content-Type: application/json" \
    --data "$payload" "${rurl%/}/event" \
    && echo "$(date +%Y-%m-%dT%H:%M:%S) router alert sent by run-alerted.sh (exit $rc)" >>"$logf"
  exit "$rc"
fi

token=$(sed -n 's/^PUSHOVER_APP_TOKEN=//p' "$cdir/session-ledger.env" 2>/dev/null | tail -1)
user=$(sed -n 's/^PUSHOVER_USER_KEY=//p' "$cdir/session-ledger.env" 2>/dev/null | tail -1)
if [ -n "$token" ] && [ -n "$user" ]; then
  recent=$(tail -5 "$logf" 2>/dev/null)
  curl -s -o /dev/null --max-time 10 https://api.pushover.net/1/messages.json \
    --data-urlencode "token=$token" --data-urlencode "user=$user" \
    --data-urlencode "title=session-ledger failed" \
    --data-urlencode "message=$cdir: ledger.py $* exited $rc before it could report. Recent log: $recent" \
    --data-urlencode "priority=1" \
    && echo "$(date +%Y-%m-%dT%H:%M:%S) pushover alert sent by run-alerted.sh (exit $rc)" >>"$logf"
fi
exit "$rc"
