"""Ticket 17, second slice — the board's data over HTTP.

A frontend that is not written in Python has to be able to render this. What
crosses the wire is also what leaves the process, so it is the last place to
catch a credential.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import captured, make_event
from friday.ops.api import build_api
from friday.domain.conversation import ConversationId
from friday.outbox import Kind
from friday.domain.tasks import TaskState

WATCHED = ConversationId("fake", "watched")


@pytest.fixture
def client(db):
    return TestClient(
        build_api(db=db, provider_status=lambda: "connected", origins=["http://x"])
    )


async def test_one_request_renders_the_whole_page(client, inbox, provider, db):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={"summary": "checkout 500"},
    )

    board = client.get("/api/board").json()

    assert board["status"] == "connected"
    assert board["counts"]["messages"] == 1
    assert board["tasks_by_state"]["pending"][0]["type"] == "api_issue"
    assert board["messages"][0]["text"] == "checkout is 500ing"
    assert board["failed"] == []


async def test_a_prompt_is_never_in_a_list(client, inbox, provider, db):
    """Prompts are large and carry whatever a stranger pasted into Discord.
    They are fetched for one message, on request, never in a feed."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await db.record_model_call(
        message_id="10", agent="triage", model="MiniMax-M3",
        system_prompt="you triage", prompt="classify this", output="tool(...)",
        input_tokens=10, output_tokens=2,
    )

    (message,) = client.get("/api/board").json()["messages"]
    assert message["model_call"]["model"] == "MiniMax-M3"
    assert "prompt" not in message["model_call"]

    (call,) = client.get("/api/messages/fake/10/model-calls").json()
    assert call["prompt"] == "classify this"


async def test_the_feed_pages_and_the_page_size_is_capped(client, inbox, provider, db):
    for n in range(1, 6):
        provider.emit(make_event(message_id=str(n), text=f"message {n}"))
    await captured(inbox)

    page = client.get("/api/messages", params={"limit": 2}).json()
    assert [m["text"] for m in page] == ["message 5", "message 4"]

    nxt = client.get("/api/messages", params={"limit": 2, "before": "4"}).json()
    assert [m["text"] for m in nxt] == ["message 3", "message 2"]

    assert client.get("/api/messages", params={"limit": 10_000}).status_code == 422


async def test_a_credential_does_not_cross_the_wire(client, db):
    """`last_error` is scrubbed on the way in; this is the belt to that
    braces, because task params are model-extracted from a stranger's text."""
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.NEEDS_HUMAN,
        confidence=0.9, params={"summary": "token is sk-abcdefghijklmnopqrstuvwx"},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    await db.fail_outbound(row.id, "boom")

    body = client.get("/api/board").text

    assert "sk-abcdefghijklmnopqrstuvwx" not in body


async def test_a_failed_send_carries_its_text_and_its_error(client, db):
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.NEEDS_HUMAN,
        confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment are you on?",
    )
    await db.fail_outbound(row.id, "discord said no")

    (failed,) = client.get("/api/board").json()["failed"]

    assert failed["text"] == "which environment are you on?"
    assert failed["last_error"] == "discord said no"


def test_only_the_allowed_origin_may_read_it(client):
    allowed = client.get("/api/board", headers={"Origin": "http://x"})
    assert allowed.headers["access-control-allow-origin"] == "http://x"

    other = client.get("/api/board", headers={"Origin": "http://evil"})
    assert "access-control-allow-origin" not in other.headers


def test_a_typescript_client_can_be_generated_from_it(client):
    schema = client.get("/openapi.json").json()

    assert "/api/board" in schema["paths"]


def test_binding_beyond_loopback_without_a_credential_is_refused():
    """The board exposes every captured message and every model prompt. It has
    no authentication because it only ever answered on loopback — so binding
    wider has to bring one, or not happen."""
    from friday.ops.api import check_exposure

    check_exposure("127.0.0.1", token=None)
    check_exposure("0.0.0.0", token="a-real-token")

    with pytest.raises(SystemExit) as refused:
        check_exposure("0.0.0.0", token=None)
    assert "loopback" in str(refused.value)


def test_a_taken_port_is_reported_in_one_line():
    """Uvicorn calls sys.exit inside a TaskGroup task for this, which unwinds
    as sixty lines of traceback ending in `SystemExit: 3`. The one fact that
    matters — something else is already on the port — is buried in it."""
    import socket

    from friday.ops.api import bind

    held = bind("127.0.0.1", 0)
    port = held.getsockname()[1]

    with pytest.raises(SystemExit) as refused:
        bind("127.0.0.1", port)

    assert str(port) in str(refused.value)
    assert "already" in str(refused.value)
    held.close()


def test_binding_hands_back_a_listening_socket():
    """Bound before the server starts, so the failure happens where it can be
    reported rather than deep inside uvicorn's startup."""
    from friday.ops.api import bind

    sock = bind("127.0.0.1", 0)
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
    finally:
        sock.close()


def test_a_container_may_bind_its_own_network(tmp_path, monkeypatch):
    """Inside a container, loopback is unreachable from outside it — the port
    mapping never arrives. `0.0.0.0` there means "this container", and what
    restricts who can reach it is the publish rule, one layer out."""
    from friday.ops import api

    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr(api, "_CONTAINER_MARKER", marker)

    api.check_exposure("0.0.0.0", token=None)


def test_a_host_still_may_not(tmp_path, monkeypatch):
    from friday.ops import api

    monkeypatch.setattr(api, "_CONTAINER_MARKER", tmp_path / "absent")

    with pytest.raises(SystemExit, match="loopback"):
        api.check_exposure("0.0.0.0", token=None)
