"""Pin the EventBus contract.

The bus is the seam between writers (`Database.record_*_call`) and
the SSE endpoint (`/api/events`). It is in-process and in-memory,
so a unit test covers everything SSE needs from it without
spinning up the FastAPI app.
"""
from __future__ import annotations

import asyncio

import pytest

from friday.kernel.ops import events as events_module
from friday.kernel.ops.events import EventBus


@pytest.fixture(autouse=True)
def _reset_bus() -> None:
    """Each test gets a fresh bus.

    The bus is a module-level singleton; without this fixture an
    event published in case A would leak into case B and any
    subscriber count from a prior test would still be wired up."""
    events_module.reset_bus_for_tests()
    yield
    events_module.reset_bus_for_tests()


async def test_publish_delivers_to_each_subscriber() -> None:
    bus = EventBus()
    a, _ = await bus.subscribe()
    b, _ = await bus.subscribe()
    bus.publish("model_call", {"agent": "triage"})
    assert (await asyncio.wait_for(a.get(), 1.0)).type == "model_call"
    assert (await asyncio.wait_for(b.get(), 1.0)).type == "model_call"


async def test_publish_id_is_monotonic() -> None:
    bus = EventBus()
    seen = [bus.publish("x", {}).id for _ in range(5)]
    assert seen == sorted(seen)
    assert len(set(seen)) == 5


async def test_subscribe_returns_a_snapshot_of_the_replay_buffer() -> None:
    """A subscriber joining late should see the events published
    before it subscribed. This is the contract SSE uses to replay
    on reconnect."""
    bus = EventBus()
    bus.publish("a", {})
    bus.publish("b", {})
    queue, replay = await bus.subscribe()
    assert [e.type for e in replay] == ["a", "b"]
    # The replay is a snapshot — events published after subscribe
    # are not in it.
    bus.publish("c", {})
    queue2, replay2 = await bus.subscribe()
    assert [e.type for e in replay2] == ["a", "b", "c"]


async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    queue, _ = await bus.subscribe()
    await bus.unsubscribe(queue)
    bus.publish("x", {})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), 0.1)


async def test_full_queue_drops_instead_of_blocking() -> None:
    """A slow subscriber cannot stall a writer. The bus drops
    rather than blocks, because the SSE spec lets a reconnecting
    client catch up by replay."""
    bus = EventBus()
    queue, _ = await bus.subscribe()
    # Fill the queue. Depending on `asyncio.Queue`'s maxsize, this
    # is REPLAY_LIMIT or less; the constant is internal and we
    # only care that *more* publishes do not raise.
    while True:
        try:
            queue.put_nowait(bus.publish("x", {}))
        except asyncio.QueueFull:
            break
    # At this point the queue is full. Another publish must not
    # raise — it should drop on the slow subscriber and succeed
    # for any future subscribers.
    try:
        bus.publish("x", {})
    except asyncio.QueueFull as exc:
        pytest.fail(f"publish raised on a full subscriber queue: {exc}")


async def test_bus_singleton_returns_same_instance() -> None:
    """The module-level `get_bus` is the seam between `Database`
    writers and the SSE endpoint. Two calls must return the same
    bus, otherwise a writer publishes into the void."""
    from friday.kernel.ops.events import get_bus

    assert get_bus() is get_bus()


async def test_slow_subscriber_does_not_block_others() -> None:
    """One slow subscriber cannot block the bus. The fast one
    receives every event even if the slow one has a full queue."""
    bus = EventBus()
    slow, _ = await bus.subscribe()
    fast, _ = await bus.subscribe()

    # Fill the slow subscriber's queue with copies of one event,
    # so `bus.publish` cannot put a *new* event into it. Use
    # `put_nowait` so the slow queue fills without going through
    # the bus at all — the bus would have also dropped the events
    # into the fast subscriber.
    from friday.kernel.ops.events import REPLAY_LIMIT
    filler = bus.publish("x", {})
    for _ in range(REPLAY_LIMIT):
        try:
            slow.put_nowait(filler)
        except asyncio.QueueFull:
            break
    # The filler also went to fast; drain it so what follows
    # arrives on an empty queue.
    await asyncio.wait_for(fast.get(), 0.5)

    # Now publish more. The fast subscriber must still receive them.
    for i in range(3):
        bus.publish("y", {"i": i})

    received = []
    while True:
        try:
            ev = await asyncio.wait_for(fast.get(), 0.5)
            received.append(ev.payload.get("i"))
        except asyncio.TimeoutError:
            break
    assert received == [0, 1, 2], (
        f"fast subscriber missed events because slow one's queue was full: {received}"
    )
