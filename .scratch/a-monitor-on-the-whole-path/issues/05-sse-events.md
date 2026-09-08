# 05: SSE events — live without polling

**What to build:**

A single `EventBus` lives in `friday/ops/events.py` and is the seam
between `Database` writers and the SSE endpoint. The bus is in
memory because the store already records every event as a row —
the bus is the cache for the hot path, not the source of truth.

**Bus contract:**

- One `asyncio.Queue` per subscriber; slow subscribers cannot
  block the bus because `put_nowait` drops rather than blocks.
- A replay buffer of `REPLAY_LIMIT` (200) events, snapshot at
  subscribe time. SSE reconnect sends `Last-Event-ID`; the bus
  replays everything after that id.
- Monotonic id per event.

**Endpoint:**

- `GET /api/events` returns `text/event-stream`. Reads
  `Last-Event-ID`, replays, then yields live events until the
  client disconnects. A keepalive comment every 15s keeps proxies
  from closing the idle connection. Disabling proxy buffering
  (`X-Accel-Buffering: no`) means events reach the browser the
  moment the bus publishes them.

**Client:**

- `web/src/useEventStream.ts` wraps the browser's native
  `EventSource`. Reconnect is the browser's job; this hook keeps
  `onEvent` in a ref so the consumer's renders do not tear down
  the connection.
- `MonitorScreen` merges the initial snapshot's events with the
  live tail. The header pill switches from "polling" to "live"
  when SSE connects, so the operator can see at a glance whether
  the screen is current.

**Blocked by:** 04.

**Decisions:** D5.

**Status:** done

- [ ] `friday/ops/events.py` defines `EventBus` (publish,
      subscribe, unsubscribe), `Event` (id, type, occurred_at,
      payload), `get_bus()` (lazy singleton), and
      `reset_bus_for_tests()`.
- [ ] `Database.record_model_call` and
      `Database.record_tool_call` publish on the bus after the
      row is written. The store does not wait for the bus —
      publish is sync and never blocks.
- [ ] `/api/events` returns `text/event-stream` with
      `Last-Event-ID` replay and a 15s keepalive comment.
- [ ] `Cache-Control: no-cache` and
      `X-Accel-Buffering: no` headers disable proxy buffering.
- [ ] `_format_sse` produces the SSE wire shape (`id`, `event`,
      `data`, blank line). Payload is scrubbed via `_clean()` so
      every string runs through `scrub` on the way out.
- [ ] `web/src/useEventStream.ts` exposes `connected` from the
      underlying `EventSource.onopen` / `onerror`. `MonitorScreen`
      uses it to flip the header pill.
- [ ] `tests/test_events.py` covers the bus contract:
      per-subscriber delivery, monotonic id, replay snapshot,
      unsubscribe, full-queue drop, singleton, slow-subscriber
      isolation.
- [ ] `tests/test_web_tokens.py::test_monitor_polling_does_not_exist`
      pinned `useEventStream` — the audit's rule that SSE replaces
      polling.
