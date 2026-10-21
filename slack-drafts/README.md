# slack-drafts

Slack can't edit a draft, and the Slack MCP connector can't delete one, so an agent that revises a draft leaves the old version next to the new one. `slack-drafts` lists and deletes your own drafts so the agent can clear the old draft before it writes the new one.

```
slack-drafts [-w workspace] list [channel_id]
slack-drafts [-w workspace] clear <channel_id> [thread_ts]
slack-drafts [-w workspace] delete <draft_id>
slack-drafts [-w workspace] raw <draft_id>
```

- `clear` deletes every active draft for one channel, or for one thread when `thread_ts` is given.
- The workspace is a [slackdump](https://github.com/rusq/slackdump) session name: `-w`, else `$SLACK_DRAFTS_WORKSPACE`, else slackdump's current workspace. Create a session with `slackdump workspace new <name>`.
- It calls the Slack web client's `drafts.list` and `drafts.delete` with the session's user token and cookie, and never prints the token. `drafts.delete` wants `client_last_updated_ts` set to the current time; the draft's own `last_updated_ts` returns `draft_has_conflict`.

`bin/slack-drafts` builds the binary into `${XDG_CACHE_HOME:-~/.cache}/agent-files/` on first run, so Go must be installed.
