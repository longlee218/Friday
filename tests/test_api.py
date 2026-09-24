"""Ticket 17, second slice — the board's data over HTTP.

A frontend that is not written in Python has to be able to render this. What
crosses the wire is also what leaves the process, so it is the last place to
catch a credential.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import BoardClient, captured, make_event
from friday.ops.api import build_api
from friday.domain.conversation import ConversationId
from friday.outbox import Kind
from friday.domain.states import TaskState

WATCHED = ConversationId("fake", "watched")


@pytest.fixture
def client(db):
    return BoardClient(
        build_api(db=db, provider_status=lambda: "connected", origins=["http://x"])
    )


async def test_one_request_renders_the_whole_page(client, inbox, provider, db):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=TaskState.PENDING,
        confidence=0.9, params={"summary": "checkout 500"},
    )

    board = client.get("/api/board").json()

    assert board["status"] == "connected"
    assert board["counts"]["messages"] == 1
    assert board["tasks_by_state"]["pending"][0]["type"] == "devops.api_issue"
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
        conversation=WATCHED, type="devops.api_issue", state=TaskState.NEEDS_HUMAN,
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
        conversation=WATCHED, type="devops.api_issue", state=TaskState.NEEDS_HUMAN,
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


def test_binding_beyond_loopback_is_refused():
    """The board exposes every captured message and every model prompt, and it
    writes the operator's own memory. It has no authentication because it only
    ever answered on loopback — so binding wider does not happen. There is no
    credential that lifts this any more (`BOARD_TOKEN` is gone, ticket 02)."""
    from friday.ops.api import check_exposure

    check_exposure("127.0.0.1")
    check_exposure("localhost")

    with pytest.raises(SystemExit) as refused:
        check_exposure("0.0.0.0")
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


def test_a_container_may_no_longer_bind_its_own_network_uncredentialed(
    tmp_path, monkeypatch
):
    """This used to warn and continue, and the reason was sound while the
    board could only be read: inside a container loopback is unreachable from
    outside it, `0.0.0.0` means "this container", and who can reach *that* is
    the publish rule one layer out.

    It is not sound any more (board D10). One thing here writes — a channel's
    context `overrides`, which reaches the instructions of every agent in that
    room — so the question stopped being about disclosure and became one about
    control, and the old branch answered it by trusting a compose file this
    process cannot see.
    """
    from friday.ops import api

    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr(api, "_CONTAINER_MARKER", marker)

    with pytest.raises(SystemExit):
        api.check_exposure("0.0.0.0")


def test_loopback_is_the_supported_path():
    """The correct path is loopback — inside a container too, reached from
    outside over an SSH tunnel or a loopback port mapping. A guard that leaves
    no correct path is a guard somebody deletes, and this is that path. (The
    container marker only phrases the refusal; it does not gate loopback, so no
    marker is set here.)"""
    from friday.ops import api

    api.check_exposure("127.0.0.1")


def test_the_refusal_says_what_changed_and_what_to_do(tmp_path, monkeypatch):
    """Two failures this message exists to prevent: somebody reading it as the
    old read-only warning and ignoring it, and somebody finding no supported
    way forward and deleting the check. It no longer offers `BOARD_TOKEN`,
    which is gone."""
    from friday.ops import api

    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr(api, "_CONTAINER_MARKER", marker)

    with pytest.raises(SystemExit) as refused:
        api.check_exposure("0.0.0.0")

    said = str(refused.value)
    assert "write" in said, "does not say the process now accepts writes"
    assert "loopback" in said, "names no supported way forward"
    assert "BOARD_TOKEN" not in said, "still offers the credential that is gone"


def test_a_host_still_may_not(tmp_path, monkeypatch):
    from friday.ops import api

    monkeypatch.setattr(api, "_CONTAINER_MARKER", tmp_path / "absent")

    with pytest.raises(SystemExit, match="loopback"):
        api.check_exposure("0.0.0.0")


async def test_a_tasks_own_calls_are_reachable(client, db):
    """`message_id` answered "why was this classified that way". It cannot
    answer "what did this task cost, and what was the model asked while
    working on it" — an extractor runs on every pass against every message the
    reporter sent, and a responder answers a task."""
    task = await db.create_task(
        conversation=ConversationId("fake", "watched"), type="devops.api_issue",
        state=TaskState.PENDING, confidence=0.9, params={},
    )
    common = dict(model="m", system_prompt="s", output="o",
                  input_tokens=3, output_tokens=4)
    await db.record_model_call(
        task_id=task.id, node="prepare", agent="api_issue_extractor",
        prompt="lift the fields out", latency_ms=120, **common,
    )
    await db.record_model_call(agent="summary", prompt="somebody else's", **common)

    got = client.get(f"/api/tasks/{task.id}/model-calls").json()

    assert [c["agent"] for c in got] == ["api_issue_extractor"]
    assert got[0]["node"] == "prepare"
    assert got[0]["latency_ms"] == 120


async def test_calls_that_name_no_message_are_still_reachable(client, db):
    """Ticket 01 made the extractors, the responder and the summariser record,
    and every one of their rows has a NULL `message_id`. The only per-call
    route filtered by message, and `model_calls(message_id=None)` means "no
    filter" rather than "the uncorrelated ones" — so those prompts were stored
    and readable nowhere but the SQLite file. The complaint this board opened
    with was that the steps producing text a person reads had no record of
    what they were sent; storing it and not being able to look at it is the
    same complaint one step later."""
    common = dict(model="m", system_prompt="s", output="o",
                  input_tokens=1, output_tokens=1)
    await db.record_model_call(agent="summary", prompt="what this room is like", **common)
    await db.record_model_call(
        message_id="10", agent="triage", prompt="classify this", **common
    )

    got = client.get("/api/model-calls").json()

    assert [c["agent"] for c in got] == ["triage", "summary"], "newest first"
    assert client.get("/api/model-calls?uncorrelated=true").json()[0]["agent"] == "summary"


async def test_a_channels_memories_are_reachable_live_and_deleted(client, db):
    """`memory_delete` hides a line from every tool but keeps the row, and
    this is the one route that reads it back — the operator's own view, not
    scoped by agent the way the tool is."""
    from friday.domain.models import FridayState

    scope = FridayState(channel_id="100", task_id=None, agent="responder")
    kept = await db.memory_add(scope, "checkout runs on cluster b")
    gone = await db.memory_add(scope, "they deploy on fridays")
    await db.memory_delete(scope, gone.id)

    got = client.get("/api/channels/100/memories").json()

    by_id = {m["id"]: m for m in got}
    assert by_id[kept.id]["deleted_at"] is None
    assert by_id[gone.id]["deleted_by"] == "responder"
    assert by_id[gone.id]["deleted_at"] is not None


async def test_a_channels_memories_are_bounded_like_every_other_list_route(client, db):
    """Live memories stop at `MEMORY_PER_CHANNEL`, but a deleted row is never
    purged — a channel that has churned many corrections holds an unbounded
    number of rows, and every other list route on this board is bounded."""
    from friday.store.db import Database
    from friday.domain.models import FridayState

    scope = FridayState(channel_id="100", task_id=None, agent="responder")
    for n in range(5):
        written = await db.memory_add(scope, f"fact {n}")
        await db.memory_delete(scope, written.id)

    got = client.get("/api/channels/100/memories?limit=2").json()

    assert len(got) == 2


async def test_the_board_names_a_full_memory_channel(client, db):
    """Board `what-the-room-already-knows`, ticket 12, D18: the operator's
    own view of the ceiling, not only the model's refusal message."""
    from friday.domain.models import FridayState

    scope = FridayState(channel_id="100", task_id=None, agent="responder")
    for n in range(db.MEMORY_PER_CHANNEL):
        await db.memory_add(scope, f"fact number {n}")

    assert client.get("/api/board").json()["full_memory_channels"] == ["100"]


async def test_a_channels_candidates_are_reachable_pending_and_resolved(client, db):
    """Board `what-the-room-already-knows`, ticket 12: the "place for a
    person to look" a pending candidate needs, since no prompt or tool reads
    it — and a rejected one stays listed rather than deleted."""
    from friday.domain.models import CandidateStatus, FridayState

    scope = FridayState(
        channel_id="100", task_id=None, agent="responder", message_id="m1"
    )
    pending = await db.propose_memory(scope, "they usually reply in Vietnamese")
    other = FridayState(
        channel_id="100", task_id=None, agent="responder", message_id="m2"
    )
    rejected = await db.propose_memory(other, "they deploy on fridays")
    await db.resolve_candidates_for_message(
        provider_message_id="m2", mark="wrong", by="lee"
    )

    got = client.get("/api/channels/100/candidates").json()

    by_id = {c["id"]: c for c in got}
    assert by_id[pending.id]["status"] == CandidateStatus.PENDING
    assert by_id[rejected.id]["status"] == CandidateStatus.REJECTED


async def test_a_channels_candidates_do_not_leak_another_ones(client, db):
    from friday.domain.models import FridayState

    await db.propose_memory(
        FridayState(channel_id="200", task_id=None, agent="responder"), "not this room"
    )

    assert client.get("/api/channels/100/candidates").json() == []


# --- a message's whole path (board `a-window-on-the-whole-path`, ticket 02) ---


async def test_a_messages_whole_path_arrives_in_one_request(client, inbox, provider, db):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    task = await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    await db.record_model_call(
        message_id="10", agent="triage", model="m", system_prompt="s",
        prompt="p", output="o", input_tokens=10, output_tokens=2,
    )
    await db.record_tool_call(
        task_id=task.id, node="prepare", agent="extractor", tool="memory_search",
        arguments='{"missing": []}', result="asked", failed=False,
    )
    await db.mark_triaged(
        make_event(message_id="10"), task.id,
        decision={"type": "devops.api_issue", "confidence": 0.9, "params": {}},
    )

    flow = client.get("/api/messages/fake/10/flow").json()

    assert flow["message"]["text"] == "checkout is 500ing"
    assert flow["decision"]["type"] == "devops.api_issue"
    assert flow["task"]["id"] == task.id
    assert [c["agent"] for c in flow["model_calls"]] == ["triage"]
    assert [t["tool"] for t in flow["tool_calls"]] == ["memory_search"]


async def test_a_skipped_message_still_has_a_path(client, inbox, provider, db):
    """200 with no task, not a 404. "Why did it ignore this" is the question
    this route exists for, and a skip is its most common answer."""
    provider.emit(make_event(message_id="11", text="anyone want lunch"))
    await captured(inbox)
    await db.mark_triaged(
        make_event(message_id="11"), None,
        decision={"type": "skip", "confidence": 0.95, "params": {}},
    )

    flow = client.get("/api/messages/fake/11/flow").json()

    assert flow["decision"]["type"] == "skip"
    assert flow["task"] is None


async def test_an_untriaged_message_is_a_state_not_a_missing_path(client, inbox, provider):
    provider.emit(make_event(message_id="12"))
    await captured(inbox)

    flow = client.get("/api/messages/fake/12/flow").json()

    assert flow["decision"] is None
    assert flow["task"] is None


async def test_a_message_that_never_existed_is_a_404(client):
    assert client.get("/api/messages/fake/nope/flow").status_code == 404


async def test_a_credential_does_not_cross_the_wire_on_a_path(client, inbox, provider, db):
    provider.emit(make_event(message_id="13", text="it broke"))
    await captured(inbox)
    await db.record_model_call(
        message_id="13", agent="triage", model="m", system_prompt="s",
        prompt="the user said: token is sk-abcdefghijklmnopqrstuvwx",
        output="o", input_tokens=1, output_tokens=1,
    )

    body = client.get("/api/messages/fake/13/flow").text

    assert "sk-abcdefghijklmnopqrstuvwx" not in body


# --- naming a room (rooms redesign) ------------------------------------------


async def test_a_conversation_can_be_given_a_name(client, inbox, provider, db):
    """A Discord channel id is nineteen digits and means nothing to anybody.
    Nothing in this system holds a channel *name* — the provider is never
    asked for one — so the operator supplies it, and it is theirs: a label
    for their own work, not something the agent reads."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    saved = client.put(
        f"/api/conversations/{WATCHED}/name", json={"name": "backend on-call"}
    )

    assert saved.status_code == 200
    rooms = client.get("/api/conversations").json()
    assert rooms[0]["name"] == "backend on-call"
    assert rooms[0]["id"] == str(WATCHED)


async def test_a_room_with_no_name_says_so_rather_than_inventing_one(
    client, inbox, provider
):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    (room,) = client.get("/api/conversations").json()

    assert room["name"] is None


async def test_a_name_can_be_taken_back(client, inbox, provider):
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    client.put(f"/api/conversations/{WATCHED}/name", json={"name": "temporary"})

    client.put(f"/api/conversations/{WATCHED}/name", json={"name": ""})

    (room,) = client.get("/api/conversations").json()
    assert room["name"] is None, "an empty name is no name, not the string ''"


async def test_naming_a_conversation_nobody_has_spoken_in_is_refused(client):
    answer = client.put("/api/conversations/fake:ghost/name", json={"name": "x"})

    assert answer.status_code == 404


async def test_a_room_carries_what_a_list_of_rooms_needs(client, inbox, provider, db):
    """One request for what the left-hand list renders: which rooms exist,
    what they are called, how busy, and how stale. Without the counts the
    page fetches every room's messages to render a sidebar."""
    for n in (10, 11):
        provider.emit(make_event(message_id=str(n)))
    await captured(inbox)

    (room,) = client.get("/api/conversations").json()

    assert room["messages"] == 2
    assert room["last_at"] is not None
    assert room["channel_id"] == "watched"


async def test_a_room_that_has_spoken_is_listed_even_without_a_conversation_row(
    client, db
):
    """`rooms()` joined `conversations`, which only `record_conversation`
    writes — so a room whose row was never written vanished from the list
    while its messages sat plainly in the table.

    A list of rooms that silently omits one with twenty-one messages in it is
    the same shape as a dropped mention: indistinguishable from there being
    no such room. Driven from `messages` now, with the name joined on."""
    await db.record_message(make_event(message_id="10", text="hello"))

    (room,) = client.get("/api/conversations").json()

    assert room["messages"] == 1
    assert room["name"] is None


async def test_naming_works_for_a_room_with_no_conversation_row_yet(client, db):
    """And naming it has to work too, or the list shows a room the operator
    cannot label."""
    await db.record_message(make_event(message_id="10"))

    saved = client.put(f"/api/conversations/{WATCHED}/name", json={"name": "ops"})

    assert saved.status_code == 200
    assert client.get("/api/conversations").json()[0]["name"] == "ops"


async def test_each_message_says_which_task_it_belongs_to(client, inbox, provider, db):
    """The board used to pick a task card's text by *room*: the first message
    in the task's conversation. Two tasks in one room with no summary — the
    operator's two `doc_question`s on 2026-09-18 — then showed the same
    message and opened the same flow, which read as one task listed twice.
    A card can only find its own message if each message says whose it is."""
    provider.emit(make_event(message_id="10", text="where is the POD type?"))
    provider.emit(make_event(message_id="11", text="and the homepage one?"))
    provider.emit(make_event(message_id="12", text="just chatting"))
    await captured(inbox)
    first = await db.create_task(
        conversation=WATCHED, type="doc_question", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    second = await db.create_task(
        conversation=WATCHED, type="doc_question", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    await db.mark_triaged(make_event(message_id="10"), task_id=first.id)
    await db.mark_triaged(make_event(message_id="11"), task_id=second.id)

    by_id = {m["provider_message_id"]: m for m in client.get("/api/board").json()["messages"]}

    assert by_id["10"]["task_id"] == first.id
    assert by_id["11"]["task_id"] == second.id
    assert by_id["12"]["task_id"] is None


async def test_a_task_carries_its_opening_message_past_the_boards_window(
    client, inbox, provider, db
):
    """The board loads the newest 25 messages and up to 200 tasks, so a card
    cannot find its task's opening message among the loaded ones — the
    reviewer found task #1's own at position 28, and the card opening
    position 23 instead. The server says which message opened each task."""
    # Distinct times, oldest first: `make_event`'s default gives every message
    # one timestamp, which made "earliest" a tie SQLite settled by row order —
    # a descending sort passed this test too (second review, 2026-09-18).
    start = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    provider.emit(make_event(message_id="1", text="api tạo mới user bị 500", created_at=start))
    for n in range(2, 32):
        provider.emit(make_event(
            message_id=str(n), text=f"later {n}", created_at=start + timedelta(minutes=n),
        ))
    await captured(inbox)
    old = await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    new = await db.create_task(
        conversation=WATCHED, type="doc_question", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    bare = await db.create_task(
        conversation=WATCHED, type="doc_question", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    await db.mark_triaged(make_event(message_id="1"), task_id=old.id)
    await db.mark_triaged(make_event(message_id="5"), task_id=old.id)
    await db.mark_triaged(make_event(message_id="31"), task_id=new.id)

    board = client.get("/api/board").json()
    assert "1" not in {m["provider_message_id"] for m in board["messages"]}
    by_id = {t["id"]: t for t in board["tasks_by_state"]["pending"]}

    assert by_id[old.id]["opening"] == {
        "provider": "fake", "provider_message_id": "1", "text": "api tạo mới user bị 500",
    }
    assert by_id[new.id]["opening"]["provider_message_id"] == "31"
    assert by_id[bare.id]["opening"] is None
    (listed,) = [t for t in client.get("/api/tasks").json() if t["id"] == old.id]
    assert listed["opening"]["provider_message_id"] == "1"
