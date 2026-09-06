"""Board `a-window-on-the-whole-path`, ticket 02 — a message's whole path.

The spine is a message, not a task (D5). A task-spined view loses triage —
when the classifier runs there is no task and the call carries only
`message_id` — and loses every `skip`, which is the case an operator most
wants to interrogate. So the two paths that are *not* the happy one get the
most tests here.
"""

from __future__ import annotations

from conftest import make_event

from friday.domain.conversation import ConversationId
from friday.domain.states import TaskState


async def _seen(db, message_id="m1", text="the api is down", **kw):
    event = make_event(message_id=message_id, text=text, **kw)
    await db.record_message(event)
    return event


async def test_a_message_nothing_has_looked_at_yet_is_still_a_path(db):
    """Queued and untriaged is a real state, not an absence — the flow shows
    the message and says nothing has happened to it."""
    await _seen(db)

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert flow is not None
    assert flow.message.text == "the api is down"
    assert flow.decision is None
    assert flow.task is None


async def test_a_message_that_does_not_exist_has_no_path(db):
    assert await db.flow_for(provider="fake", message_id="never") is None


async def test_a_skipped_message_is_a_complete_path_not_an_empty_one(db):
    """The whole reason the spine is a message: a skip opens no task, so a
    task-spined view would not show it at all — and "why did it ignore me" is
    the question this screen exists for."""
    event = await _seen(db, text="anyone want lunch")
    await db.mark_triaged(
        event, None, decision={"type": "skip", "confidence": 0.95, "params": {}}
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert flow.decision["type"] == "skip"
    assert flow.decision["confidence"] == 0.95
    assert flow.task is None
    assert flow.triaged_at is not None


async def test_a_message_the_prefilter_held_says_which_word_held_it(db):
    """No model call exists for this one, so without the reason the path just
    stops with no explanation — which reads as the pipeline losing it."""
    event = await _seen(db, text="lương tháng này chưa về")
    await db.mark_triaged(
        event,
        None,
        decision={
            "type": TaskState.NEEDS_HUMAN,
            "confidence": 0.0,
            "params": {"reason": "mentions 'lương' — not sent to the model"},
        },
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert "lương" in flow.decision["params"]["reason"]
    assert flow.model_calls == []


async def test_a_path_carries_the_calls_made_before_the_task_existed(db):
    """Triage runs when there is no task, so its call correlates by
    `message_id` alone. A task-spined view drops it."""
    event = await _seen(db)
    await db.record_model_call(
        message_id="m1",
        agent="triage",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=10,
        output_tokens=2,
    )
    await db.mark_triaged(
        event, None, decision={"type": "skip", "confidence": 0.9, "params": {}}
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert [c.agent for c in flow.model_calls] == ["triage"]


async def test_a_path_reaches_through_the_task_to_what_it_reached_for(db):
    event = await _seen(db)
    task = await db.create_task(
        conversation=event.conversation,
        type="api_issue",
        state=TaskState.PENDING,
        confidence=0.9,
        params={},
    )
    await db.mark_triaged(
        event, task.id, decision={"type": "api_issue", "confidence": 0.9, "params": {}}
    )
    await db.record_model_call(
        message_id=None,
        task_id=task.id,
        node="prepare",
        agent="extractor",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=5,
        output_tokens=1,
    )
    await db.record_tool_call(
        task_id=task.id,
        node="prepare",
        agent="extractor",
        tool="ask_for_fields",
        arguments='{"missing": ["correlation_id"]}',
        result="asked",
        failed=False,
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert flow.task.id == task.id
    assert [c.agent for c in flow.model_calls] == ["extractor"]
    assert [t.tool for t in flow.tool_calls] == ["ask_for_fields"]


async def test_a_path_ends_at_what_was_sent(db):
    event = await _seen(db)
    task = await db.create_task(
        conversation=event.conversation,
        type="api_issue",
        state=TaskState.PENDING,
        confidence=0.9,
        params={},
    )
    await db.mark_triaged(
        event, task.id, decision={"type": "api_issue", "confidence": 0.9, "params": {}}
    )
    await db.queue_outbound(
        task_id=task.id,
        conversation=event.conversation,
        kind="clarify",
        sender="user",
        text="which environment?",
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert [o.text for o in flow.outbound] == ["which environment?"]


async def test_the_turn_the_message_belonged_to_comes_with_it(db):
    """People send one thought in three messages. A path that shows only the
    mention shows a third of what triage actually read."""
    first = await _seen(db, message_id="m1", text="the api is down")
    await _seen(db, message_id="m2", text="specifically /v2/orders")

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert [m.text for m in flow.turn] == [
        "the api is down",
        "specifically /v2/orders",
    ]
    assert first.provider_message_id == "m1"


async def test_calls_from_before_and_after_the_task_read_as_one_sequence(db):
    """Triage's call correlates by message, the extractor's by task. They are
    one story and must not arrive as two lists a reader has to interleave."""
    event = await _seen(db)
    await db.record_model_call(
        message_id="m1",
        agent="triage",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=1,
        output_tokens=1,
    )
    task = await db.create_task(
        conversation=event.conversation,
        type="api_issue",
        state=TaskState.PENDING,
        confidence=0.9,
        params={},
    )
    await db.mark_triaged(
        event, task.id, decision={"type": "api_issue", "confidence": 0.9, "params": {}}
    )
    await db.record_model_call(
        message_id=None,
        task_id=task.id,
        node="prepare",
        agent="extractor",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=1,
        output_tokens=1,
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert [c.agent for c in flow.model_calls] == ["triage", "extractor"]


async def test_another_conversations_work_is_not_in_this_path(db):
    """The same guard every other reader here has. A task from another room
    joined by nothing but time would land in this list."""
    event = await _seen(db)
    other = await db.create_task(
        conversation=ConversationId.parse("fake:elsewhere"),
        type="api_issue",
        state=TaskState.PENDING,
        confidence=0.9,
        params={},
    )
    await db.record_model_call(
        message_id=None,
        task_id=other.id,
        agent="extractor",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=1,
        output_tokens=1,
    )
    await db.mark_triaged(
        event, None, decision={"type": "skip", "confidence": 0.9, "params": {}}
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert flow.model_calls == []
    assert flow.task is None


async def test_a_call_naming_both_a_message_and_its_task_is_listed_once(db):
    """The two correlation keys were read separately and concatenated, on the
    assumption that no call carries both. Nothing enforces that: `_About` in
    `friday/agent/harness.py` holds `message_id` and `task_id` independently
    and `Harness.run`'s docstring invites both — *"a caller supplies whichever
    it knows"*. Only triage passes one today, so the assumption holds by
    coincidence of the current call sites.

    The first extractor or graph node to pass both would have every call
    rendered twice and its tokens counted twice in the task screen's total.
    """
    event = await _seen(db)
    task = await db.create_task(
        conversation=event.conversation,
        type="api_issue",
        state=TaskState.PENDING,
        confidence=0.9,
        params={},
    )
    await db.mark_triaged(
        event, task.id, decision={"type": "api_issue", "confidence": 0.9, "params": {}}
    )
    await db.record_model_call(
        message_id="m1",
        task_id=task.id,
        node="prepare",
        agent="extractor",
        model="m",
        system_prompt="s",
        prompt="p",
        output="o",
        input_tokens=7,
        output_tokens=3,
    )
    await db.record_tool_call(
        message_id="m1",
        task_id=task.id,
        agent="extractor",
        tool="ask_for_fields",
        arguments="{}",
        result="asked",
        failed=False,
    )

    flow = await db.flow_for(provider="fake", message_id="m1")

    assert len(flow.model_calls) == 1, "a call with both keys was counted twice"
    assert len(flow.tool_calls) == 1, "a tool call with both keys was counted twice"
    assert sum(c.input_tokens + c.output_tokens for c in flow.model_calls) == 10


def test_there_is_still_only_one_provider_name():
    """A tripwire, not a rule — the thing it guards is in `db.py`.

    `flow_for` correlates model and tool calls by `message_id` alone: those
    tables carry no provider column, only `messages` has the composite key.
    That is honest while one platform exists, because a Discord snowflake
    does not collide with itself. The day a second provider name appears, two
    messages can share an id and each path will show the other's calls.

    Written as a test rather than a comment because this codebase's own
    convention is that the rules only written down are the ones that drifted.
    A note naming a trigger condition is worth exactly as much as the next
    person happening to read it; this fails on the day the condition arrives.
    """
    import ast
    import pathlib

    names = set()
    for path in pathlib.Path("friday/providers").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "name"
                        for t in stmt.targets
                    )
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    names.add(stmt.value.value)

    assert names == {"discord"}, (
        f"a second provider exists ({sorted(names)}), and `Database.flow_for` "
        "correlates model and tool calls by `message_id` with no provider "
        "beside it — see the known-limitation note on `_calls_about` in "
        "friday/store/db.py. Two messages can now share an id, and each "
        "flow will show the other's calls. Scoping them needs a column on "
        "`model_calls` and `tool_calls`, and a migration."
    )
