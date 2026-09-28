"""Ticket 09 — the memory an agent keeps itself.

Four `Database` methods, all scope-filtered by channel: `remember` staged a
guess for a promotion pass that never ran long enough to matter (nothing wrote
one for months); this is an agent writing directly and reading back what it
wrote, with the floor moved from an approval count to three narrower
guarantees — see `friday/kernel/tools/memory.py`'s module docstring for the argument.

These tests drive the store directly, without the tool layer: `FridayState` is
the only shape a caller needs, and every one of the properties below has to
hold at the store, because a tool that forgot to check would just relay
whatever it got back.
"""

from __future__ import annotations

import pytest

from friday.kernel.domain.models import FridayState
from friday.kernel.memory import registry as memory_kinds

ROOM = FridayState(channel_id="c1", task_id=7, agent="responder")
OTHER_ROOM = FridayState(channel_id="c2", task_id=None, agent="responder")
ROOM_WITH_SOURCE = FridayState(
    channel_id="c1", task_id=7, agent="responder", message_id="9001"
)


async def test_a_memory_is_written_and_found_by_search(db):
    await db.memory_add(ROOM, "checkout runs on cluster b")

    (found,) = await db.memory_search(ROOM, "cluster", limit=8, kind=memory_kinds.VOICE)

    assert found.text == "checkout runs on cluster b"
    assert found.channel_id == "c1"


async def test_search_finds_nothing_in_an_empty_room(db):
    assert await db.memory_search(ROOM, "anything", limit=8, kind=memory_kinds.VOICE) == []


async def test_a_room_cannot_read_another_rooms_memory(db):
    await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_search(ROOM, "deploy", limit=8, kind=memory_kinds.VOICE) == []
    assert await db.memory_search(OTHER_ROOM, "deploy", limit=8, kind=memory_kinds.VOICE) != []


async def test_correcting_a_memory_replaces_its_text(db):
    written = await db.memory_add(ROOM, "they always send a curl")

    updated = await db.memory_update(ROOM, written.id, "they usually send a curl")

    assert updated.text == "they usually send a curl"
    (found,) = await db.memory_search(ROOM, "curl", limit=8, kind=memory_kinds.VOICE)
    assert found.text == "they usually send a curl"


async def test_correcting_an_id_from_another_room_fails_the_same_as_missing(db):
    theirs = await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_update(ROOM, theirs.id, "anything") is None
    assert await db.memory_update(ROOM, "not-a-real-id", "anything") is None


async def test_deleting_a_memory_removes_it_from_search(db):
    written = await db.memory_add(ROOM, "the staging key rotates weekly")

    assert await db.memory_delete(ROOM, written.id) is True

    assert await db.memory_search(ROOM, "staging", limit=8, kind=memory_kinds.VOICE) == []


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

    still_there = await db.memory_search(ROOM, "fact", limit=1000, kind=memory_kinds.VOICE)
    assert len(still_there) == Database.MEMORY_PER_CHANNEL
    assert any(m.text == "fact number 0" for m in still_there), (
        "the oldest memory was not silently evicted to make room"
    )


async def test_full_memory_channels_names_a_channel_at_the_cap(db):
    """Board `what-the-room-already-knows`, ticket 12, D18: the visibility
    half of the ceiling, read directly at the store rather than through the
    heartbeat's rendering of it."""
    from friday.store.db import Database

    for n in range(Database.MEMORY_PER_CHANNEL):
        await db.memory_add(ROOM, f"fact number {n}")

    assert await db.full_memory_channels() == ["c1"]


async def test_full_memory_channels_ignores_a_room_with_headroom(db):
    await db.memory_add(ROOM, "test.apero is staging")

    assert await db.full_memory_channels() == []


async def test_full_memory_channels_does_not_count_deleted_or_superseded_rows(db):
    """A channel that has churned through corrections must not read as full
    from rows nothing serves any more."""
    from friday.store.db import Database

    written = [
        await db.memory_add(ROOM, f"fact number {n}")
        for n in range(Database.MEMORY_PER_CHANNEL)
    ]
    await db.memory_delete(ROOM, written[0].id)
    await db.memory_supersede(ROOM, written[1].id, "fact number 1, corrected")

    assert await db.full_memory_channels() == []


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

    found = await db.memory_search(ROOM, "checkout", limit=8, kind=memory_kinds.VOICE)

    assert [m.text for m in found] == [
        "checkout also runs a batch job",
        "checkout runs on cluster b",
    ]


async def test_an_empty_query_returns_the_channels_recent_memories(db):
    """Not validated against: a model sending "" is asking to see what is
    there, and every word-count check passes vacuously on no words at all."""
    await db.memory_add(ROOM, "checkout runs on cluster b")
    await db.memory_add(ROOM, "they deploy on fridays")

    found = await db.memory_search(ROOM, "", limit=8, kind=memory_kinds.VOICE)

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

    first = (await db.memory_search(ROOM, "fact number 0", limit=1, kind=memory_kinds.VOICE))[0]
    await db.memory_delete(ROOM, first.id)

    assert await db.memory_add(ROOM, "now there is room again") is not None


async def test_the_store_enforces_the_same_text_length_the_tool_advertises(db):
    """`friday/kernel/tools/memory.py`'s `TEXT_CHARS` is what `memory_add` cuts to
    and what its docstring quotes to the model — but the store is what a
    second caller would actually reach, the same argument
    `MEMORY_PER_CHANNEL`'s own comment already makes for the count. A store
    that did not also cut would let a caller who skips the tool write
    anything, however long."""
    from friday.store.db import Database
    from friday.kernel.tools.memory import TEXT_CHARS

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
        await db.memory_search(ROOM, "anything", kind=memory_kinds.VOICE)


async def test_memory_add_records_the_source_message_id(db):
    """`FridayState.message_id` is the link the Rooms screen joins on to
    mark the source row with an enrichment glyph. The store records it
    on the row; a `None` source leaves the column `None`, and a non-`None`
    source comes back out."""
    written = await db.memory_add(ROOM_WITH_SOURCE, "they said this in the room")

    assert written.source_message_id == "9001"

    # The `None` case still works — a tool that did not name a source
    # message leaves the column `None`, and the join on the Rooms screen
    # does not produce a marker for that row.
    without = await db.memory_add(ROOM, "no source")
    assert without.source_message_id is None


# ---- ticket 10: kind and lifecycle ----------------------------------------


def test_the_reader_of_a_memory_follows_from_its_kind():
    """D14: the reader is a function of `kind`, not a second column — so
    there is nothing for a second field to disagree with. `reader_for`
    became `readers_for` on board `read-it-the-way-the-operator-does`
    (ticket 09), because `finding` has two readers; the whole table is
    pinned in `tests/test_memory_kinds.py`, and this keeps the extractor's
    half of it."""

    for kind in memory_kinds.domain_kinds():
        assert "extractor" in memory_kinds.readers_for(kind)
    assert memory_kinds.readers_for(memory_kinds.VOICE) == {"responder"}
    assert memory_kinds.domain_kinds() == {
        memory_kinds.FACT, memory_kinds.CONSTRAINT, memory_kinds.FINDING, memory_kinds.DECISION,
    }


def test_readers_for_refuses_a_kind_outside_the_closed_set():
    """`preference` was considered and rejected (D14): every preference in
    this domain is either voice or a constraint, and a kind that cannot be
    told apart from its neighbours is one a model will place at random."""
    import pytest


    with pytest.raises(ValueError):
        memory_kinds.readers_for("preference")


async def test_memory_add_defaults_to_voice_and_active(db):
    """`kind` defaults to `memory_kinds.VOICE` because the only wired producer
    today is the responder, which writes nothing else. `status` starts
    `active` regardless of kind."""
    written = await db.memory_add(ROOM, "they usually reply in Vietnamese")

    assert written.kind == memory_kinds.VOICE
    assert written.status == "active"
    assert written.superseded_by is None


async def test_memory_add_takes_the_kind_it_is_given(db):
    written = await db.memory_add(
        ROOM, "test.apero is staging", kind=memory_kinds.FACT
    )

    assert written.kind == memory_kinds.FACT


async def test_domain_memories_reads_the_four_domain_kinds(db):
    """`db.domain_memories` is the extractor's read path (D14, D21): the four
    domain kinds, newest first, and nothing voice-kind — that is the
    responder's alone."""
    await db.memory_add(ROOM, "test.apero is staging", kind=memory_kinds.FACT)
    await db.memory_add(ROOM, "never deploy on fridays", kind=memory_kinds.CONSTRAINT)
    await db.memory_add(
        ROOM, "the timeout was the proxy, not the api", kind=memory_kinds.FINDING,
        data={"task_id": 7, "service": "api", "confidence": 0.7},
    )
    await db.memory_add(ROOM, "moved to the new queue", kind=memory_kinds.DECISION)
    await db.memory_add(ROOM, "they like short replies", kind=memory_kinds.VOICE)

    found = await db.domain_memories(ROOM.channel_id)

    assert {m.text for m in found} == {
        "test.apero is staging",
        "never deploy on fridays",
        "the timeout was the proxy, not the api",
        "moved to the new queue",
    }
    assert all(m.kind != memory_kinds.VOICE for m in found)


async def test_domain_memories_does_not_leak_another_rooms(db):
    await db.memory_add(ROOM, "test.apero is staging", kind=memory_kinds.FACT)
    await db.memory_add(OTHER_ROOM, "prod.other is production", kind=memory_kinds.FACT)

    found = await db.domain_memories(ROOM.channel_id)

    assert [m.channel_id for m in found] == [ROOM.channel_id]


async def test_memory_search_only_returns_the_kind_it_is_asked_for(db):
    """The responder's tool always searches `memory_kinds.VOICE` (D14) — a
    domain-kind row, however it got written, must not surface in that
    search."""
    await db.memory_add(ROOM, "test.apero is staging", kind=memory_kinds.FACT)
    voice = await db.memory_add(ROOM, "they like short replies", kind=memory_kinds.VOICE)

    found = await db.memory_search(ROOM, "", kind=memory_kinds.VOICE, limit=8)

    assert [m.id for m in found] == [voice.id]


async def test_correcting_a_memory_leaves_its_kind_and_status_alone(db):
    written = await db.memory_add(ROOM, "test.apero is staging", kind=memory_kinds.FACT)

    updated = await db.memory_update(ROOM, written.id, "test.apero is dev, not staging")

    assert updated.kind == memory_kinds.FACT
    assert updated.status == "active"


async def test_superseding_a_memory_marks_the_old_one_and_writes_a_new_one(db):
    """D16: replacing what a memory claims is a different operation from
    correcting its wording. The old row survives, superseded, pointing at
    the new one; the new row carries the new claim, active, under the same
    kind."""
    old = await db.memory_add(ROOM, "the queue is rabbitmq", kind=memory_kinds.DECISION)

    new = await db.memory_supersede(ROOM, old.id, "the queue moved to kafka")

    assert new.text == "the queue moved to kafka"
    assert new.kind == memory_kinds.DECISION
    assert new.status == "active"

    everything = await db.memories_for_channel(ROOM.channel_id)
    (old_row,) = [m for m in everything if m.id == old.id]
    assert old_row.text == "the queue is rabbitmq", "the old claim is still visible"
    assert old_row.status == "superseded"
    assert old_row.superseded_by == new.id


async def test_a_superseded_memory_is_invisible_to_every_reader_that_serves_a_model(db):
    old = await db.memory_add(ROOM, "the queue is rabbitmq", kind=memory_kinds.DECISION)
    await db.memory_supersede(ROOM, old.id, "the queue moved to kafka")

    assert old.id not in {m.id for m in await db.domain_memories(ROOM.channel_id)}
    assert old.id not in {
        m.id for m in await db.memory_search(ROOM, "", kind=memory_kinds.DECISION, limit=8)
    }


async def test_a_superseded_memory_cannot_be_superseded_again_through_its_own_id(db):
    """"The current one" is the row a supersession points at — supersede
    that one instead of trying to reach the row it already replaced."""
    old = await db.memory_add(ROOM, "the queue is rabbitmq", kind=memory_kinds.DECISION)
    new = await db.memory_supersede(ROOM, old.id, "the queue moved to kafka")

    assert await db.memory_supersede(ROOM, old.id, "anything") is None

    again = await db.memory_supersede(ROOM, new.id, "the queue moved to nats")
    assert again is not None


async def test_a_superseded_memory_cannot_be_corrected_or_deleted_either(db):
    """Frozen history (D16): once replaced, a row is not the one to correct
    or retract — the row that replaced it is."""
    old = await db.memory_add(ROOM, "the queue is rabbitmq", kind=memory_kinds.DECISION)
    await db.memory_supersede(ROOM, old.id, "the queue moved to kafka")

    assert await db.memory_update(ROOM, old.id, "anything") is None
    assert await db.memory_delete(ROOM, old.id) is False


async def test_superseding_a_memory_in_another_room_fails_the_same_as_missing(db):
    theirs = await db.memory_add(OTHER_ROOM, "they deploy on fridays")

    assert await db.memory_supersede(ROOM, theirs.id, "anything") is None


async def test_superseding_is_never_refused_for_the_channels_cap(db):
    """An active row becomes inactive and a new active row is written in the
    same call, so the channel's active count does not move — unlike
    `memory_add`, this must never be refused for capacity."""
    from friday.store.db import Database

    ids = []
    for n in range(Database.MEMORY_PER_CHANNEL):
        written = await db.memory_add(ROOM, f"fact number {n}")
        ids.append(written.id)
    assert await db.memory_add(ROOM, "one more than the room can hold") is None

    assert await db.memory_supersede(ROOM, ids[0], "fact number 0, corrected") is not None

    # Still full: a supersession writes a new active row in the same call it
    # retires one, so the active count never dips — unlike `memory_delete`,
    # which frees a slot because nothing replaces the deleted row.
    assert await db.memory_add(ROOM, "still one more than the room can hold") is None


async def test_the_cap_counts_only_active_memories_not_every_superseded_generation(db):
    """A chain of supersessions leaves one active row behind many superseded
    ones — physically more rows than the cap, but the cap counts only what a
    model can currently read (D16), so a long-corrected fact does not make a
    room look full when it is not."""
    from friday.store.db import Database

    current = await db.memory_add(ROOM, "the queue is rabbitmq", kind=memory_kinds.DECISION)
    for n in range(Database.MEMORY_PER_CHANNEL):
        current = await db.memory_supersede(ROOM, current.id, f"the queue is generation {n}")

    assert await db.memory_add(ROOM, "well under the cap") is not None


async def test_two_writes_racing_for_the_last_slot_do_not_both_land(db):
    """D18's cap holds when two writes arrive at once, not only one by one.

    The pool works tasks side by side now (ticket 13), and a reaction marking
    a candidate right writes through here from the gateway while a responder
    run may be writing too — so the count and the insert cannot be two steps
    something else can land between.
    """
    import asyncio

    from friday.store.db import Database

    for n in range(Database.MEMORY_PER_CHANNEL - 1):
        await db.memory_add(ROOM, f"fact {n}")

    first, second = await asyncio.gather(
        db.memory_add(ROOM, "one more"), db.memory_add(ROOM, "and another")
    )

    assert [first is None, second is None].count(True) == 1
    live = await db.memory_search(ROOM, "", limit=1000, kind=memory_kinds.VOICE)
    assert len(live) == Database.MEMORY_PER_CHANNEL


# --- structured rows, read by code (ticket 00) ------------------------------


async def test_a_structured_row_comes_back_as_its_own_type(db):
    """`route`, `service` and `project` are read by code, never by a model,
    and what code wants is the typed object rather than a `data` dict every
    call site rebuilds."""
    from friday.sdk.memory import MemoryOrigin

    # The service comes first: ticket 19's check refuses a route naming one
    # that does not exist, which is the whole of why this order now matters.
    await _project(db, "p")
    await _service(db, "be", "p")
    await db.memory_add(
        ROOM, "ReelMe on dev", kind="backend.route", origin=MemoryOrigin.ADMIN,
        data={"domain": "api.dev.aperogroup.ai", "env": "dev", "service": "be"},
    )

    found = await db.structured_memory(
        "c1", kind="backend.route", key="api.dev.aperogroup.ai"
    )

    assert (found.env, found.service) == ("dev", "be")


async def test_this_rooms_row_wins_over_the_one_written_for_every_room(db):
    """Two rows can hold one key — that is what `'*'` is for — and the more
    specific is the one somebody wrote about this room on purpose."""
    from friday.sdk.memory import MemoryOrigin

    # A `'*'` row may only name another `'*'` row — ticket 19's third open
    # question, answered: "true everywhere" cannot depend on something that
    # exists in one room only, or it resolves for that room and nowhere else.
    everywhere = FridayState(channel_id="*", agent="admin")
    await _project(db, "p", channel="*")
    await _service(db, "shared", "p", channel="*")
    await _project(db, "p")
    await _service(db, "this-room", "p")
    for state, service in ((everywhere, "shared"), (ROOM, "this-room")):
        await db.memory_add(
            state, "route", kind="backend.route", origin=MemoryOrigin.ADMIN,
            data={"domain": "api.aperogroup.ai", "env": "production",
                  "service": service},
        )

    found = await db.structured_memory(
        "c1", kind="backend.route", key="api.aperogroup.ai"
    )

    assert found.service == "this-room"


async def test_a_prose_kind_has_no_structured_data_to_ask_for(db):
    import pytest

    with pytest.raises(ValueError, match="prose"):
        await db.structured_memory("c1", kind=memory_kinds.FACT, key="anything")


# --- a row that names another row (ticket 19) -------------------------------


async def _project(db, name: str, channel: str = "c1"):
    from friday.sdk.memory import MemoryOrigin

    return await db.memory_add(
        FridayState(channel_id=channel, agent="operator"), f"repo {name}",
        kind="backend.project", origin=MemoryOrigin.ADMIN,
        data={"name": name, "repo_path": f"~/{name}",
              "default_branch": "main", "stack": "NestJS"},
    )


async def _service(db, name: str, project: str, channel: str = "c1"):
    from friday.sdk.memory import MemoryOrigin

    return await db.memory_add(
        FridayState(channel_id=channel, agent="operator"), f"service {name}",
        kind="backend.service", origin=MemoryOrigin.ADMIN,
        data={
            "name": name, "project": project,
            "prod": {"cluster": "c", "namespace": "n", "app": name},
            "dev": {"kube_context": "d", "namespace": "dev",
                    "pod_pattern": f"{name}-"},
        },
    )


async def test_a_row_naming_a_row_that_does_not_exist_is_refused(db):
    """Ticket 19. `service.project` is matched against a `project` row's key
    by string equality, and the form asked for it as free text — a question
    whose wrong answers look exactly like its right ones. It failed on the
    first six rows ever typed."""
    from friday.kernel.domain.models import MemoryRefused

    with pytest.raises(MemoryRefused, match="reelme-v2"):
        await _service(db, "backend-reelme-v2", "reelme-v2")


async def test_the_same_row_is_accepted_once_what_it_names_exists(db):
    await _project(db, "reelme-v2")

    written = await _service(db, "backend-reelme-v2", "reelme-v2")

    assert written is not None


async def test_a_row_written_for_every_room_satisfies_one_room(db):
    """`'*'` is "true everywhere", and it is the scope `structured_memory`
    already reads by — so what the check accepts and what a lookup will find
    cannot be two different things."""
    await _project(db, "shared", channel="*")

    assert await _service(db, "backend", "shared") is not None


async def test_renaming_a_row_another_row_names_is_refused(db):
    """The second mismatch, twenty minutes after the first was fixed. A
    structured row's key *is* its data, so renaming it orphans every row that
    names it — and those rows are ones nobody touched. A dropdown cannot help
    here: the operator is editing the row being named."""
    from friday.kernel.domain.models import MemoryRefused

    project = await _project(db, "reelme-v2")
    await _service(db, "backend-reelme-v2", "reelme-v2")

    with pytest.raises(MemoryRefused, match="backend-reelme-v2"):
        await db.memory_update(
            FridayState(channel_id="c1", agent="operator"), project.id,
            text="repo", data={**project.data, "name": "renamed"},
            origin="admin",
        )


async def test_renaming_a_row_nobody_names_is_fine(db):
    project = await _project(db, "lonely")

    changed = await db.memory_update(
        FridayState(channel_id="c1", agent="operator"), project.id,
        text="repo", data={**project.data, "name": "renamed"}, origin="admin",
    )

    assert changed.key == "renamed"


async def test_editing_a_row_without_moving_its_key_is_fine(db):
    """Correcting a repo path must not require unpicking every service."""
    project = await _project(db, "reelme-v2")
    await _service(db, "backend-reelme-v2", "reelme-v2")

    changed = await db.memory_update(
        FridayState(channel_id="c1", agent="operator"), project.id,
        text="repo", data={**project.data, "repo_path": "~/moved"},
        origin="admin",
    )

    assert changed.data["repo_path"] == "~/moved"


async def test_deleting_a_row_another_row_names_is_refused(db):
    """Refused rather than allowed-and-handed-over later: the breakage would
    otherwise surface hours afterwards, inside a graph run, which is the
    failure this whole check exists to stop. A soft delete makes the
    work-around cheap — remove the service first."""
    from friday.kernel.domain.models import MemoryRefused

    project = await _project(db, "reelme-v2")
    await _service(db, "backend-reelme-v2", "reelme-v2")

    with pytest.raises(MemoryRefused, match="backend-reelme-v2"):
        await db.memory_delete(
            FridayState(channel_id="c1", agent="operator"), project.id,
            origin="admin",
        )


async def test_a_deleted_row_no_longer_holds_its_dependants_hostage(db):
    project = await _project(db, "reelme-v2")
    service = await _service(db, "backend-reelme-v2", "reelme-v2")
    await db.memory_delete(
        FridayState(channel_id="c1", agent="operator"), service.id,
        origin="admin",
    )

    assert await db.memory_delete(
        FridayState(channel_id="c1", agent="operator"), project.id,
        origin="admin",
    )


async def test_superseding_cannot_move_a_key_another_row_names(db):
    """The same hole as renaming, reached by a different door: this path
    takes `data` as well, so it can move a key. Found by review rather than
    by a test — the first version checked `memory_update` and `memory_delete`
    and left this one open."""
    from friday.kernel.domain.models import MemoryRefused

    project = await _project(db, "reelme-v2")
    await _service(db, "backend-reelme-v2", "reelme-v2")

    with pytest.raises(MemoryRefused, match="backend-reelme-v2"):
        await db.memory_supersede(
            FridayState(channel_id="c1", agent="operator"), project.id,
            "a better description", origin="admin",
            data={**project.data, "name": "renamed"},
        )


async def test_superseding_without_moving_the_key_is_still_allowed(db):
    """D16's whole point: a row is corrected by the row that replaces it."""
    project = await _project(db, "reelme-v2")
    await _service(db, "backend-reelme-v2", "reelme-v2")

    new = await db.memory_supersede(
        FridayState(channel_id="c1", agent="operator"), project.id,
        "a better description", origin="admin",
        data={**project.data, "repo_path": "~/moved"},
    )

    assert new.key == "reelme-v2" and new.data["repo_path"] == "~/moved"


async def test_a_shared_row_cannot_be_removed_while_one_room_names_it(db):
    """A `'*'` row is named from everywhere, so its dependants are searched
    everywhere. Scoped to `[channel_id, '*']` with `channel_id == '*'` that
    collapses to `'*'` alone, and a shared project could be removed while one
    room's service still named it. Found by review."""
    from friday.kernel.domain.models import MemoryRefused

    await _project(db, "shared", channel="*")
    await _service(db, "c1-service", "shared", channel="c1")
    shared = [
        m for m in await db.memories_for_channel("*")
        if m.kind == "backend.project"
    ][0]

    with pytest.raises(MemoryRefused, match="c1-service"):
        await db.memory_delete(
            FridayState(channel_id="*", agent="operator"), shared.id,
            origin="admin",
        )


async def test_a_row_for_every_room_may_not_name_one_room_s_row(db):
    """Ticket 19's third open question, answered in code rather than in a
    comment: "true everywhere" cannot depend on something that exists in one
    room, or it resolves for that room and nowhere else."""
    from friday.kernel.domain.models import MemoryRefused

    await _project(db, "only-here", channel="c1")

    with pytest.raises(MemoryRefused, match="only-here"):
        await _service(db, "shared-service", "only-here", channel="*")
