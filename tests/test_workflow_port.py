"""S3 — the workflow port on real DBOS (throwaway SQLite), ticket 06.

The seam the spec names highest: run graphs against a real DBOS on a file-based
SQLite system database, and assert the durability behaviour that is the whole
reason the engine exists — a completed step is not run twice, a crash resumes
from the last incomplete step, an `Ask`/`HandOver` is a terminal result (no
workflow waits on a person since build-the-spine ticket 14). Individual node logic stays unit-testable with stub deps elsewhere;
this file proves the adapter.
"""

from __future__ import annotations

import tempfile

import pytest

from friday.kernel.dag import adapter
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.outbox import Outbox
from friday.sdk.actions import Ask
from friday.sdk.workflow import DAG, DAGState, Deps, Edge, Node, NodeRun, envelope

WATCHED = ConversationId("fake", "watched")

# Side-effect ledgers, module-level so they survive a destroy()+launch() (a
# simulated restart) within one test process and prove memoization.
RAN: list[str] = []
RECORDED: list[NodeRun] = []


def _launch(sysdb: str) -> None:
    from dbos import DBOS, DBOSConfig

    DBOS.destroy(destroy_registry=False)  # clear any stale singleton first
    cfg: DBOSConfig = {
        "name": "friday-wf-test",
        "system_database_url": f"sqlite:///{sysdb}",
    }
    DBOS(config=cfg)
    DBOS.launch()


@pytest.fixture()
def dbos_sqlite():
    """A real DBOS on its own throwaway SQLite file. Yields the db path so a
    test can simulate a restart by destroy()+_launch() on the same file."""
    RAN.clear()
    RECORDED.clear()
    adapter.clear_graphs()
    tmp = tempfile.mkdtemp()
    sysdb = f"{tmp}/sys.db"
    _launch(sysdb)
    try:
        yield sysdb
    finally:
        from dbos import DBOS

        DBOS.destroy(destroy_registry=False)
        adapter.clear_graphs()


async def _no_deps(scope_key) -> Deps:
    return Deps(extra=dict(scope_key))


def _record_all(scope_key):
    async def rec(run: NodeRun) -> None:
        RECORDED.append(run)

    return rec


def _node(name: str, result):
    async def run(state: DAGState, deps: Deps):
        RAN.append(name)
        return result(state, deps) if callable(result) else result

    return Node(name=name, run=run)


async def test_a_linear_graph_runs_every_node_and_returns_the_state(dbos_sqlite):
    dag = DAG(
        name="linear",
        nodes=(
            _node("a", envelope("ok", value=1)),
            _node("b", envelope("ok", value=2)),
        ),
        edges=(Edge("a", "b"),),
    )
    adapter.register_graph(dag, _no_deps)

    state = await adapter.run("linear", {})

    assert RAN == ["a", "b"]
    assert state["a"]["value"] == 1 and state["b"]["value"] == 2


async def test_list_workflows_reports_a_finished_run_as_succeeded(dbos_sqlite):
    """Ticket 08: the board reads `list_workflows`, mapped out of DBOS's
    vocabulary. A run driven to completion shows up as `succeeded` — on real
    DBOS, not a stub — with its id and no DBOS status word leaking through."""
    dag = DAG(name="one", nodes=(_node("only", envelope("ok", value=1)),))
    adapter.register_graph(dag, _no_deps)

    await adapter.run("one", {}, workflow_id="wf-done")

    views = await adapter.list_workflows()
    mine = next(v for v in views if v["id"] == "wf-done")
    assert mine["status"] == "succeeded"
    assert mine["status"] not in {"SUCCESS", "PENDING", "ERROR"}


async def test_an_edge_condition_routes_the_walk(dbos_sqlite):
    dag = DAG(
        name="branch",
        nodes=(
            _node("start", envelope("empty")),
            _node("left", envelope("ok", side="left")),
            _node("right", envelope("ok", side="right")),
        ),
        edges=(
            Edge("start", "left", when=lambda s: s["start"]["status"] == "ok"),
            Edge("start", "right", when=lambda s: s["start"]["status"] == "empty"),
        ),
    )
    adapter.register_graph(dag, _no_deps)

    state = await adapter.run("branch", {})

    assert RAN == ["start", "right"]
    assert "left" not in state and state["right"]["side"] == "right"


async def test_a_node_error_becomes_an_error_envelope_scrubbed(dbos_sqlite):
    async def boom(state, deps):
        RAN.append("boom")
        raise RuntimeError("leaked token=sk-ABC123DEF456GHI789 in the client")

    dag = DAG(name="err", nodes=(Node(name="boom", run=boom),))
    adapter.register_graph(dag, _no_deps, _record_all)

    state = await adapter.run("err", {})

    assert state["boom"]["status"] == "error"
    assert "sk-ABC123DEF456GHI789" not in state["boom"]["reason"]  # scrub applied
    assert "[REDACTED]" in state["boom"]["reason"]
    assert [r.status for r in RECORDED] == ["error"]
    assert RECORDED[0].node == "boom" and RECORDED[0].dag_name == "err"


async def test_a_node_retries_the_exceptions_it_named_then_succeeds(dbos_sqlite):
    """The kernel chain's retry, ported into the adapter's `_invoke`: an
    exception in `retry_on` is tried again up to `max_attempts`, and every
    attempt is a `node_runs` row."""
    tries = {"n": 0}

    async def flaky(state, deps):
        tries["n"] += 1
        if tries["n"] < 2:
            raise ValueError("transient")
        return envelope("ok", tries=tries["n"])

    dag = DAG(
        name="retry",
        nodes=(
            Node(
                name="flaky",
                run=flaky,
                retry_on=(ValueError,),
                max_attempts=2,
                retry_backoff_seconds=0.0,
            ),
        ),
    )
    adapter.register_graph(dag, _no_deps, _record_all)

    state = await adapter.run("retry", {})

    assert state["flaky"]["tries"] == 2
    assert [r.status for r in RECORDED] == ["error", "ok"]  # one row per attempt


async def test_deps_are_rebuilt_inside_the_run_from_the_scope_key(dbos_sqlite):
    seen = {}

    async def reader(state, deps):
        seen["task_id"] = deps.extra["task_id"]
        return envelope("ok")

    async def factory(scope_key) -> Deps:
        return Deps(extra={"task_id": scope_key["task_id"]})

    dag = DAG(name="scoped", nodes=(Node(name="r", run=reader),))
    adapter.register_graph(dag, factory)

    await adapter.run("scoped", {"task_id": "T-42"})

    assert seen["task_id"] == "T-42"


async def test_an_ask_is_terminal_nothing_waits_on_a_person(dbos_sqlite):
    """Ticket 14: an `Ask` ends the run like a `HandOver` — the pool reads it
    off the state; the reporter's reply is a new pass, not a `recv`."""

    async def asker(state, deps):
        RAN.append("ask")
        return Ask("what is the ticket number?")

    dag = DAG(
        name="asking",
        nodes=(
            Node(name="ask", run=asker),
            _node("use", lambda s, d: envelope("ok")),
        ),
        edges=(Edge("ask", "use", when=lambda s: not isinstance(s.get("ask"), Ask)),),
    )
    adapter.register_graph(dag, _no_deps)

    state = await adapter.run("asking", {}, workflow_id="wf-ask")

    assert RAN == ["ask"]
    assert state["ask"] == Ask("what is the ticket number?")


async def test_handover_is_terminal_not_suspended(dbos_sqlite):
    """`HandOver` does NOT suspend: an `Ask` waits for the reporter, but a
    `HandOver` escalates to the operator out of band, so it flows on as a
    terminal result the pool reads off the state (v1 semantics)."""
    from friday.sdk.actions import HandOver

    async def stuck(state, deps):
        RAN.append("stuck")
        return HandOver("needs a human")

    dag = DAG(name="ho", nodes=(Node(name="stuck", run=stuck),))
    adapter.register_graph(dag, _no_deps)

    # No answer is ever sent; the run still completes because HandOver is
    # terminal, and the state carries it for the pool.
    state = await adapter.run("ho", {}, workflow_id="wf-ho")

    assert RAN == ["stuck"]
    assert isinstance(state["stuck"], HandOver)


async def test_a_crash_resumes_from_the_last_incomplete_step(dbos_sqlite):
    """Box 8: kill mid-workflow -> restart -> resume from the last incomplete
    step, on real DBOS + SQLite.

    A child process (`dbos_crash_child.py`) runs the `ask` step — appending once
    to a marker file — then `os._exit`s inside `after`, which waits for a go
    file only this process writes: a true kill mid-run. This process then
    launches DBOS on the SAME SQLite file, registers the same graph, and DBOS
    recovery re-enters the PENDING workflow: `ask` is memoized (the marker
    stays at one `a`) and `after` runs to the end."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from dbos import DBOS

    from tests.dbos_crash_child import build_and_register

    # This fixture already launched DBOS; the child needs exclusive access to
    # the SQLite file, so tear ours down, run the child, then bring ours back.
    root = Path(__file__).resolve().parent.parent
    marker = dbos_sqlite + ".marker"
    DBOS.destroy(destroy_registry=False)
    child = subprocess.run(
        [
            sys.executable,
            str(root / "tests/dbos_crash_child.py"),
            dbos_sqlite,
            marker,
            "wf-crash",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(root),
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONPATH": str(root)},
        check=False,
    )
    assert child.returncode == 1, (
        f"child did not crash as expected: {child.stderr[-800:]}"
    )
    assert Path(marker).read_text().count("a") == 1  # ask ran once before the kill

    Path(marker + ".go").write_text("go")
    _launch(dbos_sqlite)  # "restart" — same SQLite file, fresh process state
    build_and_register(marker)

    handle = await DBOS.retrieve_workflow_async("wf-crash")
    state = await handle.get_result()

    assert (
        Path(marker).read_text().count("a") == 1
    )  # memoized: ask not re-run on resume
    assert state["ask"]["asked"] is True and state["after"]["done"] is True


async def test_a_send_interrupted_mid_call_is_delivery_unknown_after_restart(
    dbos_sqlite,
):
    """Ticket 07, criterion 6: kill between the channel call and the write ->
    the row is `delivery_unknown` after restart, and nothing is sent twice.

    A child process delivers one row through the durable step: it marks the row
    `dispatching`, calls the channel (appending once to a marker), then
    `os._exit`s before recording the send — a true crash mid-delivery. This
    process then relaunches DBOS on the SAME system database and reopens the SAME
    application database; DBOS recovery re-enters the PENDING delivery workflow,
    `deliver_once` reads the `dispatching` marker, and — the channel cannot
    dedupe — routes the row to `delivery_unknown` without a second send."""
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from dbos import DBOS

    from friday.kernel.dag import adapter
    from friday.store.db import Database

    tmp = tempfile.mkdtemp()
    app_db = f"{tmp}/app.db"
    marker = f"{tmp}/marker"
    root = Path(__file__).resolve().parent.parent

    # Seed one sendable row (a policy-approved ask, frozen at enqueue), then let
    # go of both databases so the child has exclusive access.
    db = await Database.connect(app_db, create=True)
    opened = await db.create_task(
        conversation=WATCHED,
        type="backend.trace_problem",
        state="pending",
        confidence=0.9,
        params={"summary": "s"},
    )
    row = await db.queue_outbound(
        task_id=opened.id,
        conversation=WATCHED,
        kind="ask_for_details",
        sender="discord_user",
        text="which environment?",
    )
    await db.close()
    DBOS.destroy(destroy_registry=False)  # the child needs the system db to itself

    child = subprocess.run(
        [
            sys.executable,
            str(root / "tests/outbox_crash_child.py"),
            dbos_sqlite,
            app_db,
            marker,
            str(row.id),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(root),
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONPATH": str(root)},
        check=False,
    )
    assert child.returncode == 1, (
        f"child did not crash as expected: {child.stderr[-800:]}"
    )
    assert Path(marker).read_text().count("a") == 1  # the channel was called once

    # "Restart": same system db, same application db, a fresh sender.
    _launch(dbos_sqlite)
    db = await Database.connect(app_db, create=False)
    box = Outbox(db=db, senders={"discord_user": _RecordingSender()})
    adapter.register_outbox(box.deliver_once)

    # Recovery re-enters the PENDING workflow; get_result blocks until it ends.
    handle = await DBOS.retrieve_workflow_async(f"outbox-{row.id}-0")
    outcome = await handle.get_result()

    assert outcome == "delivery_unknown"
    assert Path(marker).read_text().count("a") == 1, (
        "the send was not repeated on resume"
    )
    assert (await db.outbound_row(row.id)).state == "delivery_unknown"
    assert (await db.tasks())[0].state == "needs_human"
    await db.close()


class _RecordingSender:
    """A non-idempotent channel that would record a send if asked — so the test
    can prove recovery does NOT ask it to."""

    supports_idempotency = False

    def __init__(self) -> None:
        self.sent: list[int] = []

    async def send(self, row) -> str:
        self.sent.append(row.id)
        return f"sent-{row.id}"


def test_the_adapter_is_the_only_module_that_imports_dbos():
    """Box 2 / Rule 11: DBOS is the adapter beneath the kernel, and a plugin
    (or anything else) reaches durability through `friday.sdk`, never `dbos`.
    Replacing the engine stays a rewrite of one module only while that holds."""
    import subprocess

    allowed = {"friday/kernel/dag/adapter.py"}
    hits = subprocess.run(
        ["grep", "-rlE", r"^\s*(from|import)\s+dbos\b", "friday/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected dbos importer: {set(hits) - allowed}"


def test_launch_leaves_the_system_db_in_wal(tmp_path):
    """§12.1 (ticket 09): the workflow db is WAL, set by the production launch
    path — not only the `_enable_wal` helper it calls. A board read must not
    block a writing workflow here any more than on the application db."""
    import sqlite3

    from friday.kernel.dag import adapter

    sysdb = str(tmp_path / "sys.db")
    adapter.launch("friday-wal-test", sysdb)
    try:
        conn = sqlite3.connect(sysdb)
        try:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        finally:
            conn.close()
    finally:
        adapter.shutdown()
    assert mode.lower() == "wal"


async def _until(pred, tries: int = 200, delay: float = 0.02) -> None:
    import asyncio

    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(delay)
    raise AssertionError("condition not met in time")
