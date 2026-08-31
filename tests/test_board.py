"""Ticket 05 — the board.

A debug view, not a control panel. It displays; every action happens in
Discord. That is what lets it run without auth.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import captured, make_event
from friday.board import build_board
from friday.conversation import ConversationId
from friday.outbox import Kind
from friday.tasks import TaskState

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
