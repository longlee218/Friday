"""A workflow as a graph of nodes, not a single function.

A planner that decides "trace this correlation id" by making five tool calls
in sequence is not a function — it is a graph. The difference matters for two
reasons this module exists to serve:

**Resume.** A planner that crashes after step three of five starts over.
Against a log store that is minutes and real money. Here the runner records
what each node produced before starting the next, so a restart continues at
the first unfinished node.

**A place to stop.** Without an explicit graph the model is at the mercy of
`max_turns`: it may stop after three steps because the prompt said so, or run
all five and run out. Naming the nodes makes the stopping points inspectable.

The runner is deliberately small. Branching is `Edge(..., when=predicate)` —
ordinary Python, not a DSL. There is no graph library here and the decision
was measured: twenty-two extra packages, a second HTTP client, and two foreign
tables in the one SQLite file, for something that fits in this file. Revisit
when durable resume spreads past two workflows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from friday.dag.state import DAGState

__all__ = [
    "DAG",
    "DAGDeps",
    "DAGRunner",
    "DAGState",
    "Edge",
    "Node",
    "NodeRun",
    "STATUSES",
    "envelope",
    "status_of",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DAGDeps:
    """What a node is handed besides the state.

    The injection point: a node that needs the database, an MCP server, or the
    task it is working on reads them here rather than closing over module
    globals. That is what lets a node be tested with a stub without touching
    anything real.
    """

    task: Any = None
    db: Any = None
    #: Tool servers this run opened. Keyed by name so a node asks for the one
    #: it needs rather than positionally.
    servers: dict[str, Any] = field(default_factory=dict)
    #: Anything a specific DAG wants to pass down that is not worth a field.
    extra: dict[str, Any] = field(default_factory=dict)


#: A node is any async callable taking the state so far and its dependencies,
#: returning what it learned. Returning `None` means "nothing to record" — the
#: node still counts as complete, so a resume does not re-run it.
NodeFn = Callable[[DAGState, DAGDeps], Awaitable[Any]]


#: What a node's result may say about how it went, when it is not an
#: `Action`. The runner itself writes `timed_out` and `error`; a node writes
#: the others. One closed set, so an edge can route on it and the board can
#: render any node without knowing what the node is.
STATUSES = frozenset({"ok", "empty", "skipped", "timed_out", "error"})


def envelope(status: str, reason: str = "", **fields: Any) -> dict[str, Any]:
    """A node's result as the one shape every node shares: a JSON dict with a
    `status` from `STATUSES` and a `reason`, plus whatever the node found."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} (known: {sorted(STATUSES)})")
    return {**fields, "status": status, "reason": reason}


def status_of(result: Any) -> str | None:
    """The envelope status of a node's result, or `None` if it has none."""
    if isinstance(result, dict) and result.get("status") in STATUSES:
        return result["status"]
    return None


def _timed_out(node: "Node") -> dict[str, Any]:
    return envelope(
        "timed_out", f"{node.name} did not finish in {node.timeout_seconds}s"
    )


@dataclass(frozen=True, slots=True)
class Node:
    """One step. The name is the key its result is stored under.

    How long it may take and what is worth trying again are declared here and
    enforced by the runner, not by the node: a node that has to remember to
    wrap itself is a node that one day does not.
    """

    name: str
    run: NodeFn
    #: The whole invoke — every attempt and every backoff — in seconds. `None`
    #: is no clock of the node's own. On expiry the node's result is
    #: `{status: timed_out}` and the run goes on along the edges.
    timeout_seconds: float | None = None
    #: What is worth another attempt — an explicit list, never a guess from
    #: the message, the same rule `Harness._attempts` follows. Anything else
    #: becomes `{status: error}` on its first occurrence.
    retry_on: tuple[type[Exception], ...] = ()
    max_attempts: int = 1
    #: The wait after the first failed attempt, doubling after each one.
    retry_backoff_seconds: float = 1.0
    #: The configured agent this node runs, if it calls a model. Named so the
    #: graph's registration can check the two clocks against each other — see
    #: `friday.dag.router.check_node_clocks`. Model calls keep their own retry
    #: in the harness; a model node lists nothing in `retry_on`.
    agent: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a node needs a name — it is the state key")
        if self.max_attempts < 1:
            raise ValueError(f"node {self.name!r}: max_attempts must be at least 1")
        if self.max_attempts > 1 and not self.retry_on:
            raise ValueError(
                f"node {self.name!r}: max_attempts={self.max_attempts} with an "
                "empty retry_on retries nothing — name the exceptions worth "
                "another attempt"
            )


@dataclass(frozen=True, slots=True)
class NodeRun:
    """One attempt at one node, as the runner saw it end.

    What `node_runs` holds a row of. `status` is the result's envelope status,
    or `ok` for a result that is not an envelope — an `Action`, a value.
    """

    dag_name: str
    dag_version: str
    node: str
    attempt: int
    status: str
    reason: str
    duration_ms: int


#: The default edge condition: always follow.
def _always(_: DAGState) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed edge, optionally conditional on the state so far.

    `when` is a plain predicate over the state. Ordinary Python expresses
    branching perfectly well; a graph language that exists to replace `if`
    puts a dialect between the author and their own code.
    """

    src: str
    dst: str
    when: Callable[[DAGState], bool] = _always


@dataclass(frozen=True, slots=True)
class DAG:
    """Nodes plus the edges between them, with one entry point.

    Validated at construction: an edge naming a node that does not exist is a
    typo that would otherwise surface as a silent early stop, hours later, on
    a task nobody is watching.
    """

    name: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...] = ()
    #: Where to start. Defaults to the first node.
    entry: str = ""

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError(f"DAG {self.name!r} has no nodes")

        names = [n.name for n in self.nodes]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(
                f"DAG {self.name!r} has duplicate node names: {sorted(duplicates)}"
            )

        known = set(names)
        for edge in self.edges:
            for end, which in ((edge.src, "src"), (edge.dst, "dst")):
                if end not in known:
                    raise ValueError(
                        f"DAG {self.name!r}: edge {which} {end!r} is not a node "
                        f"(known: {sorted(known)})"
                    )

        object.__setattr__(self, "entry", self.entry or names[0])
        if self.entry not in known:
            raise ValueError(
                f"DAG {self.name!r}: entry {self.entry!r} is not a node"
            )

    @property
    def version(self) -> str:
        """A digest of the graph's shape: its entry, its node names in order,
        and its edges. Part of the checkpoint key.

        Derived rather than declared, because a version an author has to
        remember to bump is a version that one day is not bumped. Renaming,
        adding, removing or reordering a node moves it, so a stored result
        can never be read under a name it was not produced by. A node whose
        *body* changed under the same name does not move it — that is what
        the parameters fingerprint and a new `name` are for.
        """
        shape = {
            "entry": self.entry,
            "nodes": [n.name for n in self.nodes],
            "edges": [[e.src, e.dst] for e in self.edges],
        }
        digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode())
        return digest.hexdigest()[:16]

    def node(self, name: str) -> Node:
        for candidate in self.nodes:
            if candidate.name == name:
                return candidate
        raise KeyError(f"DAG {self.name!r} has no node {name!r}")

    def next_after(self, name: str, state: DAGState) -> str | None:
        """The first outgoing edge whose condition holds. None ends the run.

        First rather than all: parallel branches are not a goal here.
        `asyncio.gather` inside a node expresses fan-out where it is actually
        wanted, without the runner needing to know about it.
        """
        for edge in self.edges:
            if edge.src == name and edge.when(state):
                return edge.dst
        return None


class DAGRunner:
    """Walks a DAG, recording each node's result before starting the next.

    The one thing it guarantees, and the whole reason it exists: **a
    completed node does not run twice.** Resume starts at the first node with
    no recorded result.

    A node that cannot decide says so by returning an `Ask`, `Reply` or
    `HandOver` like any other node deciding what the graph's answer is — there
    used to be a second way, `PauseForHuman`, raised rather than returned so
    the run could stop mid-node instead of ending after one. It dissolved
    once new reporter text re-running from node 1 (ticket 03) did everything
    "resume from the paused node" bought, which was the only thing the second
    mechanism was for.
    """

    def __init__(
        self,
        dag: DAG,
        *,
        deps: DAGDeps | None = None,
        state: DAGState | None = None,
        #: The path an earlier run walked, from the checkpoint that saved it.
        #: A resumed run appends to it rather than starting empty — see
        #: `trail`.
        trail: list[str] | None = None,
        on_checkpoint: Callable[[DAGState, list[str]], Awaitable[None]] | None = None,
        #: Given every attempt of every node this runner invokes, as it ends.
        on_node_run: Callable[["NodeRun"], Awaitable[None]] | None = None,
        max_steps: int = 50,
    ) -> None:
        self._dag = dag
        self._deps = deps or DAGDeps()
        self._state = state or DAGState.empty()
        self._on_checkpoint = on_checkpoint
        self._on_node_run = on_node_run
        #: A cycle in the edges would otherwise spin forever. The graph is
        #: meant to be acyclic; this is the guard that says so out loud.
        self._max_steps = max_steps
        self._trail: list[str] = list(trail or [])

    @property
    def state(self) -> DAGState:
        return self._state

    @property
    def trail(self) -> list[str]:
        """The nodes this run walked, in the order it walked them.

        Declaration order is not execution order, and a caller asking "what
        did the graph end up deciding?" needs the second. Reading it off the
        node tuple instead means a bookkeeping node declared last — an audit
        line, a cleanup — silently answers for the node that actually decided.

        It is checkpointed alongside the state, because a run that resumes
        after a restart would otherwise answer that question from the part of
        the path it happened to walk itself. A node already recorded is walked
        past rather than re-run, and walking past it still counts.
        """
        return list(self._trail)

    async def run(self) -> DAGState:
        """Run to the end. A node deciding to stop early is not this
        function's business to notice — the state carries its `Ask`, `Reply`
        or `HandOver` like any other result, and the caller reads it off the
        trail (see `Pool._outcome`)."""
        current = self._resume_point()
        steps = 0

        while current is not None:
            steps += 1
            if steps > self._max_steps:
                raise RuntimeError(
                    f"DAG {self._dag.name!r} exceeded {self._max_steps} steps "
                    f"at {current!r} — check the edges for a cycle"
                )

            if self._state.has(current):
                # Already done in an earlier run; walk past it without
                # re-running, and still count it as part of the path, so a
                # resumed run can answer "what did this graph decide?" the
                # same way a fresh one does.
                #
                # Unless the trail already says so. The path is now restored
                # from the checkpoint alongside the state, and `_resume_point`
                # leaves the loop pointing at the last recorded node when
                # everything has run — so appending unconditionally counted
                # that node twice on every resumed pass.
                self._record_step(current)
                current = self._dag.next_after(current, self._state)
                continue

            node = self._dag.node(current)
            log.info("dag %s: running %s", self._dag.name, node.name)
            self._record_step(node.name)
            result = await self._invoke(node)

            self._state = self._state.with_result(node.name, result)
            await self._checkpoint()

            current = self._dag.next_after(node.name, self._state)

        return self._state

    async def run_entry(self) -> Any:
        """Invoke the entry node alone, through the same invoke as every other
        node, and return its result without recording it in the state.

        For node 0, which the pool runs outside the walk on every pass —
        before there is a fingerprint to load a checkpoint by — but not
        outside the clock, the retry or the `node_runs` record.
        """
        return await self._invoke(self._dag.node(self._dag.entry))

    async def _invoke(self, node: Node) -> Any:
        """Run one node: its clock, its retry, and its failure as a result.

        The clock bounds the whole invoke, backoff included, so a node that
        fails fast and retries cannot outlive it. Only the runner's own clock
        is `timed_out`; a `TimeoutError` the node raises itself — an HTTP
        client's — is an exception like any other, retried if listed.

        An exception that is not retried, or that ran out of attempts, is the
        node's result, `{status: error}`, and the run goes on along the edges.
        A cancellation from outside is not an exception here and propagates.

        Every attempt is handed to `on_node_run` as it ends, so the record
        holds three rows for three tries rather than one for the outcome.
        """
        loop = asyncio.get_running_loop()
        deadline = (
            None if node.timeout_seconds is None else loop.time() + node.timeout_seconds
        )
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            # The clock wraps the node alone; recording and backoff happen
            # outside it, so an expiry never cancels a write half done.
            try:
                async with asyncio.timeout_at(deadline) as clock:
                    result = await node.run(self._state, self._deps)
            except Exception as exc:  # noqa: BLE001 - becomes the result
                if clock.expired():
                    # Either the clock's own TimeoutError, or the node swallowed
                    # the cancellation and raised something else; the clock ran
                    # out either way.
                    return await self._ended(node, attempt, started, _timed_out(node))
                reason = f"{type(exc).__name__}: {exc}"
                retryable = isinstance(exc, node.retry_on)
                if not retryable or attempt >= node.max_attempts:
                    if retryable:
                        reason = f"gave up after {attempt} attempts — {reason}"
                    log.warning(
                        "dag %s: %s failed — %s", self._dag.name, node.name, reason
                    )
                    return await self._ended(
                        node, attempt, started, envelope("error", reason)
                    )
                await self._ended(node, attempt, started, envelope("error", reason))
            else:
                return await self._ended(node, attempt, started, result)

            wait = node.retry_backoff_seconds * 2 ** (attempt - 1)
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - loop.time()))
            await asyncio.sleep(wait)
            if deadline is not None and loop.time() >= deadline:
                # Out of time in the backoff: no attempt ran, so none is
                # recorded, and the result is the clock's all the same.
                return _timed_out(node)

    async def _ended(self, node: Node, attempt: int, started: float, result: Any) -> Any:
        """Hand one finished attempt to the sink, and pass its result through.

        A failed save must not lose the run — the same rule `_checkpoint`
        follows, for the same reason.
        """
        if self._on_node_run is not None:
            run = NodeRun(
                dag_name=self._dag.name,
                dag_version=self._dag.version,
                node=node.name,
                attempt=attempt,
                status=status_of(result) or "ok",
                reason=result.get("reason", "") if status_of(result) else "",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            try:
                await self._on_node_run(run)
            except Exception:  # noqa: BLE001 - a failed record must not lose the run
                log.exception(
                    "dag %s: could not record attempt %d of %s",
                    self._dag.name,
                    attempt,
                    node.name,
                )
        return result

    def _record_step(self, name: str) -> None:
        """Put a node on the path, once.

        Once because the path is restored from the checkpoint and a resumed
        run walks it again — and because a node can genuinely *re-run* on that
        pass: a result that does not survive JSON is dropped on load, so the
        node is not recorded and runs a second time. Appending unconditionally
        counted such a node twice and grew the row by one entry per pass.

        The graph is acyclic, so a name appearing once is the whole of what
        callers need: `Pool._outcome` reads the path backwards for the node
        that produced the run's `Action`, and multiplicity says nothing it
        asks about.
        """
        if name not in self._trail:
            self._trail.append(name)

    def _resume_point(self) -> str:
        """Where to start: the entry, unless earlier nodes already ran.

        Walking forward from the entry rather than trusting a stored cursor
        means the state itself decides, and a state written by an older
        version of the DAG still resumes somewhere sensible.
        """
        current = self._dag.entry
        seen: set[str] = set()
        while current is not None and self._state.has(current):
            if current in seen:  # cycle in a stored state; stop trusting it
                return current
            seen.add(current)
            following = self._dag.next_after(current, self._state)
            if following is None:
                return current  # everything ran; run() will walk past it
            current = following
        return current

    async def _checkpoint(self) -> None:
        if self._on_checkpoint is None:
            return
        try:
            await self._on_checkpoint(self._state, list(self._trail))
        except Exception:  # noqa: BLE001 - a failed save must not lose the run
            log.exception(
                "dag %s: could not checkpoint; the run continues but a "
                "restart will repeat from the last saved node",
                self._dag.name,
            )
