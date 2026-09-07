# 05: SSE events — live without polling

**What to build:**

Server: `GET /api/events` in `friday/ops/api.py` returning
`StreamingResponse(media_type="text/event-stream")`. Each event is a
JSON line of `{id, type, occurred_at, payload}`. The store already
records every relevant event as a row (`model_calls`, `tool_calls`,
`messages`, `tasks`), so the implementation is a `SELECT WHERE id >
last_seen` loop in an `asyncio.Queue` per subscriber. `Last-Event-ID`
header triggers replay on reconnect.

Client: `web/src/useEventStream.ts` — hook wrapping `EventSource`. On
message, dispatch into a small typed store. The Monitor screen
subscribes in `useEffect`. Reconnect is the browser's.

Wiring: each existing call site that writes to `model_calls` /
`tool_calls` etc. also enqueues onto the event queue. One change, one
place per writer, so the event types are exhausted by what the store
already records.

**Blocked by:** 04.

**Decisions:** D5.

**Status:** ready-for-agent
