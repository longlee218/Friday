"""Board `a-window-on-the-whole-path`, ticket 05 — the page and the API agree.

The expensive failure for a read-only SPA is not a misplaced button; it is a
renamed field rendering `undefined` in silence. Nothing on the JavaScript side
catches that (D11: no frontend tests), and `/openapi.json` cannot either —
every route here is annotated `-> dict` and builds its body by hand, so the
schema says `{"type": "object", "additionalProperties": true}` and a generated
client would pin the *routes* and none of the *fields*.

So the guard lives here: call the real converters, read the keys they emit,
and fail when they stop matching what `web/src/api-types.ts` declares. It runs
in `uv run pytest -q` like everything else, and needs no second toolchain.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from conftest import captured, make_event
from friday.domain.conversation import ConversationId
from friday.domain.states import TaskState
from friday.memory.channel_context import ContextStore
from friday.ops.api import build_api
from friday.outbox import Kind

WATCHED = ConversationId("fake", "watched")
TYPES = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "api-types.ts"


def declared(interface: str) -> set[str]:
    """The field names one `export interface` block declares."""
    body = re.search(
        rf"export interface {interface} \{{(.*?)\n\}}", TYPES.read_text(), re.S
    )
    assert body, f"web/src/api-types.ts declares no interface {interface}"
    return set(re.findall(r"^\s{2}(\w+)[?]?:", body.group(1), re.M))


@pytest.fixture
def store(tmp_path) -> ContextStore:
    return ContextStore(tmp_path)


@pytest.fixture
def client(db, store):
    return TestClient(
        build_api(
            db=db,
            provider_status=lambda: "connected",
            context_store=store,
        )
    )


async def test_the_page_and_the_board_route_agree(client, inbox, provider, db):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    row = await db.queue_outbound(
        task_id=task.id, conversation=WATCHED, kind=Kind.ASK_FOR_DETAILS,
        sender="discord_user", text="which environment?",
    )
    await db.fail_outbound(row.id, "boom")

    board = client.get("/api/board").json()

    assert set(board) == declared("Board")
    # Nested, and the reason this line exists: the first version of the page
    # typed `counts` as `Record<string, number>` and rendered each value
    # directly. Two of its entries are breakdowns by state and one is a
    # timestamp, so the page crashed on load with React error #31 — caught in
    # a browser, not by this file, because comparing top-level key names says
    # nothing about what a value *is*.
    assert set(board["counts"]) == declared("Counts")
    assert isinstance(board["counts"]["tasks"], dict)
    assert isinstance(board["counts"]["outbound"], dict)
    assert set(board["messages"][0]) == declared("Message")
    assert set(board["tasks_by_state"]["pending"][0]) == declared("Task")
    assert set(board["failed"][0]) == declared("Outbound")


async def test_the_page_and_the_flow_route_agree(client, inbox, provider, db):
    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    await db.record_model_call(
        message_id="10", agent="triage", model="m", system_prompt="s",
        prompt="p", output="o", input_tokens=1, output_tokens=1,
    )
    await db.record_tool_call(
        task_id=task.id, agent="extractor", tool="ask_for_fields",
        arguments="{}", result="ok", failed=False,
    )
    await db.mark_triaged(
        make_event(message_id="10"), task.id,
        decision={"type": "api_issue", "confidence": 0.9, "params": {}},
    )

    flow = client.get("/api/messages/fake/10/flow").json()

    assert set(flow) == declared("Flow")
    assert set(flow["decision"]) == declared("Decision")
    assert set(flow["model_calls"][0]) == declared("ModelCall")
    assert set(flow["tool_calls"][0]) == declared("ToolCall")


def test_the_page_and_the_context_route_agree(client):
    client.post("/api/channels/c1/context")

    body = client.get("/api/channels/c1/context").json()

    assert set(body) == declared("ChannelContext")


async def test_the_summary_shown_beside_a_message_agrees(client, inbox, provider, db):
    """A message carries a four-field summary of the call behind it, and
    deliberately not the prompt — that is a separate request."""
    provider.emit(make_event(message_id="10"))
    await captured(inbox)
    await db.record_model_call(
        message_id="10", agent="triage", model="m", system_prompt="s",
        prompt="p", output="o", input_tokens=1, output_tokens=1,
    )

    (message,) = client.get("/api/board").json()["messages"]

    assert set(message["model_call"]) == declared("ModelCallSummary")


# --- serving the page (ticket 05) --------------------------------------------


def _built(tmp_path):
    """A believable `web/dist`: an index and one hashed asset."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    return tmp_path


def test_the_api_serves_alone_when_nobody_has_built_the_page(db, tmp_path, monkeypatch):
    """A fresh checkout has no `web/dist`, and so does every test run. The API
    has to be usable without it — that is what `serve_board.py` is."""
    from friday.ops import api as api_module

    monkeypatch.setattr(api_module, "PAGE", tmp_path / "absent")
    client = TestClient(build_api(db=db, provider_status=lambda: "x"))

    assert client.get("/api/board").status_code == 200
    assert client.get("/").status_code == 404


def test_a_built_page_is_served_under_the_api(db, tmp_path, monkeypatch):
    from friday.ops import api as api_module

    monkeypatch.setattr(api_module, "PAGE", _built(tmp_path))
    client = TestClient(build_api(db=db, provider_status=lambda: "x"))

    assert '<div id="root">' in client.get("/").text
    assert client.get("/assets/index-abc.js").status_code == 200


def test_a_route_the_browser_owns_falls_back_to_the_page(db, tmp_path, monkeypatch):
    """`/flow/discord/123` is the SPA's own route, not a file. Answering 404
    would break every link and every refresh."""
    from friday.ops import api as api_module

    monkeypatch.setattr(api_module, "PAGE", _built(tmp_path))
    client = TestClient(build_api(db=db, provider_status=lambda: "x"))

    assert '<div id="root">' in client.get("/flow/discord/123").text


def test_the_api_still_wins_over_the_page(db, tmp_path, monkeypatch):
    """The catch-all is mounted last, so `/api/...` matches first. Reversed,
    the page would swallow its own data source."""
    from friday.ops import api as api_module

    monkeypatch.setattr(api_module, "PAGE", _built(tmp_path))
    client = TestClient(build_api(db=db, provider_status=lambda: "x"))

    answer = client.get("/api/board")

    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/json")


async def test_the_days_spend_is_reachable_and_split_by_agent(client, db):
    """`spent_today` fed the budget ceiling and nothing could read it. The
    ceiling is unset by default on purpose — this is how an operator learns
    what to set it to."""
    for agent, tokens in (("triage", 10), ("responder", 100), ("triage", 5)):
        await db.record_model_call(
            message_id=None, agent=agent, model="m", system_prompt="s",
            prompt="p", output="o", input_tokens=tokens, output_tokens=0,
        )

    spend = client.get("/api/spend").json()

    assert spend["total"] == 115
    assert spend["by_agent"] == {"triage": 15, "responder": 100}


async def test_an_agent_that_spent_nothing_is_simply_absent(client, db):
    """Which agents exist is config.yaml's business — `extractor_<type>` is
    one per task type — so there is no list to enumerate against."""
    await db.record_model_call(
        message_id=None, agent="triage", model="m", system_prompt="s",
        prompt="p", output="o", input_tokens=1, output_tokens=1,
    )

    assert list(client.get("/api/spend").json()["by_agent"]) == ["triage"]
