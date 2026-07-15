"""channel-connector — the Feishu WS byte-path service (design `feishu-bot-bytepath.md`).

Model B: each account runs its own self-built Feishu app, so a WS connection IS an
account (no union_id routing). This service holds one long-connection per *effective*
account (thread-per-app), reconciles that set off the dataplane by polling
`feishu_configs.list_effective()` and diffing `desired_digest`, and relays inbound
DMs to `ar-{account}` `/run/stream` (waking the pod first), streaming the reply back
out to Feishu. The ar pod never touches IM — the connector owns the socket.

Session lifecycle inherits the SDK's slash commands: `/clear` + `/compact` pass
straight through to the run; `/new` detaches the binding (session_uuid → NULL) so the
next DM starts a fresh session.
"""
