"""Ticket 05 — the board.

A debug view, not a control panel. It displays; every action happens in
Discord. That is what lets it run without auth.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import captured, make_event
from friday.board import build_board
from friday.domain.conversation import ConversationId
from friday.outbox import Kind
from friday.domain.states import TaskState

WATCHED = ConversationId("fake", "watched")


@pytest.fixture
def client(db):
    return TestClient(build_board(db=db, provider_status=lambda: "connected"))


async def seed(db, state=TaskState.PENDING):
    return await db.create_task(
        conversation=WATCHED, type="api_issue", state=state,
        confidence=0.9, params={"summary": "checkout 500"},
    )


async def test_tasks_are_grouped_by_state(client, db):
    await seed(db, TaskState.PENDING)
    await seed(db, TaskState.NEEDS_HUMAN)

    page = client.get("/").text

    assert "pending" in page and "needs_human" in page
    assert page.count("checkout 500") == 2


async def test_messages_and_the_calls_made_about_them_are_shown(
    client, inbox, provider, db
):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    await db.record_model_call(
        message_id="10", agent="triage", model="m",
        system_prompt="you triage", prompt="classify this",
        output="create_api_issue_task({...})", input_tokens=10, output_tokens=2,
    )

    page = client.get("/").text

    assert "checkout is 500ing" in page
    assert "create_api_issue_task" in page


async def test_a_message_that_could_not_be_sent_shows_its_text_to_copy(client, db):
    """The whole point of surfacing it: it gets sent by hand."""
    task = await seed(db, TaskState.NEEDS_HUMAN)
    row = await db.queue_outbound(
        task_id=task.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment are you on?",
    )
    await db.fail_outbound(row.id, "discord said no")

    page = client.get("/").text

    assert "which environment are you on?" in page
    assert "discord said no" in page


async def test_connection_health_and_the_last_event_are_shown(
    client, inbox, provider, db
):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    page = client.get("/").text

    assert "connected" in page


async def test_the_page_refreshes_itself(client, db):
    assert "hx-trigger" in client.get("/").text


def test_the_page_offers_no_way_to_change_anything(client):
    page = client.get("/").text

    assert "<form" not in page
    assert "<button" not in page
    assert client.post("/").status_code == 405


async def test_a_messages_call_survives_a_crowd_of_unlinked_ones(
    client, db, provider, inbox
):
    """The board's window is shared, and it did not used to be.

    It maps calls to messages from one page of `model_calls`, and until every
    agent started recording, every row in that page was triage's and carried a
    message id. Now the extractors, the responder and the summariser write
    there too, and none of them is about a single message — so a page of the
    most recent calls can be entirely rows that can never match anything,
    while the call that actually classified the message sits just outside it.

    Asking for the calls that belong to these messages is the fix, and it is
    also what the board meant all along.
    """
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)

    await db.record_model_call(
        message_id="10", agent="triage", model="m",
        system_prompt="s", prompt="why it was classified", output="classify(...)",
        input_tokens=1, output_tokens=1,
    )
    for _ in range(250):
        await db.record_model_call(
            agent="api_issue_extractor", model="m",
            system_prompt="s", prompt="lift the fields out", output="{}",
            input_tokens=1, output_tokens=1,
        )

    page = client.get("/").text

    assert "why it was classified" in page


async def test_a_tasks_calls_are_shown_under_it(client, db):
    """The board shows what a task cost and what the model was asked while
    working on it. Until every agent recorded there was nothing to show; now
    the rows exist and the page is where an operator would look for them."""
    task = await seed(db)
    await db.record_model_call(
        task_id=task.id, node="prepare", agent="api_issue_extractor", model="m",
        system_prompt="s", prompt="lift the fields out", output="{}",
        input_tokens=7, output_tokens=2, latency_ms=120,
    )

    page = client.get("/").text

    assert "lift the fields out" in page
    assert "api_issue_extractor" in page


async def test_what_a_task_reached_for_is_shown_beside_what_it_was_asked(client, db):
    """A prompt and the tools it led to are read together or not at all: the
    question an operator has is "why did it do that", and half the answer is
    what the model was told and half is what it then went and looked up."""
    task = await seed(db)
    await db.record_model_call(
        task_id=task.id, agent="responder", model="m", system_prompt="s",
        prompt="write it in their voice", output="ok", input_tokens=1,
        output_tokens=1,
    )
    await db.record_tool_call(
        task_id=task.id, agent="responder", tool="search_skills",
        arguments='{"query": "rolling out"}', result="deploy: release a build",
        failed=False, latency_ms=3,
    )

    page = client.get("/").text

    assert "search_skills" in page
    assert "rolling out" in page
