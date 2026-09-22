"""The DBOS adapter beneath the workflow port — the one module that imports `dbos`.

A `DAG` from `friday.sdk.workflow` is compiled onto DBOS here: the graph walk
is a `@DBOS.workflow`, each node an `@DBOS.step` whose return DBOS memoizes, so
a crash resumes from the first node with no recorded result — DBOS's durability
in place of the hand-written engine's checkpoint and derived `DAG.version`
(DESIGN-v2 §7).

Two invariants the kernel keeps for itself rather than delegating to DBOS
(§3.1): **the kernel chain still wraps every step** — the clock, the retry, the
redaction and the `node_runs` record are Friday code in `_invoke`, not
`@DBOS.step(max_retries=…)`; and a workflow's input is a **serializable scope
key**, with the live `Deps` rebuilt inside the run (DBOS cannot persist an open
connection).

`Ask`/`HandOver` **suspend the workflow in place** on `DBOS.recv_async` and
resume when the pool `send`s the answer — the durable version of v1's pause,
proven by spike to survive a kill mid-wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from dbos import DBOS, SetWorkflowID

from friday.domain.actions import Ask
from friday.ops.redact import scrub
from friday.sdk.workflow import (
    DAG,
    DAGState,
    Deps,
    DepsFactory,
    Node,
    NodeRun,
    ScopeKey,
    envelope,
    status_of,
)

log = logging.getLogger(__name__)

#: Given every attempt of every node as it ends, for the `node_runs` rows.
Recorder = Callable[[NodeRun], Awaitable[None]]
#: Builds the per-run recorder from the scope key (recording is per task).
RecorderFactory = Callable[[ScopeKey], Recorder]

#: A cycle in the edges would otherwise spin forever; the graph is meant to be
#: acyclic, and this says so out loud.
MAX_STEPS = 50

#: The DBOS event key a suspended workflow publishes its pending `Ask` under,
#: so the pool can poll a run to its next boundary from outside.
PENDING_EVENT = "pending"

#: How long a suspended `Ask`/`HandOver` waits for its answer before the wait
#: itself times out. DBOS records the wait as a durable sleep, so it must be a
#: concrete number — `recv` with no timeout is not supported. A day is longer
#: than any reporter/operator turn; the cutover wires this to a per-run budget
#: after which the wait resolves to a hand-over rather than blocking forever.
WAIT_TIMEOUT_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class _Graph:
    dag: DAG
    deps_factory: DepsFactory
    recorder_factory: RecorderFactory | None


#: The router's registry, in DBOS terms: name -> how to run it. Populated at
#: startup by `register_graph`; DBOS finds the `@DBOS.workflow` by decoration.
_GRAPHS: dict[str, _Graph] = {}


@dataclass
class _Live:
    """What one running workflow needs that cannot cross a `@DBOS.step`
    boundary: the live `Deps`, the accumulating state, the recorder. The
    memoized step reads these rather than taking the whole growing state as a
    (re-serialized) argument every call. `state` is reassigned as the walk
    records nodes, so this is mutable by design."""

    deps: Deps
    state: DAGState
    recorder: Recorder | None
    #: Answers a suspended node has been given, keyed by node name, oldest
    #: first. Refilled onto `deps.answers` before each run of that node.
    answers: dict[str, list[Any]] = field(default_factory=dict)


#: Live workflow context, keyed by the DBOS workflow id. Rebuilt when the
#: workflow enters (including on recovery), torn down when it ends.
_LIVE: dict[str, _Live] = {}


def register_graph(
    dag: DAG,
    deps_factory: DepsFactory,
    recorder_factory: RecorderFactory | None = None,
) -> None:
    """Make a graph runnable by name. The `deps_factory` rebuilds live handles
    from a scope key inside the run; `recorder_factory` builds the `node_runs`
    sink per task."""
    _GRAPHS[dag.name] = _Graph(dag, deps_factory, recorder_factory)


def clear_graphs() -> None:
    """Forget every registered graph — for tests that register their own."""
    _GRAPHS.clear()


def _timed_out(node: Node) -> dict[str, Any]:
    return envelope("timed_out", f"{node.name} did not finish in {node.timeout_seconds}s")


async def _invoke(
    dag_name: str, node: Node, state: DAGState, deps: Deps, record: Recorder | None
) -> Any:
    """Run one node: its clock, its retry, and its failure as a result.

    A faithful port of the hand-written runner's `_invoke`/`_ended`: the clock
    wraps the node alone (backoff and recording sit outside it), only the
    runner's own clock is `timed_out`, a non-retryable or exhausted exception
    becomes `{status: error}` scrubbed at the point it is built, and every
    attempt is handed to the recorder as it ends.
    """
    loop = asyncio.get_running_loop()
    deadline = None if node.timeout_seconds is None else loop.time() + node.timeout_seconds

    async def ended(attempt: int, started: float, result: Any) -> Any:
        if record is not None:
            run = NodeRun(
                dag_name=dag_name,
                node=node.name,
                attempt=attempt,
                status=status_of(result) or "ok",
                reason=result.get("reason", "") if status_of(result) else "",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            try:
                await record(run)
            except Exception:  # noqa: BLE001 - a failed record must not lose the run
                log.exception(
                    "workflow %s: could not record attempt %d of %s",
                    dag_name, attempt, node.name,
                )
        return result

    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            async with asyncio.timeout_at(deadline) as clock:
                result = await node.run(state, deps)
        except Exception as exc:  # noqa: BLE001 - becomes the result
            if clock.expired():
                return await ended(attempt, started, _timed_out(node))
            reason = scrub(f"{type(exc).__name__}: {exc}")
            retryable = isinstance(exc, node.retry_on)
            if not retryable or attempt >= node.max_attempts:
                if retryable:
                    reason = f"gave up after {attempt} attempts — {reason}"
                log.warning("workflow %s: %s failed — %s", dag_name, node.name, reason)
                return await ended(attempt, started, envelope("error", reason))
            await ended(attempt, started, envelope("error", reason))
        else:
            return await ended(attempt, started, result)

        wait = node.retry_backoff_seconds * 2 ** (attempt - 1)
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - loop.time()))
        await asyncio.sleep(wait)
        if deadline is not None and loop.time() >= deadline:
            return _timed_out(node)


@DBOS.step()
async def _run_node(dag_name: str, node_name: str) -> Any:
    """One node, as a durable step: DBOS memoizes what this returns, so a
    completed node is not run twice on recovery. The live `Deps`, state and
    recorder come from the per-workflow caches, not from step arguments."""
    wfid = DBOS.workflow_id
    assert wfid is not None  # always set inside a workflow
    graph = _GRAPHS[dag_name]
    live = _LIVE[wfid]
    # A node that has asked and been answered re-runs with its answers in hand.
    live.deps.answers[:] = live.answers.get(node_name, [])
    return await _invoke(
        dag_name, graph.dag.node(node_name), live.state, live.deps, live.recorder
    )


@DBOS.workflow()
async def _run_graph(dag_name: str, scope_key: ScopeKey) -> dict[str, Any]:
    """Walk the graph on DBOS. Rebuild `Deps` from the scope key, run each node
    as a memoized step, suspend on `Ask`, and return the final results. Resume
    after a crash re-enters here: the memoized steps replay without re-running,
    so the walk reaches the first incomplete node and continues from there.

    The return is the raw results mapping (node name -> result), **with the
    domain objects intact** — an `Ask`/`Reply`/`HandOver` a node decided on, not
    a JSON projection of it: DBOS pickles a workflow's return, so the pool reads
    the real action off the state the way v1 read it off the live `DAGState`.
    Past this boundary there is no `MissingNodeResult` guard; that lives in the
    node signature, where the wiring mistakes it catches happen.
    """
    graph = _GRAPHS[dag_name]
    wfid = DBOS.workflow_id
    assert wfid is not None  # always set inside a workflow
    # A `_seed` in the scope key pre-loads node results, so the walk skips
    # straight to the first unseeded node — how the replay eval starts at
    # `resolve` with `prepare` already supplied, without running extraction.
    seed = scope_key.get("_seed") or {}
    live = _LIVE[wfid] = _Live(
        deps=await graph.deps_factory(scope_key),
        state=DAGState(results=dict(seed)),
        recorder=graph.recorder_factory(scope_key) if graph.recorder_factory else None,
    )
    try:
        current: str | None = graph.dag.entry
        steps = 0
        while current is not None:
            steps += 1
            if steps > MAX_STEPS:
                raise RuntimeError(
                    f"workflow {dag_name!r} exceeded {MAX_STEPS} steps at "
                    f"{current!r} — check the edges for a cycle"
                )
            if live.state.has(current):
                current = graph.dag.next_after(current, live.state)
                continue

            result = await _run_node(dag_name, current)

            if isinstance(result, Ask):
                # Model B, pure suspend: a node that cannot finish without the
                # reporter suspends the workflow in place. The pool is told what
                # it paused on, the wait is durable (survives a kill — proven by
                # spike), and the answer re-runs the SAME node with the answer in
                # `deps.answers` — the durable version of v1's "answer re-runs
                # the asking node", but only that node re-runs, not the graph
                # from the top.
                #
                # `HandOver` is not here on purpose: an `Ask` waits for the
                # reporter and the same task resumes, but a `HandOver` escalates
                # to the operator out of band — there is no answer that re-runs
                # the node — so it flows on as a terminal result the pool reads
                # off the state, exactly as v1's walk did.
                # Publish what the run is waiting on, so the pool can poll it to
                # its next boundary without blocking (get_event from outside).
                await DBOS.set_event_async(
                    PENDING_EVENT, {"waiting": True, "node": current, "text": result.text}
                )
                answer = await DBOS.recv_async(current, timeout_seconds=WAIT_TIMEOUT_SECONDS)
                await DBOS.set_event_async(PENDING_EVENT, {"waiting": False})
                live.answers.setdefault(current, []).append(answer)
                continue  # re-run `current`; do not advance, do not record the Ask

            live.state = live.state.with_result(current, result)
            current = graph.dag.next_after(current, live.state)
        return dict(live.state.results)
    finally:
        _LIVE.pop(wfid, None)


async def run_node(
    dag_name: str, node: Node, state: DAGState, deps: Deps, record: Recorder | None = None
) -> Any:
    """Run one node OUTSIDE a workflow, through the very same `_invoke` the
    workflow uses — its clock, its retry, its redaction and its `node_runs`
    record. The pool runs node 0 this way each pass, before the durable
    workflow starts, so node 0 is timed and retried like every other node."""
    return await _invoke(dag_name, node, state, deps, record)


def launch(name: str, system_db: str) -> None:
    """Bring DBOS up on its own SQLite system database. The one place outside
    this module that used to import `dbos` — the composition root, the replay
    tool, the test harness — goes through here instead, so the vendor stays put.
    Destroy-first clears any singleton a previous launch left (tests reuse the
    process)."""
    from dbos import DBOS, DBOSConfig

    DBOS.destroy(destroy_registry=False)
    DBOS(config=DBOSConfig(name=name, system_database_url=f"sqlite:///{system_db}"))
    DBOS.launch()


def shutdown() -> None:
    """Tear DBOS down, keeping the decorated-workflow registry for a relaunch."""
    from dbos import DBOS

    DBOS.destroy(destroy_registry=False)


async def start(dag_name: str, scope_key: ScopeKey, *, workflow_id: str | None = None):
    """Start a graph as a durable workflow; returns a DBOS handle. The pool
    keys the handle so it can `answer` a suspended run."""
    keyed = SetWorkflowID(workflow_id) if workflow_id is not None else nullcontext()
    with keyed:
        return await DBOS.start_workflow_async(_run_graph, dag_name, scope_key)


async def run(dag_name: str, scope_key: ScopeKey, *, workflow_id: str | None = None) -> dict[str, Any]:
    """Start and await a graph to completion — the final state as a dict."""
    handle = await start(dag_name, scope_key, workflow_id=workflow_id)
    return await handle.get_result()


async def answer(workflow_id: str, node: str, value: Any) -> None:
    """Deliver the answer a suspended `Ask` is waiting on."""
    await DBOS.send_async(workflow_id, value, topic=node)


async def status(workflow_id: str) -> str | None:
    """A run's state in Friday's own words — `"running"`, `"done"` or
    `"failed"` — or `None` if it was never started. DBOS's status vocabulary is
    mapped here so it does not cross the seam into the pool."""
    try:
        st = await DBOS.get_workflow_status_async(workflow_id)
    except Exception:  # noqa: BLE001 - an unknown id is "not started", not an error
        return None
    if st is None:
        return None
    if st.status == "SUCCESS":
        return "done"
    if st.status in ("ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"):
        return "failed"
    return "running"


async def result(workflow_id: str) -> dict[str, Any]:
    """The finished run's results mapping (node name -> result, objects intact)."""
    handle: Any = await DBOS.retrieve_workflow_async(workflow_id)
    return await handle.get_result()


async def pending(workflow_id: str) -> dict[str, Any] | None:
    """What a suspended run is waiting on — `{"node", "text"}` — or `None` if it
    is not currently suspended on an `Ask`."""
    try:
        event = await DBOS.get_event_async(workflow_id, PENDING_EVENT, timeout_seconds=0)
    except Exception:  # noqa: BLE001
        return None
    if event and event.get("waiting"):
        return {"node": event["node"], "text": event["text"]}
    return None


async def cancel(workflow_id: str) -> None:
    """Stop a run for good — the task was handled or escalated out of band, so
    its suspended workflow must not sit waiting on an answer that will not come."""
    try:
        await DBOS.cancel_workflow_async(workflow_id)
    except Exception:  # noqa: BLE001 - already gone is fine
        pass
