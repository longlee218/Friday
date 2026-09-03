"""Ticket 08 — what a step learned while working.

Written to a staging tier and nowhere else. Nothing reads it back into a prompt:
an agent that learns from its own unreviewed notes drifts, and the drift has no
floor. Promoting one is ticket 10's business, and only for work a human approved.
"""

from __future__ import annotations

import pytest

from friday.domain.conversation import ConversationId
from friday.memory.observations import Category
from friday.tools.remember import remember_tool
from friday.domain.states import TaskState

WATCHED = ConversationId("fake", "watched")


async def task(db):
    return await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={"summary": "s"},
    )


async def test_a_step_records_what_it_learned(db):
    opened = await task(db)
    remember = remember_tool(db, task_id=opened.id)

    await remember(category="fact", text="checkout runs on cluster b")

    (noted,) = await db.observations()
    assert noted.category == "fact"
    assert noted.text == "checkout runs on cluster b"


async def test_the_task_and_the_time_are_attached_not_supplied(db):
    """A model asked for a timestamp will invent one, and a model asked which
    task it is working on will sometimes be wrong."""
    opened = await task(db)

    await remember_tool(db, task_id=opened.id)(category="fact", text="x")

    (noted,) = await db.observations()
    assert noted.task_id == opened.id
    assert noted.created_at is not None


async def test_a_category_nobody_recognises_is_refused(db):
    """Returned as a result the model can act on, not raised: a tool that
    raises ends the run, and the step had more to do."""
    opened = await task(db)

    answer = await remember_tool(db, task_id=opened.id)(
        category="vibes", text="feels wrong"
    )

    assert "vibes" in answer
    assert await db.observations() == []


async def test_every_category_the_system_knows_is_accepted(db):
    opened = await task(db)
    remember = remember_tool(db, task_id=opened.id)

    for category in Category:
        await remember(category=category.value, text=f"a {category} note")

    assert len(await db.observations()) == len(Category)


async def test_observations_are_scoped_to_their_task(db):
    first, second = await task(db), await task(db)
    await remember_tool(db, task_id=first.id)(category="fact", text="mine")
    await remember_tool(db, task_id=second.id)(category="fact", text="theirs")

    assert [o.text for o in await db.observations(task_id=first.id)] == ["mine"]


async def test_only_two_things_read_them_and_neither_prompts_with_them(db):
    """The guarantee this ticket actually makes.

    The store answers for them and the promotion pass consumes them; nothing
    else may look. An agent given its own unreviewed notes as context drifts,
    and nothing about the output says so — which is why this is asserted rather
    than intended.
    """
    import subprocess

    allowed = {"friday/store/db.py", "friday/memory/notes.py"}
    hits = subprocess.run(
        ["grep", "-rln", r"\.observations(", "friday/"],
        capture_output=True, text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected reader: {set(hits) - allowed}"


async def test_a_step_can_be_given_it_as_a_tool(db):
    """The shape a planner uses: bound to its task, handed to the agent."""
    from agents import function_tool

    from friday.agent.harness import Harness
    from friday.config import AgentConfig

    opened = await task(db)
    tool = function_tool(remember_tool(db, task_id=opened.id), name_override="remember")

    run = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="u", model="m"),
        instructions="i",
        tools=[tool],
    )

    assert [t.name for t in run.agent.tools] == ["remember"]
