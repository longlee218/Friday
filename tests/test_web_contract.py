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
from friday.ops.api import build_api, servable
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
def client(db):
    return TestClient(
        build_api(
            db=db,
            provider_status=lambda: "connected",
            confidence_threshold=0.7,
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
        task_id=task.id, agent="extractor", tool="memory_search",
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


def test_the_page_declares_no_context_file_any_more():
    """The route went with the YAML files (ticket 10); a type the page still
    declared for it would be a shape nothing serves."""
    with pytest.raises(AssertionError):
        declared("ChannelContext")


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
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    """Which agents exist is config.yaml's business, so there is no list to
    enumerate against."""
    await db.record_model_call(
        message_id=None, agent="triage", model="m", system_prompt="s",
        prompt="p", output="o", input_tokens=1, output_tokens=1,
    )

    assert list(client.get("/api/spend").json()["by_agent"]) == ["triage"]


# --- the catch-all does not hand out the filesystem --------------------------


def _repo(tmp_path):
    """The real layout: `PAGE` is `<repo>/web/dist`, so a credential file is
    exactly two `..` away. Every secret here is a decoy with a real file
    behind it — a parametrised case whose target does not exist passes
    whether or not the guard is present, which is how the first version of
    this test was vacuous for five of its six cases."""
    page = _built(tmp_path / "repo" / "web" / "dist")
    (tmp_path / "repo" / ".env").write_text("DISCORD_USER_TOKEN=not-a-real-token")
    (tmp_path / "repo" / "config.yaml").write_text("database_path: ./data/friday.db")
    (tmp_path / "repo" / "data").mkdir()
    (tmp_path / "repo" / "data" / "friday.db").write_text("SQLite format 3")
    (tmp_path / "outside.txt").write_text("not even in the repo")
    return page


@pytest.mark.parametrize(
    "attempt",
    [
        "../../.env",
        "../../config.yaml",
        "../../data/friday.db",
        "assets/../../../.env",
        "../../../outside.txt",
        "./../../.env",
    ],
)
def test_the_page_route_refuses_to_walk_out_of_its_directory(tmp_path, attempt):
    """`FileResponse` streams bytes and never touches `_clean`, so the one
    route that bypasses scrubbing must not reach a credential file. `PAGE` is
    `<repo>/web/dist`, which puts `.env`, `config.yaml` and the whole task
    database two `..` away — and in a container `/proc/self/environ`, where
    the runtime-injected Discord token lives.

    This was real and was served: uvicorn percent-decodes before routing and
    `pathlib`'s `/` walks upward without complaint, so `/../../.env` and
    `/%2e%2e/%2e%2e/.env` both returned the file. It is the board's own scrub
    gap — the stated reason `friday/board/` was deleted — reintroduced by the
    commit that replaced it.

    **Tested at the function, not over HTTP, and that is the point.** The
    first version drove `TestClient`, which normalises `..` out of the path
    before the route sees it, so it passed against the vulnerable code. A real
    uvicorn does not normalise, which is how the reviewer found it. The
    containment decision is a function so it can be checked where it is made,
    and every target below is a file that actually exists so that the guard
    is what makes the case pass.
    """
    assert servable(_repo(tmp_path), attempt) is None


def test_the_containment_check_still_serves_what_the_page_owns(tmp_path):
    """A guard that breaks the thing it guards is one somebody deletes."""
    page = _built(tmp_path / "dist")

    assert servable(page, "assets/index-abc.js") == page / "assets" / "index-abc.js"
    assert servable(page, "index.html") == page / "index.html"
    assert servable(page, "") is None, "the empty path is the SPA, not a file"
    assert servable(page, "flow/discord/123") is None, "a browser route is not a file"


def test_a_symlink_out_of_the_page_is_not_a_way_around_it(tmp_path):
    """`resolve()` follows symlinks, so a link planted inside `web/dist`
    resolves outside it and is refused — checking the unresolved path would
    not catch this."""
    page = _repo(tmp_path)
    (page / "sneaky").symlink_to(tmp_path / "repo" / ".env")

    assert servable(page, "sneaky") is None


def test_every_entrypoint_that_binds_a_port_checks_its_exposure():
    """There are two doors and ticket 04 only hardened one.

    `run_agent.py` calls `check_exposure`; `serve_board.py` called `bind` and
    nothing else, while the same commit made it *writable* — so an operator
    who set `board_host: 0.0.0.0` got a refusal from the agent and an
    unauthenticated write endpoint from the script. Both read the same
    `config.board_host`.

    Asserted by reading the source rather than by remembering, because the
    thing that failed here was somebody remembering.
    """
    import ast

    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("run_agent.py", "serve_board.py"):
        tree = ast.parse((root / name).read_text())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "bind" not in called:
            continue
        assert "check_exposure" in called, (
            f"{name} binds a port without checking its exposure — it can serve "
            "every captured message, every model prompt, and a write path into "
            "what the agents believe about a room"
        )


async def test_the_board_carries_the_threshold_the_page_compares_against(client):
    """`FlowScreen` renders "confidence 0.62 of 0.70" and the 0.70 was
    hardcoded — `config.yaml`'s `confidence_threshold` was never served. It
    agreed by luck and would diverge silently the first time the operator
    tuned it, which defeats the only thing that badge exists to show: a
    number is meaningless without the line it is being judged against."""
    board = client.get("/api/board").json()

    assert board["confidence_threshold"] == 0.7
    assert set(board) == declared("Board")


async def test_a_tasks_tool_calls_are_reachable_beside_its_prompts(client, db):
    """The task screen claimed model and tool calls interleaved, and fetched
    only `/api/tasks/{id}/model-calls` — there was no per-task tool route at
    all, so nothing on that screen had ever shown a tool call. Two ticked
    criteria, one missing route."""
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )
    await db.record_model_call(
        message_id=None, task_id=task.id, node="prepare", agent="extractor",
        model="m", system_prompt="s", prompt="p", output="o",
        input_tokens=4, output_tokens=1,
    )
    await db.record_tool_call(
        task_id=task.id, node="prepare", agent="extractor",
        tool="memory_search", arguments="{}", result="asked", failed=False,
    )

    calls = client.get(f"/api/tasks/{task.id}/calls").json()

    assert set(calls) == declared("TaskCalls")
    assert [c["agent"] for c in calls["model_calls"]] == ["extractor"]
    assert [t["tool"] for t in calls["tool_calls"]] == ["memory_search"]
    assert calls["spent"] == 5


async def test_a_stuck_tasks_compaction_state_is_reachable(client, db):
    """Ticket 08 of `what-the-room-already-knows`: a log warning names a
    task whose own transcript truncation could not bring it under budget,
    but a log is not the operator's own view of it — this route is."""
    task = await db.create_task(
        conversation=WATCHED, type="api_issue", state=TaskState.PENDING,
        confidence=0.9, params={},
    )

    fresh = client.get(f"/api/tasks/{task.id}/compaction").json()
    assert set(fresh) == declared("TaskCompaction")
    assert fresh == {"ineffective_count": 0, "on_cooldown": False}

    await db.record_ineffective_compaction(task.id)
    await db.record_ineffective_compaction(task.id)

    stuck = client.get(f"/api/tasks/{task.id}/compaction").json()
    assert stuck == {"ineffective_count": 2, "on_cooldown": True}


# --- the operator's memory rows (board `read-it-the-way-the-operator-does`,
# ticket 09) -----------------------------------------------------------------


def test_the_page_and_the_memory_routes_agree(client):
    """A row the operator writes comes back in the same shape the list
    renders, and the form's description of each kind matches its types."""
    made = client.post(
        "/api/channels/c1/memories",
        json={"kind": "person", "data": {"discord_id": "1", "name": "Lan",
                                          "role": "backend", "team": "orders"}},
    ).json()
    (listed,) = client.get("/api/channels/c1/memories").json()
    kinds = client.get("/api/memory-kinds").json()

    assert set(made) == declared("Memory")
    assert set(listed) == declared("Memory")
    assert all(set(k) == declared("MemoryKindForm") for k in kinds)
    fields = [f for k in kinds for f in k["fields"]]
    assert fields and all(set(f) == declared("MemoryField") for f in fields)
