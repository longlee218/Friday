"""Ticket 25 — what the agent knows about this channel.

Two kinds of knowledge share one file: `derived`, machine-written and safe to
delete, and `overrides`, the operator's, which a rebuild must never touch.
"""

from __future__ import annotations

import pytest
from agents.testing import ScriptedModel, assistant_message

from friday.channel_context import ContextRebuilder, ContextStore
from friday.config import AgentConfig
from friday.conversation import ConversationId
from friday.liveness import Heartbeat
from friday.notes import Promotion
from friday.tasks import TaskState
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
