"""Ticket 09 — the memory an agent keeps itself.

Four `Database` methods, all scope-filtered by channel: `remember` staged a
guess for a promotion pass that never ran long enough to matter (nothing wrote
one for months); this is an agent writing directly and reading back what it
wrote, with the floor moved from an approval count to three narrower
guarantees — see `friday/tools/memory.py`'s module docstring for the argument.

These tests drive the store directly, without the tool layer: `MemoryScope` is
the only shape a caller needs, and every one of the properties below has to
hold at the store, because a tool that forgot to check would just relay
whatever it got back.
"""

from __future__ import annotations

from friday.domain.models import MemoryScope

ROOM = MemoryScope(channel_id="c1", task_id=7, agent="responder")
OTHER_ROOM = MemoryScope(channel_id="c2", task_id=None, agent="responder")


async def test_a_memory_is_written_and_found_by_search(db):
    await db.memory_add(ROOM, "checkout runs on cluster b")

    (found,) = await db.memory_search(ROOM, "cluster", limit=8)

    assert found.text == "checkout runs on cluster b"
    assert found.channel_id == "c1"


async def test_search_finds_nothing_in_an_empty_room(db):
    assert await db.memory_search(ROOM, "anything", limit=8) == []


async def test_a_room_cannot_read_another_rooms_memory(db):
    await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_search(ROOM, "deploy", limit=8) == []
    assert await db.memory_search(OTHER_ROOM, "deploy", limit=8) != []


async def test_correcting_a_memory_replaces_its_text(db):
    written = await db.memory_add(ROOM, "they always send a curl")

    updated = await db.memory_update(ROOM, written.id, "they usually send a curl")

    assert updated.text == "they usually send a curl"
    (found,) = await db.memory_search(ROOM, "curl", limit=8)
    assert found.text == "they usually send a curl"


async def test_correcting_an_id_from_another_room_fails_the_same_as_missing(db):
    theirs = await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_update(ROOM, theirs.id, "anything") is None
    assert await db.memory_update(ROOM, "not-a-real-id", "anything") is None


async def test_deleting_a_memory_removes_it_from_search(db):
    written = await db.memory_add(ROOM, "the staging key rotates weekly")

    assert await db.memory_delete(ROOM, written.id) is True

    assert await db.memory_search(ROOM, "staging", limit=8) == []


async def test_deleting_an_id_from_another_room_fails_the_same_as_missing(db):
    theirs = await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_delete(ROOM, theirs.id) is False
    assert await db.memory_delete(ROOM, "not-a-real-id") is False


async def test_a_deleted_memory_cannot_be_corrected_either(db):
    """Gone is gone: an id that once resolved and no longer does reads the
    same as one that never existed, in both directions."""
    written = await db.memory_add(ROOM, "the staging key rotates weekly")
    await db.memory_delete(ROOM, written.id)

    assert await db.memory_update(ROOM, written.id, "anything") is None
    assert await db.memory_delete(ROOM, written.id) is False


async def test_a_neighbouring_id_does_not_resolve(db):
    """Guessing "the next one" must not land on a real, different memory —
    which is the actual test of *sparse, not sequential*, so this has to
    write a second memory and check the guess against its real id rather
    than only checking that a guess resolves to nothing.

    An earlier version of this test wrote one memory and asserted that a
    guess resolved to nothing, which passes whether ids are random tokens or
    small sequential integers, as long as nothing else happens to occupy
    that slot yet — it could not have told the two apart. It was also
    computing the wrong guess to begin with: the id is a hex token, and
    `str(int(id, 16) + 1)` renders *decimal*, which is never the
    neighbouring hex id — `written.id` is 12 hex characters, and `.isdigit()`
    holds for a 12-character hex token about 0.3% of the time (measured over
    100k samples), so the real assertion ran on almost no seed at all.
    """
    first = await db.memory_add(ROOM, "the first memory in this room")
    second = await db.memory_add(ROOM, "the second memory in this room")

    guessed = format(int(first.id, 16) + 1, "012x")
    assert guessed != second.id, (
        "sequential ids would make this the real second row — sparse ones "
        "do not"
    )
    assert await db.memory_update(ROOM, guessed, "anything") is None

    # The real property: an id is long enough that guessing one that exists
    # is not a search problem.
    assert len(first.id) >= 8


async def test_a_full_channel_refuses_a_new_memory_rather_than_evicting_one(db):
    """At the cap, nothing is silently dropped — not even the oldest row.
    Losing *any* memory with no trace is the exact failure this design
    otherwise refuses to produce; a full channel is refused instead, and
    every memory it already held is still there afterwards."""
    from friday.store.db import Database

    for n in range(Database.MEMORY_PER_CHANNEL):
        assert await db.memory_add(ROOM, f"fact number {n}") is not None

    assert await db.memory_add(ROOM, "one more than the room can hold") is None

    still_there = await db.memory_search(ROOM, "fact", limit=1000)
    assert len(still_there) == Database.MEMORY_PER_CHANNEL
    assert any(m.text == "fact number 0" for m in still_there), (
        "the oldest memory was not silently evicted to make room"
    )


async def test_the_operator_can_see_a_deleted_memory_and_who_removed_it(db):
    """`memory_delete` hides a line from every tool; it does not erase what it
    said or who took it out. That is the operator's floor now that a
    corroboration count is not: visibility into what an agent wrote and
    unwrote."""
    written = await db.memory_add(ROOM, "the staging key rotates weekly")
    await db.memory_delete(ROOM, written.id)

    everything = await db.memories_for_channel(ROOM.channel_id)

    (gone,) = [m for m in everything if m.id == written.id]
    assert gone.text == "the staging key rotates weekly"
    assert gone.deleted_by == ROOM.agent
    assert gone.deleted_at is not None


async def test_the_operators_view_of_a_channel_includes_live_memories_too(db):
    written = await db.memory_add(ROOM, "checkout runs on cluster b")

    everything = await db.memories_for_channel(ROOM.channel_id)

    assert [m.id for m in everything] == [written.id]
    assert everything[0].deleted_at is None


async def test_the_operators_view_of_a_channel_does_not_leak_another_ones(db):
    """The four tool-facing methods are all scope-filtered, each with its own
    wrong-room test. This is the fifth reader — the one an unauthenticated
    HTTP route serves — and it had none: deleting the `channel_id` filter
    left the whole suite green."""
    await db.memory_add(ROOM, "checkout runs on cluster b")
    await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    everything = await db.memories_for_channel(ROOM.channel_id)

    assert [m.channel_id for m in everything] == [ROOM.channel_id]


async def test_search_orders_by_recency_not_by_how_well_it_matches(db):
    """`memory_search` does not rank — it cannot, cheaply, against a channel
    that changes underneath every call — so "best match first" would be a
    claim the method does not back up. Newest first is the honest order."""
    await db.memory_add(ROOM, "checkout runs on cluster b")
    await db.memory_add(ROOM, "checkout also runs a batch job")

    found = await db.memory_search(ROOM, "checkout", limit=8)

    assert [m.text for m in found] == [
        "checkout also runs a batch job",
        "checkout runs on cluster b",
    ]


async def test_an_empty_query_returns_the_channels_recent_memories(db):
    """Not validated against: a model sending "" is asking to see what is
    there, and every word-count check passes vacuously on no words at all."""
    await db.memory_add(ROOM, "checkout runs on cluster b")
    await db.memory_add(ROOM, "they deploy on fridays")

    found = await db.memory_search(ROOM, "", limit=8)

    assert len(found) == 2


async def test_deleting_a_memory_frees_its_slot_at_the_cap(db):
    """The cap counts live memories, not rows — a channel that has churned
    200 lines must not be permanently full. Nothing pinned this: replacing
    the count's `deleted_at.is_(None)` with a plain channel count also left
    the whole suite green."""
    from friday.store.db import Database

    for n in range(Database.MEMORY_PER_CHANNEL):
        assert await db.memory_add(ROOM, f"fact number {n}") is not None
    assert await db.memory_add(ROOM, "one more than the room can hold") is None

    first = (await db.memory_search(ROOM, "fact number 0", limit=1))[0]
    await db.memory_delete(ROOM, first.id)

    assert await db.memory_add(ROOM, "now there is room again") is not None


async def test_the_store_enforces_the_same_text_length_the_tool_advertises(db):
    """`friday/tools/memory.py`'s `TEXT_CHARS` is what `memory_add` cuts to
    and what its docstring quotes to the model — but the store is what a
    second caller would actually reach, the same argument
    `MEMORY_PER_CHANNEL`'s own comment already makes for the count. A store
    that did not also cut would let a caller who skips the tool write
    anything, however long."""
    from friday.store.db import Database
    from friday.tools.memory import TEXT_CHARS

    assert Database.TEXT_CHARS == TEXT_CHARS, (
        "the tool's advertised limit and the store's enforced one must be "
        "the same number, or one of them is a lie"
    )

    written = await db.memory_add(ROOM, "x" * (Database.TEXT_CHARS + 100))
    assert len(written.text) == Database.TEXT_CHARS

    updated = await db.memory_update(ROOM, written.id, "y" * (Database.TEXT_CHARS + 100))
    assert len(updated.text) == Database.TEXT_CHARS


async def test_memory_search_requires_a_limit_rather_than_defaulting_to_one(db):
    """`limit` had a bare-literal default (`= 8`) that duplicated `RESULTS` —
    the tool always passed it explicitly, so the default existed only to be
    wrong later, the same objection that retired `Limits`. Required now, so
    a caller that forgets it fails at the call rather than silently agreeing
    with `RESULTS` by coincidence."""
    import pytest

    with pytest.raises(TypeError):
        await db.memory_search(ROOM, "anything")
