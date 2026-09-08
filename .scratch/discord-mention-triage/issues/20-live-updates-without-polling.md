# 20: Live updates without polling — DONE

> Landed in ticket 05 of
> [`.scratch/a-monitor-on-the-whole-path/`](../a-monitor-on-the-whole-path/issues/05-sse-events.md):
> `GET /api/events` returns `text/event-stream`; the bus carries every
> `model_call` and `tool_call` row the moment it is written. The Monitor
> screen subscribes on mount and merges live events into its snapshot.
> Last-Event-ID powers reconnect replay.

**What to build:** The board updates as things happen instead of asking every few
seconds, and it recovers on its own when the connection drops or the agent restarts.

**Blocked by:** 18

**Status:** done

Server-sent events are the shape that fits: the traffic is one-directional, the page
is read-only, and reconnection is the browser's problem rather than something to
hand-write. FastAPI has this built in now.

The cost lands in a process that already runs five loops beside two Discord gateways:
a connection held open is a coroutine held open, it has to be cancelled at shutdown or
the server will not exit, and it must not hold a database connection across a yield.
`Heartbeat` already computes a summary on a timer, but at a cadence far slower than
the board wants, so this needs its own.

Polling already works. This is worth doing because it is now small, not because the
current board is broken — if it turns out not to be small, the honest outcome is to
stop and keep polling.

- [ ] The board reflects a new message, a new task, or a failed send without being asked
- [ ] Closing the tab releases whatever the connection was holding
- [ ] The agent shuts down cleanly with a board open
- [ ] A dropped connection or an agent restart recovers without a manual refresh
- [ ] A missed update while disconnected does not leave the page permanently wrong
