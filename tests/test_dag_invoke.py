"""Board `read-it-the-way-the-operator-does`, ticket 11 — one invoke.

Every node the runner starts goes through `DAGRunner._invoke`: the node's own
timeout, its retry over an explicit exception list, any other exception turned
into a result, and a `node_runs` row per attempt. No node wraps itself. These
tests hold the runner to that from the outside — through `run()`, the pool and
the store — never by calling `_invoke`.
"""

from __future__ import annotations

import asyncio

import pytest

from friday.dag.engine import DAG, DAGDeps, DAGRunner, Edge, Node
from friday.dag.state import DAGState
from friday.domain.states import TaskState


def _returns(name: str, ran: list[str]):
    async def run(state: DAGState, deps: DAGDeps):
        ran.append(name)
        return f"{name}-ran"

    return run


# --- timeout ------------------------------------------------------------------


async def test_a_node_that_hangs_returns_timed_out_and_the_run_reaches_its_last_node():
    ran: list[str] = []

    async def hangs(state: DAGState, deps: DAGDeps):
        ran.append("hangs")
        await asyncio.Event().wait()  # never set

    dag = DAG(
        name="slow",
        nodes=(
            Node("a", _returns("a", ran)),
            Node("hangs", hangs, timeout_seconds=0.05),
            Node("last", _returns("last", ran)),
        ),
        edges=(Edge("a", "hangs"), Edge("hangs", "last")),
    )

    state = await asyncio.wait_for(DAGRunner(dag).run(), timeout=5)

    assert ran == ["a", "hangs", "last"]
    assert state["hangs"]["status"] == "timed_out"
    assert "0.05" in state["hangs"]["reason"]
    assert state["last"] == "last-ran"


# --- retry ----------------------------------------------------------------------


class Flaky(Exception):
    """Stands in for what a kubectl or git node would list: a connection drop."""


def _fails_then_answers(times: int, exc: Exception, calls: list[int]):
    async def run(state: DAGState, deps: DAGDeps):
        calls.append(1)
        if len(calls) <= times:
            raise exc
        return "answered"

    return run


async def test_a_node_raising_a_listed_exception_is_tried_again():
    calls: list[int] = []
    dag = DAG(
        name="retrying",
        nodes=(
            Node(
                "look",
                _fails_then_answers(2, Flaky("connection reset"), calls),
                retry_on=(Flaky,),
                max_attempts=3,
                retry_backoff_seconds=0.001,
            ),
        ),
    )

    state = await DAGRunner(dag).run()

    assert len(calls) == 3
    assert state["look"] == "answered"


async def test_a_node_raising_an_unlisted_exception_is_not_tried_again():
    """Retrying a bug buys the same bug at twice the price. It becomes the
    node's result instead, and the run goes on rather than dying."""
    calls: list[int] = []
    ran: list[str] = []
    dag = DAG(
        name="retrying",
        nodes=(
            Node(
                "look",
                _fails_then_answers(1, KeyError("pod"), calls),
                retry_on=(Flaky,),
                max_attempts=3,
                retry_backoff_seconds=0.001,
            ),
            Node("last", _returns("last", ran)),
        ),
        edges=(Edge("look", "last"),),
    )

    state = await DAGRunner(dag).run()

    assert len(calls) == 1
    assert state["look"]["status"] == "error"
    assert "KeyError" in state["look"]["reason"]
    assert ran == ["last"]


async def test_running_out_of_attempts_says_how_many_it_took():
    calls: list[int] = []
    dag = DAG(
        name="retrying",
        nodes=(
            Node(
                "look",
                _fails_then_answers(9, Flaky("connection reset"), calls),
                retry_on=(Flaky,),
                max_attempts=2,
                retry_backoff_seconds=0.001,
            ),
        ),
    )

    state = await DAGRunner(dag).run()

    assert len(calls) == 2
    assert state["look"]["status"] == "error"
    assert "gave up after 2 attempts" in state["look"]["reason"]
    assert "connection reset" in state["look"]["reason"]


async def test_a_timeout_the_node_raises_itself_is_an_error_not_the_nodes_clock():
    """An HTTP client's own timeout is an exception like any other: listed, it
    retries; unlisted, it is an error. Only the runner's clock is `timed_out`."""
    calls: list[int] = []
    dag = DAG(
        name="d",
        nodes=(
            Node(
                "look",
                _fails_then_answers(1, TimeoutError("read timed out"), calls),
                timeout_seconds=5,
            ),
        ),
    )

    state = await DAGRunner(dag).run()

    assert state["look"]["status"] == "error"


async def test_the_timeout_bounds_every_attempt_together():
    """A node that fails fast and retries forever would otherwise outlive its
    own clock — the timeout is the invoke's, not each try's."""
    calls: list[int] = []
    dag = DAG(
        name="d",
        nodes=(
            Node(
                "look",
                _fails_then_answers(10_000, Flaky("down"), calls),
                timeout_seconds=0.1,
                retry_on=(Flaky,),
                max_attempts=10_000,
                # A backoff far past the clock: the wait is cut to what is
                # left rather than slept in full.
                retry_backoff_seconds=30,
            ),
        ),
    )

    state = await asyncio.wait_for(DAGRunner(dag).run(), timeout=2)

    assert state["look"]["status"] == "timed_out"
    assert len(calls) < 10


def test_a_retry_with_nothing_to_retry_on_is_refused():
    """`max_attempts=3` with no exception list reads like a retry and is none."""
    with pytest.raises(ValueError, match="retry_on"):
        Node("look", _returns("look", []), max_attempts=3)


# --- one record per attempt ---------------------------------------------------


async def test_every_attempt_is_handed_to_the_recording_sink():
    """Three attempts are three rows, the way `model_calls` holds one row per
    provider call: a record that shows one where there were three is the
    record disagreeing with what happened."""
    calls: list[int] = []
    recorded = []

    async def sink(run) -> None:
        recorded.append(run)

    dag = DAG(
        name="retrying",
        nodes=(
            Node(
                "look",
                _fails_then_answers(2, Flaky("connection reset"), calls),
                retry_on=(Flaky,),
                max_attempts=3,
                retry_backoff_seconds=0.001,
            ),
        ),
    )

    await DAGRunner(dag, on_node_run=sink).run()

    assert [(r.node, r.attempt, r.status) for r in recorded] == [
        ("look", 1, "error"),
        ("look", 2, "error"),
        ("look", 3, "ok"),
    ]
    assert "connection reset" in recorded[0].reason
    assert all(r.dag_name == "retrying" and r.dag_version == dag.version for r in recorded)
    assert all(r.duration_ms >= 0 for r in recorded)


async def test_a_timed_out_attempt_is_recorded_as_timed_out():
    recorded = []

    async def sink(run) -> None:
        recorded.append(run)

    async def hangs(state: DAGState, deps: DAGDeps):
        await asyncio.Event().wait()

    dag = DAG(name="slow", nodes=(Node("hangs", hangs, timeout_seconds=0.05),))

    await DAGRunner(dag, on_node_run=sink).run()

    assert [(r.node, r.attempt, r.status) for r in recorded] == [("hangs", 1, "timed_out")]


async def test_a_failing_sink_does_not_lose_the_run():
    async def sink(run) -> None:
        raise RuntimeError("disk full")

    ran: list[str] = []
    dag = DAG(name="d", nodes=(Node("a", _returns("a", ran)),))

    state = await DAGRunner(dag, on_node_run=sink).run()

    assert state["a"] == "a-ran"


# --- through the pool and the store ------------------------------------------


_CID = "abcdef01-2345-6789-abcd-ef0123456789"


def _graph(*nodes: Node, name: str = "investigate") -> DAG:
    from tests.test_pool import _ready

    chain = (Node("prepare", _ready), *nodes)
    return DAG(
        name=name,
        nodes=chain,
        edges=tuple(Edge(a.name, b.name) for a, b in zip(chain, chain[1:])),
    )


@pytest.fixture
def api_issue_graph():
    """Swap `api_issue`'s graph for the one a test builds, and put it back."""
    from friday.dag.router import EDGE_ROUTER, register_dag

    original = EDGE_ROUTER.pop("api_issue", None)

    def install(dag: DAG) -> None:
        EDGE_ROUTER.pop("api_issue", None)
        register_dag("api_issue", dag)

    yield install
    EDGE_ROUTER.pop("api_issue", None)
    if original is not None:
        EDGE_ROUTER["api_issue"] = original


async def _pass(db, task):
    from friday.domain.states import TaskState
    from friday.tasks.pool import Pool

    await db.move_task(task.id, TaskState.PENDING)
    return await Pool(db=db, auto_ask=True).run_once()


async def test_every_attempt_of_every_node_is_a_node_runs_row(db, api_issue_graph):
    """Node 0 included: it runs outside the checkpoint but not outside the
    invoke, and on every graph registered today it is the only node there is."""
    from tests.test_pool import make_task

    calls: list[int] = []
    api_issue_graph(
        _graph(
            Node(
                "look",
                _fails_then_answers(1, Flaky("connection reset"), calls),
                retry_on=(Flaky,),
                max_attempts=2,
                retry_backoff_seconds=0.001,
            )
        )
    )
    task = await make_task(db, correlation_id=_CID)

    await _pass(db, task)

    rows = await db.node_runs(task.id)
    assert [(r["node"], r["attempt"], r["status"]) for r in rows] == [
        ("prepare", 1, "ok"),
        ("look", 1, "error"),
        ("look", 2, "ok"),
    ]
    assert all(r["dag_name"] == "investigate" for r in rows)
    assert "connection reset" in rows[1]["reason"]


async def test_a_renamed_node_does_not_inherit_a_stored_result(db, api_issue_graph):
    """`decide` concluded from what `look` found. Rename `look` and the new
    node runs — but without the version in the key, `decide` is still
    recorded under its old name and is walked past, its conclusion drawn from
    a node that no longer exists."""
    from tests.test_pool import make_task

    ran: list[str] = []

    def records(name: str, value):
        async def run(state, deps):
            ran.append(name)
            return value

        return run

    decide = Node("decide", records("decide", {"status": "ok", "reason": "", "cause": "db"}))
    api_issue_graph(_graph(Node("look", records("look", "lines")), decide))
    task = await make_task(db, correlation_id=_CID)
    await _pass(db, task)
    assert ran == ["look", "decide"]

    ran.clear()
    api_issue_graph(_graph(Node("search_logs", records("search_logs", "lines")), decide))
    await _pass(db, task)

    assert ran == ["search_logs", "decide"]


async def test_the_same_graph_still_resumes_past_what_it_recorded(db, api_issue_graph):
    """The other half: the version is stable for an unchanged graph, or the
    checkpoint would never be read at all."""
    from tests.test_pool import make_task

    ran: list[str] = []

    async def look(state, deps):
        ran.append("look")
        return "lines"

    api_issue_graph(_graph(Node("look", look)))
    task = await make_task(db, correlation_id=_CID)
    await _pass(db, task)
    api_issue_graph(_graph(Node("look", look)))
    await _pass(db, task)

    assert ran == ["look"]


async def test_a_node_that_failed_runs_again_on_the_next_pass(db, api_issue_graph):
    """A failure is the node's result for this run — edges route on it — but
    not a completion to resume past. Before the envelope, a raising node
    ended the run before its checkpoint, and the next pass tried it again;
    that is kept."""
    from friday.domain.actions import HandOver
    from tests.test_pool import make_task

    calls: list[int] = []
    api_issue_graph(_graph(Node("look", _fails_then_answers(1, KeyError("pod"), calls))))
    task = await make_task(db, correlation_id=_CID)

    (first,) = await _pass(db, task)
    await _pass(db, task)

    assert len(calls) == 2
    assert first.state == TaskState.NEEDS_HUMAN


async def test_a_graph_that_ends_on_a_failure_hands_over_naming_it(db, api_issue_graph):
    """What the operator reads is what failed, not 'finished without
    deciding' — the reason an exception used to carry must still arrive."""
    from tests.test_pool import make_task

    calls: list[int] = []
    api_issue_graph(_graph(Node("look", _fails_then_answers(1, KeyError("pod"), calls))))
    task = await make_task(db, correlation_id=_CID)

    await _pass(db, task)

    pause = await db.dag_pause(task.id)
    assert pause is not None
    node, question = pause
    assert node == "look"
    assert "KeyError" in question and "pod" in question


async def test_node_0_raising_still_hands_the_task_over(db, api_issue_graph):
    from tests.test_pool import make_task

    async def broken(state, deps):
        raise RuntimeError("extractor exploded")

    api_issue_graph(DAG(name="investigate", nodes=(Node("prepare", broken),)))
    task = await make_task(db, correlation_id=_CID)

    (acted,) = await _pass(db, task)

    assert acted.state == TaskState.NEEDS_HUMAN
    rows = await db.node_runs(task.id)
    assert [(r["node"], r["status"]) for r in rows] == [("prepare", "error")]
    assert "extractor exploded" in rows[0]["reason"]


# --- credentials in an exception ----------------------------------------------

#: What a kubectl, Loki or HTTP client's exception quotes: the header it sent.
#: Shaped to match `friday.ops.redact`'s pattern, and split so this file does
#: not itself look like it carries one.
_SECRET = "Bearer " + "abcdefghijklmnopqrstuvwxyz0123456789"


async def _leaks(state: DAGState, deps: DAGDeps):
    raise RuntimeError(f"401 from loki, sent Authorization: {_SECRET}")


async def test_an_exception_quoting_a_credential_is_scrubbed_before_it_is_the_result():
    """The reason is built from the exception in the runner, and from there
    it is the node's result, the recorded attempt, and the hand-over the
    operator is sent. Scrubbed where it is built, so none of them carries it."""
    recorded = []

    async def sink(run) -> None:
        recorded.append(run)

    dag = DAG(name="d", nodes=(Node("look", _leaks),))

    state = await DAGRunner(dag, on_node_run=sink).run()

    assert _SECRET not in state["look"]["reason"]
    assert "[REDACTED]" in state["look"]["reason"]
    assert _SECRET not in recorded[0].reason


async def test_the_store_scrubs_what_it_is_handed_for_node_runs_and_pauses(db):
    """At the point of writing as well, which is the only place the rule is
    unconditional — `fail_outbound` scrubs its provider error for the same
    reason. A caller that did not build its text through the runner is still
    a caller."""
    from tests.test_pool import make_task

    task = await make_task(db, correlation_id=_CID)
    await db.record_node_run(
        task_id=task.id, dag_name="d", dag_version="v", node="look", attempt=1,
        status="error", reason=f"boom {_SECRET}", duration_ms=1,
    )
    await db.save_dag_state(
        task.id, dag_name="d", results={}, params_fingerprint="f",
        dag_version="v", paused_at_node="look", paused_question=f"boom {_SECRET}",
    )

    (row,) = await db.node_runs(task.id)
    assert _SECRET not in row["reason"]
    _, question = await db.dag_pause(task.id)
    assert _SECRET not in question


async def test_a_credential_in_a_node_exception_never_reaches_the_task_db(db, api_issue_graph):
    """End to end: a node raising with a token in its message, through the
    pool, and neither `node_runs` nor `dag_state` holds it."""
    from tests.test_pool import make_task

    api_issue_graph(_graph(Node("look", _leaks)))
    task = await make_task(db, correlation_id=_CID)

    await _pass(db, task)

    rows = await db.node_runs(task.id)
    assert rows and all(_SECRET not in r["reason"] for r in rows)
    _, question = await db.dag_pause(task.id)
    assert "look" in question and _SECRET not in question


# --- two clocks ---------------------------------------------------------------


def _config_with(agent_timeout: float):
    from types import SimpleNamespace

    return SimpleNamespace(
        agents={"diagnose": SimpleNamespace(timeout_seconds=agent_timeout)},
        context=SimpleNamespace(extraction_budget_tokens=None),
    )


def _model_graph(node_timeout: float):
    async def diagnose(state, deps):
        return "cause"

    def build(task_type, params_cls, **_):
        from tests.test_pool import _ready

        return DAG(
            name=task_type,
            nodes=(
                Node("prepare", _ready),
                Node("diagnose", diagnose, agent="diagnose", timeout_seconds=node_timeout),
            ),
            edges=(Edge("prepare", "diagnose"),),
        )

    return build


@pytest.mark.parametrize("node_timeout", [30.0, 60.0])
def test_config_load_refuses_a_model_node_whose_clock_is_not_past_its_harness(
    monkeypatch, node_timeout
):
    """Finding E. The node's clock cancelling a harness run mid-flight is a
    cancellation, which the harness's retry never sees — so a model node's
    timeout has to leave the harness room to fire first. Equal is a race."""
    from friday.config import ConfigError
    from friday.dag import router

    monkeypatch.setattr(router, "build_simple_dag", _model_graph(node_timeout))

    with pytest.raises(ConfigError, match="diagnose"):
        router.register_dags(_config_with(agent_timeout=60.0))


def test_a_model_node_with_room_past_its_harness_is_accepted(monkeypatch):
    from friday.dag import router

    monkeypatch.setattr(router, "build_simple_dag", _model_graph(90.0))

    router.register_dags(_config_with(agent_timeout=60.0))

    assert router.dag_for("api_issue").node("diagnose").timeout_seconds == 90.0


def test_a_model_node_naming_an_agent_nobody_configured_is_refused(monkeypatch):
    from types import SimpleNamespace

    from friday.config import ConfigError
    from friday.dag import router

    monkeypatch.setattr(router, "build_simple_dag", _model_graph(90.0))

    with pytest.raises(ConfigError, match="diagnose"):
        router.register_dags(
            SimpleNamespace(agents={}, context=SimpleNamespace(extraction_budget_tokens=None))
        )


def _extractor_config(timeout: float | None):
    from types import SimpleNamespace

    agents = {} if timeout is None else {
        "extractor": SimpleNamespace(timeout_seconds=timeout)
    }
    return SimpleNamespace(
        agents=agents, context=SimpleNamespace(extraction_budget_tokens=None)
    )


@pytest.mark.parametrize("extractor_timeout", [60.0, 200.0])
def test_node_0_runs_the_extractor_on_a_clock_that_outlasts_the_extractors(
    extractor_timeout,
):
    """Node 0 is the one model call that goes through the invoke today, so it
    is the one the two-clocks rule has to cover: it names its agent, and its
    clock is the extractor's plus the margin — whatever the extractor is
    given, so raising that in config.yaml cannot slip under node 0."""
    from friday.dag import router

    router.register_dags(_extractor_config(extractor_timeout))

    for task_type in router.EDGE_ROUTER:
        node = router.dag_for(task_type).node("prepare")
        assert node.agent == "extractor"
        assert node.timeout_seconds == extractor_timeout + router.NODE_CLOCK_MARGIN_SECONDS


def test_node_0_with_no_extractor_configured_calls_no_model_and_has_no_clock():
    """No `extractor` block is a warned-about install where node 0 is code
    alone — nothing to bound on the model's behalf, and naming an agent that
    is not configured would refuse the boot."""
    from friday.dag import router

    router.register_dags(_extractor_config(None))

    node = router.dag_for("api_issue").node("prepare")
    assert node.agent is None and node.timeout_seconds is None


def test_the_clocks_are_checked_from_the_configuration_alone(monkeypatch):
    """At config load: `check_graphs` needs nothing but the configuration, so
    the composition root can refuse before it opens the database or reads a
    token — rather than at registration, which waits on the skill library
    and the tool servers."""
    from friday.config import ConfigError
    from friday.dag import router

    monkeypatch.setattr(router, "build_simple_dag", _model_graph(30.0))

    with pytest.raises(ConfigError, match="diagnose"):
        router.check_graphs(_config_with(agent_timeout=60.0))
