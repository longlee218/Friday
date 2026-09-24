"""S3 — the workflow port on real DBOS (throwaway SQLite), ticket 06.

The seam the spec names highest: run graphs against a real DBOS on a file-based
SQLite system database, and assert the durability behaviour that is the whole
reason the engine exists — a completed step is not run twice, a crash resumes
from the last incomplete step, an `Ask`/`HandOver` suspends and resumes on the
answer. Individual node logic stays unit-testable with stub deps elsewhere;
this file proves the adapter.
"""

from __future__ import annotations

import tempfile

import pytest

from friday.sdk.actions import Ask
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.outbox import Outbox
from friday.sdk.workflow import DAG, DAGState, Deps, Edge, Node, NodeRun, envelope
from friday.kernel.dag import adapter

WATCHED = ConversationId("fake", "watched")

# Side-effect ledgers, module-level so they survive a destroy()+launch() (a
# simulated restart) within one test process and prove memoization.
RAN: list[str] = []
RECORDED: list[NodeRun] = []


def _launch(sysdb: str) -> None:
    from dbos import DBOS, DBOSConfig

    DBOS.destroy(destroy_registry=False)  # clear any stale singleton first
    cfg: DBOSConfig = {"name": "friday-wf-test", "system_database_url": f"sqlite:///{sysdb}"}
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

    dag = DAG(name="retry", nodes=(Node(
        name="flaky", run=flaky, retry_on=(ValueError,), max_attempts=2,
        retry_backoff_seconds=0.0,
    ),))
    adapter.register_graph(dag, _no_deps, _record_all)

    state = await adapter.run("retry", {})

    assert state["flaky"]["tries"] == 2
    assert [r.status for r in RECORDED] == ["error", "ok"]  # one row per attempt


async def test_a_node_that_outruns_its_clock_is_timed_out(dbos_sqlite):
    """The kernel clock wraps the node alone: a node that does not finish in
    `timeout_seconds` comes back `{status: timed_out}`, and the run goes on."""
    import asyncio as _asyncio

    async def slow(state, deps):
        await _asyncio.sleep(5)
        return envelope("ok")

    dag = DAG(name="slow", nodes=(Node(name="slow", run=slow, timeout_seconds=0.05),))
    adapter.register_graph(dag, _no_deps)

    state = await adapter.run("slow", {})

    assert state["slow"]["status"] == "timed_out"


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


async def test_ask_suspends_then_the_answer_reruns_the_asking_node(dbos_sqlite):
    """Pure model B: `Ask` suspends; the answer re-runs the SAME node with the
    answer in `deps.answers` (v1's "answer re-runs the asking node"), not a
    result-replacement. Only that node re-runs — upstream stays memoized."""
    async def asker(state, deps):
        RAN.append("ask")
        if deps.answers:  # re-run after the answer arrived
            return envelope("ok", got=deps.answers[-1])
        return Ask("what is the ticket number?")

    dag = DAG(
        name="asking",
        nodes=(
            Node(name="ask", run=asker),
            _node("use", lambda s, d: envelope("ok", got=s["ask"]["got"])),
        ),
        edges=(Edge("ask", "use"),),
    )
    adapter.register_graph(dag, _no_deps)

    handle = await adapter.start("asking", {}, workflow_id="wf-ask")
    await _until(lambda: RAN == ["ask"])  # suspended on recv for node "ask"
    # The pool learns what it paused on by polling the run to its boundary.
    assert (await adapter.pending("wf-ask"))["text"] == "what is the ticket number?"
    await adapter.answer("wf-ask", "ask", "TICKET-7")

    state = await handle.get_result()

    assert RAN == ["ask", "ask", "use"]  # asked, re-ran with the answer, then on
    assert state["ask"]["got"] == "TICKET-7"
    assert state["use"]["got"] == "TICKET-7"


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
    to a marker file — then suspends on `recv` and `os._exit`s: a true kill
    while waiting for the operator. This process then launches DBOS on the SAME
    SQLite file, registers the same graph, and DBOS recovery re-enters the
    PENDING workflow: `ask` is memoized (the marker stays at one `a`), the run
    re-suspends on `recv`, and completes when the answer is sent."""
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
        [sys.executable, str(root / "tests/dbos_crash_child.py"), dbos_sqlite, marker, "wf-crash"],
        capture_output=True, text=True, timeout=60, cwd=str(root),
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONPATH": str(root)},
    )
    assert child.returncode == 1, f"child did not crash as expected: {child.stderr[-800:]}"
    assert open(marker).read().count("a") == 1  # ask ran once before the kill

    _launch(dbos_sqlite)  # "restart" — same SQLite file, fresh process state
    build_and_register(marker)

    # The answer is durable, so recovery picks it up whenever it re-enters;
    # get_result blocks until the recovered workflow completes.
    await adapter.answer("wf-crash", "ask", "resumed")
    handle = await DBOS.retrieve_workflow_async("wf-crash")
    state = await handle.get_result()

    assert open(marker).read().count("a") == 1  # memoized: ask not re-run on resume
    assert state["ask"]["answer"] == "resumed" and state["after"]["done"] is True


async def test_a_send_interrupted_mid_call_is_delivery_unknown_after_restart(dbos_sqlite):
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

    from friday.store.db import Database
    from friday.kernel.dag import adapter

    tmp = tempfile.mkdtemp()
    app_db = f"{tmp}/app.db"
    marker = f"{tmp}/marker"
    root = Path(__file__).resolve().parent.parent

    # Seed one sendable row (a policy-approved ask, frozen at enqueue), then let
    # go of both databases so the child has exclusive access.
    db = await Database.connect(app_db, create=True)
    opened = await db.create_task(
        conversation=WATCHED, type="devops.api_issue", state="pending",
        confidence=0.9, params={"summary": "s"},
    )
    row = await db.queue_outbound(
        task_id=opened.id, conversation=WATCHED, kind="ask_for_details",
        sender="discord_user", text="which environment?",
    )
    await db.close()
    DBOS.destroy(destroy_registry=False)  # the child needs the system db to itself

    child = subprocess.run(
        [sys.executable, str(root / "tests/outbox_crash_child.py"),
         dbos_sqlite, app_db, marker, str(row.id)],
        capture_output=True, text=True, timeout=60, cwd=str(root),
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONPATH": str(root)},
    )
    assert child.returncode == 1, f"child did not crash as expected: {child.stderr[-800:]}"
    assert open(marker).read().count("a") == 1  # the channel was called once

    # "Restart": same system db, same application db, a fresh sender.
    _launch(dbos_sqlite)
    db = await Database.connect(app_db, create=False)
    box = Outbox(db=db, senders={"discord_user": _RecordingSender()})
    adapter.register_outbox(box.deliver_once)

    # Recovery re-enters the PENDING workflow; get_result blocks until it ends.
    handle = await DBOS.retrieve_workflow_async(f"outbox-{row.id}-0")
    outcome = await handle.get_result()

    assert outcome == "delivery_unknown"
    assert open(marker).read().count("a") == 1, "the send was not repeated on resume"
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
        capture_output=True, text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected dbos importer: {set(hits) - allowed}"


async def _until(pred, tries: int = 200, delay: float = 0.02) -> None:
    import asyncio

    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(delay)
    raise AssertionError("condition not met in time")
