# slack-drafts

Slack can't edit a draft, and the Slack MCP connector can't delete one, so an agent that revises a draft leaves the old version next to the new one. `slack-drafts` lists and deletes your own drafts so the agent can clear the old draft before it writes the new one.

```
slack-drafts [-w workspace] list [channel_id]
slack-drafts [-w workspace] clear [--all] <channel_id> [thread_ts]
slack-drafts [-w workspace] delete <draft_id>
slack-drafts [-w workspace] restore <backup.json>
slack-drafts [-w workspace] raw <draft_id>
```

- `clear` deletes the active drafts for one channel, or for one thread when `thread_ts` is given. It deletes only drafts the API created that no Slack client has touched since. Drafts typed or edited in Slack are kept and printed as `kept`, which also signals that someone edited a generated draft; `delete <draft_id>` removes one after saving it, and `--all` deletes them all.
- `delete` and `clear` save every draft before deleting it, to `$SLACK_DRAFTS_BACKUP_DIR` (default `/tmp/slack-drafts`): `<name>.txt` holds the message as markdown, `<name>.json` the raw blocks. If the save fails, nothing is deleted. Read the `.txt` before writing a replacement, so edits made in the composer are not lost.
- `restore` recreates a draft from a `.json` backup with its original blocks and destination.
- The workspace is a [slackdump](https://github.com/rusq/slackdump) session name: `-w`, else `$SLACK_DRAFTS_WORKSPACE`, else slackdump's current workspace. Create a session with `slackdump workspace new <name>`.
- It calls the Slack web client's `drafts.list` and `drafts.delete` with the session's user token and cookie, and never prints the token. `drafts.delete` wants `client_last_updated_ts` set to the current time; the draft's own `last_updated_ts` returns `draft_has_conflict`.

`bin/slack-drafts` builds the binary into `${XDG_CACHE_HOME:-~/.cache}/agent-files/` on first run, so Go must be installed.
