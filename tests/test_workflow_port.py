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

from friday.domain.actions import Ask
from friday.sdk.workflow import DAG, DAGState, Deps, Edge, Node, NodeRun, envelope
from friday.workflow import adapter

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


async def test_ask_suspends_the_workflow_and_resumes_on_the_answer(dbos_sqlite):
    async def asker(state, deps):
        RAN.append("ask")
        return Ask("what is the ticket number?")

    dag = DAG(
        name="asking",
        nodes=(
            Node(name="ask", run=asker),
            _node("use", lambda s, d: envelope("ok", got=s["ask"])),
        ),
        edges=(Edge("ask", "use"),),
    )
    adapter.register_graph(dag, _no_deps)

    handle = await adapter.start("asking", {}, workflow_id="wf-ask")
    # The run is now suspended on recv for node "ask"; deliver the answer.
    await _until(lambda: RAN == ["ask"])
    await adapter.answer("wf-ask", "ask", "TICKET-7")

    state = await handle.get_result()

    assert state["ask"] == "TICKET-7"
    assert state["use"]["got"] == "TICKET-7"


async def test_a_crash_resumes_from_the_last_incomplete_step(dbos_sqlite):
    """Box 8: kill mid-workflow -> restart -> resume from the last incomplete
    step. Simulated in-process: a workflow suspended on `Ask` is PENDING in the
    system db; destroy()+launch() on the same SQLite file triggers DBOS
    recovery, which re-enters the workflow — the completed first step is
    memoized (its side effect does not repeat) and the run resumes still
    waiting, then completes when the answer arrives."""
    async def asker(state, deps):
        RAN.append("ask")  # the side effect that must NOT repeat on resume
        return Ask("waiting")

    dag = DAG(
        name="crash",
        nodes=(
            Node(name="ask", run=asker),
            _node("after", envelope("ok", done=True)),
        ),
        edges=(Edge("ask", "after"),),
    )
    adapter.register_graph(dag, _no_deps)

    await adapter.start("crash", {}, workflow_id="wf-crash")
    await _until(lambda: RAN == ["ask"])  # step ran, now suspended on recv

    # Restart: tear DBOS down and bring it back on the same SQLite file.
    _launch(dbos_sqlite)
    adapter.register_graph(dag, _no_deps)  # re-register in the fresh process state

    from dbos import DBOS

    await _until(lambda: RAN.count("ask") >= 1)  # recovery re-entered
    await adapter.answer("wf-crash", "ask", "resumed")
    handle = await DBOS.retrieve_workflow_async("wf-crash")
    state = await handle.get_result()

    assert RAN.count("ask") == 1  # memoized: the first step did not run twice
    assert state["ask"] == "resumed" and state["after"]["done"] is True


def test_the_adapter_is_the_only_module_that_imports_dbos():
    """Box 2 / Rule 11: DBOS is the adapter beneath the kernel, and a plugin
    (or anything else) reaches durability through `friday.sdk`, never `dbos`.
    Replacing the engine stays a rewrite of one module only while that holds."""
    import subprocess

    allowed = {"friday/workflow/adapter.py"}
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
