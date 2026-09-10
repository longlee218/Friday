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

from friday.domain.models import ApiIssueParams, Memory, MemoryKind
from friday.extraction.context import build_full_context
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
        db, None, channel_id=None, task_id=task.id, known=ApiIssueParams()
    )
    without_task = await build_full_context(
        db, None, channel_id=None, task_id=None, known=ApiIssueParams()
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
    from friday.domain.models import Memory, MemoryKind, MemoryScope

    await db.memory_add(
        MemoryScope(channel_id="watched", task_id=None, agent="responder"),
        "test.apero is staging",
        kind=MemoryKind.FACT,
    )

    with_room = await build_full_context(
        db, None, channel_id="watched", task_id=None, known=ApiIssueParams()
    )
    without_channel = await build_full_context(
        db, None, channel_id=None, task_id=None, known=ApiIssueParams()
    )

    assert any(m.text == "test.apero is staging" for m in with_room.domain_memories)
    assert without_channel.domain_memories == (), (
        "a room leaked into a call about no channel"
    )


async def test_voice_kind_memories_do_not_reach_the_extractor(db):
    """`VOICE` is the responder's alone (D14) — however it got written, it
    must not surface in what the extractor is shown."""
    from friday.domain.models import MemoryKind, MemoryScope

    scope = MemoryScope(channel_id="watched", task_id=None, agent="responder")
    await db.memory_add(scope, "test.apero is staging", kind=MemoryKind.FACT)
    await db.memory_add(scope, "they like short replies", kind=MemoryKind.VOICE)

    context = await build_full_context(
        db, None, channel_id="watched", task_id=None, known=ApiIssueParams()
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
        db, None, channel_id=None, task_id=task.id, known=ApiIssueParams()
    )

    assert context.asked == ("em gửi anh curl với",)


def test_the_prompt_is_byte_identical_gathered_or_assembled_by_hand():
    """The refactor's own guard (board `what-the-room-already-knows`, ticket
    15): `build_input(context)` renders exactly what the five-argument
    formula it replaced would have, for the same content — assembled here
    from the same section builders `friday.extraction.prompt` uses
    internally, so a rendering change that silently drifts from that
    formula turns this test red rather than only a prompt-cache regression
    nobody notices."""
    from dataclasses import fields as dataclass_fields

    from friday.agent.instruction_prompt import (
        assemble,
        memory,
        outstanding_questions,
        remembered_facts,
        room_facts,
        user_input,
    )
    from friday.extraction.prompt import build_input
    from friday.memory.channel_context import ChannelContext

    known = ApiIssueParams(environment="production")
    room = ChannelContext(
        channel_id="watched", base={}, derived={}, overrides={"env": "staging"}
    )
    now = datetime.now(UTC)
    memories = [
        Memory(
            id="m1", channel_id="watched", agent="extractor",
            text="test.apero is staging", kind=MemoryKind.FACT,
            created_at=now, updated_at=now,
        )
    ]
    asked = ("còn environment nào em?",)
    transcript = "API lỗi rồi"

    context = _context(
        transcript, ApiIssueParams, room=room, asked=asked, memories=memories,
        known=known,
    )

    schema_lines = []
    for f in dataclass_fields(ApiIssueParams):
        if getattr(known, f.name, None):
            continue
        doc = (f.metadata or {}).get("doc", f.name.replace("_", " "))
        schema_lines.append(f"- {f.name}: {doc}")
    schema = "\n".join(schema_lines) or "(no fields)"
    channel_body = "\n".join(filter(None, [room_facts(room), remembered_facts(memories)]))
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

    with caplog.at_level(logging.DEBUG, logger="friday.extraction.context"):
        await build_full_context(
            db, None, channel_id=None, task_id=task.id, known=ApiIssueParams()
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
        make_event(message_id="m1"), task.id, decision={"type": "api_issue"}
    )

    context = await build_full_context(
        db, None, channel_id=None, task_id=task.id, known=ApiIssueParams(),
        budget_tokens=5,
    )

    assert context.transcript_over_budget is True
    assert await db.compaction_ineffective_count(task.id) == 0, (
        "the builder recorded a write of its own"
    )
