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

A graph's `Ask`/`HandOver` is a terminal result: nothing waits on a person
inside a workflow any more (build-the-spine ticket 14 — `DBOS.recv/send` and
the 24h wait went with it). The spine pass (`friday/kernel/spine/workflow.py`)
is a workflow here too: `task-<id>/pass-<n>`, each stage one durable step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from dbos import DBOS, SetWorkflowID

from friday.kernel.ops.redact import scrub
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
    """Forget every registered graph and the outbox delivery callable — for
    tests that register their own."""
    global _DELIVER
    _GRAPHS.clear()
    _DELIVER = None


async def _invoke(
    dag_name: str, node: Node, state: DAGState, deps: Deps, record: Recorder | None
) -> Any:
    """Run one node: its retry, and its failure as a result.

    A faithful port of the hand-written runner's `_invoke`/`_ended`, less its
    clock (board `domains-plug-in`, ticket 17 — time lives only on a tool
    call): a non-retryable or exhausted exception becomes `{status: error}`
    scrubbed at the point it is built, and every attempt is handed to the
    recorder as it ends.
    """

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
            except Exception:
                log.exception(
                    "workflow %s: could not record attempt %d of %s",
                    dag_name,
                    attempt,
                    node.name,
                )
        return result

    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        try:
            result = await node.run(state, deps)
        except Exception as exc:  # noqa: BLE001 - becomes the result
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

        await asyncio.sleep(node.retry_backoff_seconds * 2 ** (attempt - 1))


@DBOS.step()
async def _run_node(dag_name: str, node_name: str) -> Any:
    """One node, as a durable step: DBOS memoizes what this returns, so a
    completed node is not run twice on recovery. The live `Deps`, state and
    recorder come from the per-workflow caches, not from step arguments."""
    wfid = DBOS.workflow_id
    assert wfid is not None  # always set inside a workflow
    graph = _GRAPHS[dag_name]
    live = _LIVE[wfid]
    return await _invoke(
        dag_name, graph.dag.node(node_name), live.state, live.deps, live.recorder
    )


@DBOS.workflow()
async def _run_graph(dag_name: str, scope_key: ScopeKey) -> dict[str, Any]:
    """Walk the graph on DBOS. Rebuild `Deps` from the scope key, run each node
    as a memoized step, and return the final results. Resume
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
            live.state = live.state.with_result(current, result)
            current = graph.dag.next_after(current, live.state)
        return dict(live.state.results)
    finally:
        _LIVE.pop(wfid, None)


# ── Outbox delivery as a durable workflow (ticket 07) ────────────────────────
#
# One delivery = one DBOS workflow, keyed `outbox-{row}-{attempt}`, so DBOS gives
# it exactly-once. The whole delivery decision — the state guard, the frozen-hash
# check, the staleness check, `dispatching` before the call, the send, the record
# after — is `Outbox.deliver_once`, written to be safe to re-run. DBOS re-runs a
# step it has no recorded result for, which is precisely a send interrupted by a
# crash: the step re-enters, `deliver_once` reads the `dispatching` marker its
# own crashed run left, and routes to `delivery_unknown` (or re-sends with the
# idempotency key, on a channel that dedupes) — never a silent double-post.
#
# `deliver_once` holds the live `db`/senders, which do not serialize, so the step
# takes only the row id and looks the callable up from this module-level slot,
# the same shape `_GRAPHS` uses for the graph walk.
_DELIVER: Callable[[int], Awaitable[str]] | None = None


def register_outbox(deliver_once: Callable[[int], Awaitable[str]]) -> None:
    """Wire the outbox's one-attempt delivery in, so the durable step can call
    it. The composition root passes `Outbox.deliver_once`; tests pass their own."""
    global _DELIVER
    _DELIVER = deliver_once


@DBOS.step()
async def _deliver_step(outbound_id: int) -> str:
    """One delivery attempt as a durable step. DBOS memoizes the outcome, so a
    completed delivery is not repeated on recovery; an interrupted one re-enters
    here and `deliver_once` reads its own `dispatching` marker."""
    assert _DELIVER is not None, "register_outbox was not called"
    return await _DELIVER(outbound_id)


@DBOS.workflow()
async def _deliver_outbound(outbound_id: int) -> str:
    return await _deliver_step(outbound_id)


async def deliver_outbound(outbound_id: int, attempt: int) -> str:
    """Deliver one row inside a durable workflow, and wait for the outcome.

    The workflow id carries the attempt so an ordinary retry (the row back to
    `queued`, its count bumped) is a fresh workflow rather than the memoized
    result of the last one — while a crash mid-send leaves *this* workflow
    PENDING for DBOS to resume, its row still `dispatching`."""
    wfid = f"outbox-{outbound_id}-{attempt}"
    with SetWorkflowID(wfid):
        handle = await DBOS.start_workflow_async(_deliver_outbound, outbound_id)
    return await handle.get_result()


async def run_node(
    dag_name: str,
    node: Node,
    state: DAGState,
    deps: Deps,
    record: Recorder | None = None,
) -> Any:
    """Run one node OUTSIDE a workflow, through the very same `_invoke` the
    workflow uses — its clock, its retry, its redaction and its `node_runs`
    record. The pool runs node 0 this way each pass, before the durable
    workflow starts, so node 0 is timed and retried like every other node."""
    return await _invoke(dag_name, node, state, deps, record)


# ── The spine pass as a durable workflow (build-the-spine ticket 14) ─────────
#
# `task-<id>/pass-<n>`: the body is plain code (`spine.workflow.run_pass`); each
# stage it names runs as `_pass_step`, which DBOS memoizes, so a pass that
# crashes resumes at the first stage with no recorded result. The steps hold
# live handles (the store, the servers), so a step takes only serializable
# arguments and finds its callable in this slot — the `_GRAPHS` shape again.
_PASS: (
    tuple[Callable[..., Awaitable[Any]], dict[str, Callable[..., Awaitable[Any]]]]
    | None
) = None


def register_pass(
    body: Callable[..., Awaitable[Any]], steps: dict[str, Callable[..., Awaitable[Any]]]
) -> None:
    """Wire the pass body and its named steps in (`Spine.steps()`), before
    `launch`, so a pass a previous process left running resumes."""
    global _PASS
    _PASS = (body, steps)


@DBOS.step()
async def _pass_step(name: str, args: tuple) -> Any:
    assert _PASS is not None, "register_pass was not called"
    return await _PASS[1][name](*args)


async def _step(name: str, *args: Any) -> Any:
    return await _pass_step(name, args)


@DBOS.workflow()
async def _run_pass(task_id: int, pass_no: int) -> Any:
    assert _PASS is not None, "register_pass was not called"
    return await _PASS[0](task_id, pass_no, _step)


async def run_pass(task_id: int, pass_no: int, workflow_id: str) -> Any:
    """Start pass `pass_no` of a task — or join it, when a workflow of that id
    is already running or done (a restart, a second pool pass) — and wait
    for what it came to."""
    with SetWorkflowID(workflow_id):
        handle = await DBOS.start_workflow_async(_run_pass, task_id, pass_no)
    return await handle.get_result()


def launch(name: str, system_db: str) -> None:
    """Bring DBOS up on its own SQLite system database. The one place outside
    this module that used to import `dbos` — the composition root, the replay
    tool, the test harness — goes through here instead, so the vendor stays put.
    Destroy-first clears any singleton a previous launch left (tests reuse the
    process)."""
    from dbos import DBOS, DBOSConfig

    # WAL on the workflow database too (§12.1, ticket 09): the application db
    # sets it in `store/db.py`, and the two are one state — a reader on the
    # board and a writing workflow must not block each other any more here than
    # there. WAL is a persistent property of the file, set before DBOS opens
    # it, so this holds for every connection DBOS then makes.
    _enable_wal(system_db)
    DBOS.destroy(destroy_registry=False)
    DBOS(config=DBOSConfig(name=name, system_database_url=f"sqlite:///{system_db}"))
    DBOS.launch()


def _enable_wal(path: str) -> None:
    """Put a SQLite file into WAL mode, persistently. Creating the file if it
    is not there yet — an empty WAL-mode database is what DBOS then migrates."""
    import sqlite3
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    finally:
        connection.close()


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


async def run(
    dag_name: str, scope_key: ScopeKey, *, workflow_id: str | None = None
) -> dict[str, Any]:
    """Start and await a graph to completion — the final state as a dict."""
    handle = await start(dag_name, scope_key, workflow_id=workflow_id)
    return await handle.get_result()


#: DBOS's status vocabulary, mapped to the four words the board shows (ticket
#: 08): running, queued, succeeded, failed. Kept here so DBOS's terms never
#: cross the seam into the API or the page — the same reason `status` maps its
#: own three. A status not listed is treated as running (it is in flight).
_BOARD_STATUS = {
    "PENDING": "running",
    "ENQUEUED": "queued",
    "DELAYED": "queued",
    "SUCCESS": "succeeded",
    "ERROR": "failed",
    "CANCELLED": "failed",
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "failed",
}


def _workflow_view(wf: Any) -> dict[str, Any]:
    """One DBOS `WorkflowStatus` as the board reads it: the id, the workflow
    function's name, the state in Friday's four words, the queue it waits on,
    and the two timestamps as ISO strings (DBOS carries them as epoch ms; the
    board's `ago`/`shortTime` read ISO, like every other time it renders)."""
    return {
        "id": wf.workflow_id,
        "name": wf.name,
        "status": _BOARD_STATUS.get(wf.status, "running"),
        "queue": wf.queue_name,
        "created_at": _iso_ms(wf.created_at),
        "updated_at": _iso_ms(wf.updated_at),
    }


def _iso_ms(epoch_ms: int | None) -> str | None:
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).isoformat()


async def list_workflows(limit: int = 100) -> list[dict[str, Any]]:
    """The workflows the board shows (ticket 08), newest first, in Friday's
    vocabulary. `DBOSClient.list_workflows` is the source — no Conductor.

    Empty when DBOS is not running: a board opened before the first workflow, or
    a test that never launched it, asks and gets nothing rather than a 500. The
    input and output are not loaded — the board wants status, not a run's Deps
    or its (possibly large) result."""
    try:
        rows = await DBOS.list_workflows_async(
            limit=limit, sort_desc=True, load_input=False, load_output=False
        )
    except Exception:  # noqa: BLE001 - "not running yet" is an empty list, not an error
        return []
    return [_workflow_view(row) for row in rows]


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


async def cancel(workflow_id: str) -> None:
    """Stop a run for good — the task was handled or escalated out of band, so
    its suspended workflow must not sit waiting on an answer that will not come."""
    with contextlib.suppress(Exception):  # already gone is fine
        await DBOS.cancel_workflow_async(workflow_id)
