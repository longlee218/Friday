"""Ticket 10 — what survives a task.

An observation is a guess made under pressure. A note is something the system
will act on for months. What separates them is that a human approved the work
the guess came from — and, for the categories that need it, that more than one
approved task said the same thing.
"""

from __future__ import annotations

from friday.domain.conversation import ConversationId
from friday.memory.notes import Promotion
from friday.domain.states import TaskState

WATCHED = ConversationId("fake", "watched")


async def task(db, *, approved: bool):
    created = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    if approved:
        await db.approve_task(created.id, by="longle_")
    return created


async def note(db, *, approved, category="lesson", text="ask for the env first"):
    opened = await task(db, approved=approved)
    await db.record_observation(task_id=opened.id, category=category, text=text)
    return opened


async def test_an_approved_task_leaves_something_behind(db):
    await note(db, approved=True)

    await Promotion(db=db).run_once()

    assert [n.text for n in await Promotion(db=db).believed()] == [
        "ask for the env first"
    ]


async def test_a_task_nobody_approved_leaves_nothing(db):
    """A wrong guess made under pressure must not become a permanent belief."""
    await note(db, approved=False)

    await Promotion(db=db).run_once()

    assert await Promotion(db=db).believed() == []


async def test_a_fact_needs_saying_twice(db):
    """One observation of how a system works can simply be wrong. A lesson the
    operator already approved is a judgement, not a reading."""
    await note(db, approved=True, category="fact", text="checkout is on cluster b")

    await Promotion(db=db).run_once()
    assert await Promotion(db=db).believed() == []

    await note(db, approved=True, category="fact", text="checkout is on cluster b")
    await Promotion(db=db).run_once()

    assert [n.text for n in await Promotion(db=db).believed()] == [
        "checkout is on cluster b"
    ]


async def test_a_note_says_how_much_agrees_with_it(db):
    for _ in range(3):
        await note(db, approved=True, category="fact", text="checkout is on cluster b")

    await Promotion(db=db).run_once()

    (kept,) = await Promotion(db=db).believed()
    assert kept.support == 3


async def test_considering_an_observation_uses_it_up(db):
    """Otherwise every pass re-counts the same evidence and one observation
    corroborates itself into a belief."""
    await note(db, approved=True)

    await Promotion(db=db).run_once()
    await Promotion(db=db).run_once()

    assert len(await db.notes()) == 1
    assert await db.observations() == []


async def test_an_unapproved_observation_is_considered_and_dropped(db):
    """It is not evidence and it is never going to become evidence — leaving it
    staged means reconsidering it forever."""
    await note(db, approved=False)

    await Promotion(db=db).run_once()

    assert await db.observations() == []


async def test_the_notes_do_not_grow_without_bound(db):
    """The best supported survive a trim, not the oldest or the newest — the
    thing five approved tasks agreed on outranks one seen once."""
    for n in range(5):
        await note(db, approved=True, text=f"lesson {n}")
    for _ in range(3):
        await note(db, approved=True, text="lesson 4")

    await Promotion(db=db, keep=2).run_once()

    kept = [n.text for n in await db.notes()]
    assert len(kept) == 2
    assert kept[0] == "lesson 4"


async def test_the_rendered_block_is_stable_between_promotions(db):
    """It goes in the early part of a prompt, where a byte that moves costs a
    cache hit on every call after it."""
    await note(db, approved=True, text="b")
    await note(db, approved=True, text="a")
    await Promotion(db=db).run_once()

    first = await Promotion(db=db).render()
    await Promotion(db=db).run_once()

    assert await Promotion(db=db).render() == first
    assert first.index("a") < first.index("b")  # ordered, not insertion order


async def test_nothing_is_rendered_when_there_is_nothing_to_say(db):
    assert await Promotion(db=db).render() == ""


async def test_notes_reach_an_agent_through_its_instructions(db):
    """The early, stable part of a prompt — where a byte that moves costs a
    cache hit on everything after it. Built once, so a promotion takes effect
    on the next start rather than invalidating a warm cache mid-run."""
    from friday.config import AgentConfig
    from friday.agent.harness import Harness

    await note(db, approved=True, text="ask for the env first")
    await Promotion(db=db).run_once()

    run = Harness(
        config=AgentConfig(name="a", api_key="k", base_url="u", model="m"),
        instructions="You triage messages.",
        notes=await Promotion(db=db).render(),
    )

    assert run.agent.instructions.startswith("You triage messages.")
    assert "ask for the env first" in run.agent.instructions
