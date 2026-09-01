"""Ticket 25 — what the agent knows about this channel.

Two kinds of knowledge share one file: `derived`, machine-written and safe to
delete, and `overrides`, the operator's, which a rebuild must never touch.
"""

from __future__ import annotations

import pytest
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message

from friday.memory.channel_context import ContextRebuilder, ContextStore
from friday.config import AgentConfig
from friday.domain.conversation import ConversationId
from friday.ops.liveness import Heartbeat
from friday.memory.notes import Promotion
from friday.domain.tasks import TaskState
from tests.conftest import make_event

SUMMARY_CONFIG = AgentConfig(
    name="summary", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", context_window=100, options={},
)


def test_the_operator_can_fill_in_a_channel_before_anything_is_learned(tmp_path):
    store = ContextStore(tmp_path)

    store.init_channel("100", overrides={"project": "checkout"})

    loaded = store.load("100")
    assert loaded.overrides == {"project": "checkout"}
    assert loaded.derived == {}


def test_init_refuses_to_clobber_an_existing_file(tmp_path):
    store = ContextStore(tmp_path)
    store.init_channel("100", overrides={"project": "checkout"})

    with pytest.raises(FileExistsError):
        store.init_channel("100", overrides={"project": "something else"})


def test_a_rebuild_never_touches_what_the_operator_wrote(tmp_path):
    store = ContextStore(tmp_path)
    store.init_channel("100", overrides={"project": "checkout"})

    store.rebuild_derived("100", {"learned": "the environment is usually staging"})

    loaded = store.load("100")
    assert loaded.overrides == {"project": "checkout"}
    assert loaded.derived == {"learned": "the environment is usually staging"}


def test_deleting_the_derived_part_loses_nothing_that_cannot_be_rebuilt(tmp_path):
    store = ContextStore(tmp_path)
    store.init_channel("100", overrides={"project": "checkout"})
    store.rebuild_derived("100", {"learned": "fact one"})

    # Simulate deleting the machine-written part by hand.
    store.rebuild_derived("100", {})
    assert store.load("100").derived == {}

    # A rebuild from the same source of truth restores it.
    store.rebuild_derived("100", {"learned": "fact one"})
    loaded = store.load("100")
    assert loaded.derived == {"learned": "fact one"}
    assert loaded.overrides == {"project": "checkout"}


def test_base_values_apply_everywhere_a_channel_overrides_them(tmp_path):
    (tmp_path / "base.yaml").write_text("tone: terse\n")
    store = ContextStore(tmp_path)
    store.init_channel("100", overrides={"tone": "verbose"})
    store.init_channel("200", overrides={})

    assert store.load("100").merged()["tone"] == "verbose"
    assert store.load("200").merged()["tone"] == "terse"


def test_a_file_that_cannot_be_parsed_is_reported_by_name_and_is_not_fatal(tmp_path):
    (tmp_path / "100.yaml").write_text("overrides: [unterminated\n")
    store = ContextStore(tmp_path)

    problems = store.validate_all()

    assert len(problems) == 1
    assert "100.yaml" in problems[0]
    # Degraded, not fatal: the channel just runs without what failed to parse.
    loaded = store.load("100")
    assert loaded.derived == {}
    assert loaded.overrides == {}


async def test_a_rebuild_happens_only_when_something_was_learned(db):
    calls = []

    class RecordingRebuilder:
        async def rebuild_all(self):
            calls.append(True)

    class Stub:
        def __init__(self, promoted):
            self._promoted = promoted

        async def run_once(self):
            return self._promoted

    heartbeat = Heartbeat(db=db, promotion=Stub(0), context_rebuilder=RecordingRebuilder())
    await heartbeat.promote()
    assert calls == []

    heartbeat = Heartbeat(db=db, promotion=Stub(1), context_rebuilder=RecordingRebuilder())
    await heartbeat.promote()
    assert calls == [True]


async def test_rebuild_all_writes_what_promotion_currently_believes(db, tmp_path):
    opened = await db.create_task(
        conversation=ConversationId("discord", "100"), type="api_issue",
        state=TaskState.PENDING, confidence=0.9, params={},
    )
    await db.approve_task(opened.id, by="longle_")
    await db.record_observation(task_id=opened.id, category="lesson", text="ask first")

    store = ContextStore(tmp_path)
    store.init_channel("100")
    promotion = Promotion(db=db)
    await promotion.run_once()

    rebuilder = ContextRebuilder(store=store, db=db, promotion=promotion)
    await rebuilder.rebuild_all()

    assert "ask first" in store.load("100").derived["learned"]


async def test_a_summary_is_written_only_once_the_conversation_is_large_enough(db, tmp_path):
    store = ContextStore(tmp_path)
    store.init_channel("100")
    promotion = Promotion(db=db)

    rebuilder = ContextRebuilder(
        store=store, db=db, promotion=promotion,
        summary_config=SUMMARY_CONFIG, summary_share=0.5,
        model=ScriptedModel([[assistant_message("short conversation about checkout")]]),
    )
    await rebuilder.rebuild_all()
    assert "summary" not in store.load("100").derived

    # 100-token window, 0.5 share: past ~200 characters is worth summarising.
    await db.record_message(make_event(
        provider="discord", channel_id="100", message_id="m1",
        text="x" * 300,
    ))

    await rebuilder.rebuild_all()
    assert store.load("100").derived["summary"] == "short conversation about checkout"


async def test_a_summary_covers_the_channels_threads_too(db, tmp_path):
    """A thread is its own conversation (`friday.conversation`), not part of
    its parent channel — reading by conversation id alone would silently drop
    every message inside one from the channel's summary."""
    store = ContextStore(tmp_path)
    store.init_channel("100")
    promotion = Promotion(db=db)

    rebuilder = ContextRebuilder(
        store=store, db=db, promotion=promotion,
        summary_config=SUMMARY_CONFIG, summary_share=0.5,
        model=ScriptedModel([[assistant_message("checkout is broken")]]),
    )
    await db.record_message(make_event(
        provider="discord", channel_id="100", thread_id="t1", message_id="m1",
        text="x" * 300,
    ))

    await rebuilder.rebuild_all()

    assert store.load("100").derived["summary"] == "checkout is broken"


# --- ticket 40: the room decides the register --------------------------------


class _Capture(Model):
    """Records the user turn it was given, answers nothing."""

    def __init__(self, into: list[str]) -> None:
        self._into = into

    async def get_response(self, system_instructions, input, *a, **kw):
        self._into.append(str(input))
        raise RuntimeError("captured; stopping")

    def stream_response(self, *a, **kw):
        raise NotImplementedError


def _room(tmp_path, channel="room-1", **overrides) -> ContextStore:
    store = ContextStore(tmp_path)
    store.init_channel(channel, overrides=overrides)
    return store.hold_all()


def test_the_store_is_read_once_and_then_held(tmp_path):
    """Reading per message is file I/O on the event loop. Reading once is how
    everything else the operator writes behaves."""
    store = _room(tmp_path, register="trang trọng")

    (tmp_path / "room-1.yaml").write_text("overrides: {register: changed on disk}")

    assert store.context("room-1").overrides["register"] == "trang trọng"


def test_a_channel_with_no_file_has_no_context(tmp_path):
    assert _room(tmp_path).context("some-other-room") is None


def test_the_rebuilder_refreshes_what_is_held(tmp_path):
    """The learned layer is the one part the operator does not write, so it
    must not wait for a restart."""
    store = _room(tmp_path)

    store.rebuild_derived("room-1", {"summary": "mostly payments"})

    assert store.context("room-1").derived == {"summary": "mostly payments"}


async def test_the_responder_writes_differently_in_a_different_room(tmp_path):
    """The whole ticket, at the seam a scripted model allows: the same ask in
    two rooms produces two different prompts."""
    from friday.responder import Responder

    store = ContextStore(tmp_path)
    store.init_channel("team", overrides={"register": "thân, anh/em, nói thẳng"})
    store.init_channel("client", overrides={"register": "trang trọng, xưng tôi/anh chị"})
    store.hold_all()

    prompts: list[str] = []

    responder = Responder(
        config=AgentConfig(name="r", api_key="k", base_url="http://x/v1", model="m"),
        model=_Capture(prompts),
        context_store=store,
    )
    for room in ("team", "client"):
        await responder.draft(asking="cho anh xin correlationId", channel_id=room)

    team, client = prompts
    assert "anh/em" in team and "anh/em" not in client
    assert "anh chị" in client and "anh chị" not in team


async def test_a_room_with_no_file_leaves_the_prompt_as_it_was(tmp_path):
    from friday.responder import Responder

    prompts: list[str] = []

    responder = Responder(
        config=AgentConfig(name="r", api_key="k", base_url="http://x/v1", model="m"),
        model=_Capture(prompts),
        context_store=_room(tmp_path),
    )
    await responder.draft(asking="cho anh xin correlationId", channel_id="unknown-room")

    assert "channel_" not in prompts[0]


async def test_a_named_person_reaches_the_prompt_beside_the_rooms_register(tmp_path):
    """`people:` is an exception written next to the rule it breaks, so reading
    one file tells you how to write in that room."""
    from friday.responder import Responder

    store = ContextStore(tmp_path)
    store.init_channel(
        "client",
        overrides={"register": "trang trọng", "people": {"dana": "thân, gọi em"}},
    )
    store.hold_all()
    prompts: list[str] = []
    responder = Responder(
        config=AgentConfig(name="r", api_key="k", base_url="http://x/v1", model="m"),
        model=_Capture(prompts),
        context_store=store,
    )

    await responder.draft(asking="cho anh xin correlationId", channel_id="client")

    assert "trang trọng" in prompts[0]
    assert "dana" in prompts[0] and "gọi em" in prompts[0]
