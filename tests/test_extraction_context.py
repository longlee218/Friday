"""Board `what-the-room-already-knows`, ticket 15: `build_full_context` is
the one place that reads the room, the domain-kind memories and the
outstanding questions for an extraction. These tests lived in
`tests/test_extraction.py` against `Extractor.would_ask` before D26 moved
the gathering out of the extractor and into this one function — asserted at
the same seam, the lookup, just reached through `build_full_context` now
rather than through a stub-harness-captured prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

from friday.kernel.domain.models import Memory
from plugins.backend.params import ApiIssueParams
from friday.kernel.extraction.context import build_full_context
from tests.test_extraction import _context
from tests.test_outbox import _asked, _opened_by
from tests.test_pool import make_task


async def test_the_extractor_itself_reads_what_it_already_asked(db):
    """The guard the prompt-level tests cannot be. Ticket 01 shipped with the
    equivalent hole: its end-to-end double built the prompt itself, so the
    lookup itself had no test and a mutation that made it ignore its store
    passed the suite. Asserted at the seam that does the lookup."""
    task = await make_task(db)
    await _opened_by(db, task)
    await _asked(db, task, "em gửi anh curl với", sent_message_id="out-1")

    with_task = await build_full_context(
        db, channel_id=None, task_id=task.id, known=ApiIssueParams()
    )
    without_task = await build_full_context(
        db, channel_id=None, task_id=None, known=ApiIssueParams()
    )

    assert with_task.asked == ("em gửi anh curl với",)
    assert without_task.asked == (), (
        "a question leaked into a call about no task"
    )


async def test_the_extractor_itself_reads_domain_kind_memories(db):
    """Board `what-the-room-already-knows`, ticket 10: the four domain kinds
    (D14) reach the extractor through `db.domain_memories`, the same seam
    `test_the_extractor_itself_reads_what_it_already_asked` proved for
    outstanding questions."""
    from friday.kernel.domain.models import Memory, FridayState

    await db.memory_add(
        FridayState(channel_id="watched", task_id=None, agent="responder"),
        "test.apero is staging",
        kind="fact",
    )

    with_room = await build_full_context(
        db, channel_id="watched", task_id=None, known=ApiIssueParams()
    )
    without_channel = await build_full_context(
        db, channel_id=None, task_id=None, known=ApiIssueParams()
    )

    assert any(m.text == "test.apero is staging" for m in with_room.domain_memories)
    assert without_channel.domain_memories == (), (
        "a room leaked into a call about no channel"
    )


async def test_voice_kind_memories_do_not_reach_the_extractor(db):
    """`VOICE` is the responder's alone (D14) — however it got written, it
    must not surface in what the extractor is shown."""
    from friday.kernel.domain.models import FridayState

    scope = FridayState(channel_id="watched", task_id=None, agent="responder")
    await db.memory_add(scope, "test.apero is staging", kind="fact")
    await db.memory_add(scope, "they like short replies", kind="voice")

    context = await build_full_context(
        db, channel_id="watched", task_id=None, known=ApiIssueParams()
    )

    texts = [m.text for m in context.domain_memories]
    assert "test.apero is staging" in texts
    assert "they like short replies" not in texts


async def test_the_outstanding_questions_cost_no_model_call(db):
    """Derived, not summarised. The whole value of this input is that it is a
    query over what was actually sent, so it cannot be wrong in an
    interesting way — and gathering it must never involve a model call."""
    task = await make_task(db)
    await _opened_by(db, task)
    await _asked(db, task, "em gửi anh curl với", sent_message_id="out-1")

    context = await build_full_context(
        db, channel_id=None, task_id=task.id, known=ApiIssueParams()
    )

    assert context.asked == ("em gửi anh curl với",)


def test_the_prompt_is_byte_identical_gathered_or_assembled_by_hand():
    """The refactor's own guard (board `what-the-room-already-knows`, ticket
    15): `build_input(context)` renders exactly what the five-argument
    formula it replaced would have, for the same content — assembled here
    from the same section builders `friday.kernel.extraction.prompt` uses
    internally, so a rendering change that silently drifts from that
    formula turns this test red rather than only a prompt-cache regression
    nobody notices."""
    from dataclasses import fields as dataclass_fields

    from friday.kernel.harness.instruction_prompt import (
        assemble,
        memory,
        outstanding_questions,
        room_facts,
        user_input,
    )
    from friday.kernel.harness.structured import describe
    from friday.kernel.extraction.prompt import build_input

    known = ApiIssueParams(environment="production")
    now = datetime.now(UTC)
    memories = [
        Memory(
            id="m1", channel_id="watched", agent="extractor",
            text="test.apero is staging", kind="fact",
            created_at=now, updated_at=now,
        ),
        Memory(
            id="m2", channel_id="*", agent="operator",
            text="env: staging", kind="fact",
            created_at=now, updated_at=now, origin="admin",
        ),
    ]
    asked = ("còn environment nào em?",)
    transcript = "API lỗi rồi"

    context = _context(
        transcript, ApiIssueParams, asked=asked, memories=memories,
        known=known,
    )

    # Built through `describe`, which is what `build_input` calls — the
    # point of this test is that the prompt is assembled from the same
    # pieces in the same order, not that the field list is rendered twice in
    # two places. It *was* rendered twice: this rebuilt `- name: doc` by
    # hand, and when `build_input` moved to `describe` (which also names
    # types) the two drifted, which is how this test earned its keep.
    schema = describe(ApiIssueParams, omit=known) or "(no fields)"
    channel_body = room_facts(memories)
    by_hand = (
        f"Fields:\n{schema}\n\n"
        + assemble(
            memory(
                conversation_body=outstanding_questions(asked), channel_body=channel_body
            )
        )
        + f"What they said:\n{user_input(transcript)}"
    )

    assert build_input(context) == by_hand


async def test_the_debug_log_never_carries_content(db, caplog):
    """D4: everything a gather module logs is counts, sizes and booleans —
    never the transcript, the room's facts, or a question's own text. The
    one line `build_full_context` logs is the guard; this asserts what it
    must never say, not merely that it says something."""
    import logging

    task = await make_task(db)
    await _opened_by(db, task)
    secret = "correlationId là abcdef01-2345-6789-abcd-ef0123456789, mật khẩu 12345"
    await _asked(db, task, secret, sent_message_id="out-1")

    with caplog.at_level(logging.DEBUG, logger="friday.kernel.extraction.context"):
        await build_full_context(
            db, channel_id=None, task_id=task.id, known=ApiIssueParams()
        )

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in logged, "the question's own text leaked into the log"
    assert "chars" in logged and "tokens" in logged, (
        "the log line lost the counts it exists to report"
    )


async def test_the_builder_never_writes_even_when_over_budget(db):
    """D6: recording an ineffective compaction is node 0's own write, made
    from `transcript_over_budget` after this returns — never the builder's.
    A single message far larger than the budget, run through the builder
    directly, must leave the compaction count exactly where it started."""
    from conftest import make_event
    from tests.test_pool import _said

    task = await make_task(db)
    huge = "@Lee " + ("API lỗi rồi rất là dài. " * 200)
    await _said(db, "m1", huge, secs=0, mention=True)
    await db.mark_triaged(
        make_event(message_id="m1"), task.id, decision={"type": "backend.trace_problem"}
    )

    context = await build_full_context(
        db, channel_id=None, task_id=task.id, known=ApiIssueParams(),
        budget_tokens=5,
    )

    assert context.transcript_over_budget is True
    assert await db.compaction_ineffective_count(task.id) == 0, (
        "the builder recorded a write of its own"
    )
