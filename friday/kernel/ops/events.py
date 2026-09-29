"""In-process event bus for SSE.

A single `EventBus` is created at startup and shared between the
writers (`Database.record_*_call`, friends) and the SSE
endpoint (`/api/events`). Writers put, readers get. The bus is the
whole reason SSE is faster than polling — it carries the event
the moment the row is written, not the moment the next poll
fires.

Two design choices, named so a future reader does not have to
reverse-engineer them:

1. **One queue per subscriber.** A `asyncio.Queue` per subscriber
   isolates slow readers from fast ones; an operator with the
   browser tab in the background does not block the bus. The
   memory cost is one queue per active SSE connection — small.

2. **Replay buffer, not an event log.** SSE reconnects with a
   `Last-Event-ID` header, and the bus honours it by replaying the
   last `REPLAY_LIMIT` events it has seen. The buffer is in
   memory because the wire already carries every event as a row
   in `model_calls` / `tool_calls` — the buffer is a cache for the
   hot path, and a restart asks the store if anything was missed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: How many events the bus keeps in memory for the `Last-Event-ID`
#: replay path. 200 is the same number the audit uses for the feed
#: cap on the Monitor screen — a buffer the size of one screen
#: load handles a refresh without round-tripping the store.
REPLAY_LIMIT = 200


@dataclass(frozen=True)
class Event:
    """One event on the bus.

    `id` is monotonically increasing within the bus so a
    `Last-Event-ID: 42` header asks for events after id 42. The
    store rows that produce these events each carry their own
    autoincrement id; the bus assigns a separate sequence so
    that a future event type without one (a task_opened row that
    does not exist yet, say) is still ordered.
    """

    id: int
    type: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._buffer: deque[Event] = deque(maxlen=REPLAY_LIMIT)
        self._next_id = 0
        self._lock = asyncio.Lock()

    def publish(self, type_: str, payload: dict[str, Any]) -> Event:
        """One event for every subscriber. Returns the event so
        callers can log the id; subscribers get a copy."""
        self._next_id += 1
        event = Event(
            id=self._next_id,
            type=type_,
            occurred_at=datetime.now(UTC),
            payload=payload,
        )
        # The replay buffer is a snapshot of the most recent
        # `REPLAY_LIMIT` events, not a chronological log. Anything
        # an SSE reconnect could not reach by replay is a real
        # miss, not a buffered one.
        self._buffer.append(event)
        # Slow subscribers cannot block the writer — `put_nowait`
        # raises if the queue is full, and we drop rather than
        # block, because the SSE spec says a reconnecting client
        # catches up by replay anyway.
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    async def subscribe(self) -> tuple[asyncio.Queue[Event], list[Event]]:
        """Register a new subscriber; return its queue and the
        replay buffer it should consume before live events.

        The replay list is a snapshot at subscribe time — it does
        not grow as the buffer grows, so the subscriber does not
        see duplicates of events it has already received."""
        async with self._lock:
            queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=REPLAY_LIMIT)
            self._subscribers.add(queue)
            # `list(...)` copies; mutations to the deque after this
            # point are not visible to the subscriber.
            replay = list(self._buffer)
        return queue, replay

    async def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    """The single in-process event bus.

    Lazy-initialised so importing this module does not build one
    (the test suite imports it transitively; an unbounded queue
    in a unit test would mask anything that grew on it). The
    first call creates the bus; subsequent calls return the same
    one, so a writer and a reader always see the same instance."""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus_for_tests() -> None:
    """Drop the bus so the next `get_bus()` builds a fresh one.
    Tests that publish must call this between cases, otherwise an
    event from case A leaks into case B."""
    global _bus
    _bus = None
