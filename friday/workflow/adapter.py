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

from friday.domain.actions import Action, Ask
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

#: Told when a node suspends the workflow on an `Ask`/`HandOver`, and again with
#: `None` when it resumes — so the pool can record what the run paused on (for
#: the operator's help-wanted text and the board) and clear it on resume. Kept
#: out of the adapter so `dbos` and the store stay separate: the pool injects it.
PauseSink = Callable[[ScopeKey, str, Action | None], Awaitable[None]]

#: A cycle in the edges would otherwise spin forever; the graph is meant to be
#: acyclic, and this says so out loud.
MAX_STEPS = 50

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
    pause_sink: PauseSink | None


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
    pause_sink: PauseSink | None
    #: The workflow's own input — what the pause sink is told, so the pool knows
    #: which task suspended.
    scope: ScopeKey
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
    pause_sink: PauseSink | None = None,
) -> None:
    """Make a graph runnable by name. The `deps_factory` rebuilds live handles
    from a scope key inside the run; `recorder_factory` builds the `node_runs`
    sink per task; `pause_sink` is told what a run suspends on (and that it
    resumed), for the pool to act on."""
    _GRAPHS[dag.name] = _Graph(dag, deps_factory, recorder_factory, pause_sink)


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
    live = _LIVE[wfid] = _Live(
        deps=await graph.deps_factory(scope_key),
        state=DAGState.empty(),
        recorder=graph.recorder_factory(scope_key) if graph.recorder_factory else None,
        pause_sink=graph.pause_sink,
        scope=scope_key,
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
                await _record_pause(dag_name, current, result)
                answer = await DBOS.recv_async(current, timeout_seconds=WAIT_TIMEOUT_SECONDS)
                await _clear_pause(dag_name, current)
                live.answers.setdefault(current, []).append(answer)
                continue  # re-run `current`; do not advance, do not record the Ask

            live.state = live.state.with_result(current, result)
            current = graph.dag.next_after(current, live.state)
        return dict(live.state.results)
    finally:
        _LIVE.pop(wfid, None)


@DBOS.step()
async def _record_pause(dag_name: str, node: str, action: Action) -> None:
    """Tell the pool what this run suspended on. A step so the notice is durable
    and replays in order; the sink itself (the store write) is the pool's."""
    graph = _GRAPHS[dag_name]
    if graph.pause_sink is not None:
        await graph.pause_sink(_scope_of(), node, action)


@DBOS.step()
async def _clear_pause(dag_name: str, node: str) -> None:
    """The answer arrived; the run is no longer waiting on it."""
    graph = _GRAPHS[dag_name]
    if graph.pause_sink is not None:
        await graph.pause_sink(_scope_of(), node, None)


def _scope_of() -> ScopeKey:
    wfid = DBOS.workflow_id
    assert wfid is not None
    return _LIVE[wfid].scope


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
    """Deliver the answer a suspended `Ask`/`HandOver` is waiting on."""
    await DBOS.send_async(workflow_id, value, topic=node)
