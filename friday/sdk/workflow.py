"""The workflow port: Friday's own graph vocabulary, no engine inside.

A task type's work is a graph of nodes, not a single function — a planner that
makes five tool calls in sequence is a graph, and the graph is what makes the
stopping points inspectable and the run resumable. This module is the contract
a plugin author codes against: `Node`, `Edge`, `DAG`, the `envelope`, the
`Ask`/`Reply`/`HandOver` actions, and the `Deps` a node is handed.

**It is a port, not an engine.** Durability — run persistence, per-step
memoization and resume — belongs to DBOS, reached through the adapter in
`friday/kernel/dag/adapter.py`, the one module that imports `dbos`. Nothing here
imports it, so the contract stays stable while the engine underneath is a
library (DESIGN-v2 §7). The v1 `DAG.version` source-digest is gone: recovery is
DBOS's (step name + application version), resuming from the last incomplete
step, not a re-run on a digest mismatch.

A workflow's input is a **serializable scope key**; the live handles a node
needs (`db`, tool servers, log sources) do not serialize, so the adapter
rebuilds `Deps` from the scope key at the start of each run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from friday.sdk.actions import Ask, HandOver, Outcome, Reply
from friday.sdk.workflow_state import (
    UNSTORABLE,
    DAGState,
    MissingNodeResult,
)

__all__ = [
    "DAG",
    "STATUSES",
    "UNSTORABLE",
    "Ask",
    "DAGState",
    "Deps",
    "DepsFactory",
    "Edge",
    "HandOver",
    "MissingNodeResult",
    "Node",
    "NodeFn",
    "NodeRun",
    "Outcome",
    "Reply",
    "ScopeKey",
    "envelope",
    "status_of",
]


#: A workflow's input: JSON-serializable, so DBOS can persist it and resume the
#: run after a restart. It names *what* to run for (a task id, its type), never
#: the live handles that run needs — those are rebuilt by the `DepsFactory`.
ScopeKey = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Deps:
    """What a node is handed besides the state: the live handles.

    Rebuilt inside the run from the scope key (they do not serialize), so a
    node reads the database, a tool server or the task it works on from here
    rather than a module global — which is what lets a node be tested with a
    stub without touching anything real.
    """

    task: Any = None
    db: Any = None
    #: Tool servers this run opened, keyed by name so a node asks for the one
    #: it needs rather than positionally.
    servers: dict[str, Any] = field(default_factory=dict)
    #: Anything a specific graph wants to pass down that is not worth a field.
    extra: dict[str, Any] = field(default_factory=dict)
    #: The answers a suspended node has been given, oldest first. A node that
    #: returns `Ask` suspends the workflow; when the reporter answers, the same
    #: node **re-runs** with the answer appended here, and searches again with
    #: what it now knows (the durable version of v1's "answer re-runs the asking
    #: node"). Empty for a node that has not asked. The adapter refills this
    #: with the current node's answers before each run.
    answers: list[Any] = field(default_factory=list)


#: A node is any async callable taking the state so far and its dependencies,
#: returning what it learned. Returning `None` means "nothing to record" — the
#: node still counts complete, so a resume does not re-run it.
NodeFn = Callable[[DAGState, Deps], Awaitable[Any]]

#: Given a scope key, build the live `Deps` for a run. Async because building
#: them may open a connection. Registered with the graph, never serialized.
DepsFactory = Callable[[ScopeKey], Awaitable[Deps]]


#: What a node's result may say about how it went, when it is not an `Outcome`.
#: The runner writes `error`; a node writes the others. `timed_out` is what the
#: runner wrote while nodes had clocks, kept so older rows still read. One
#: closed set, so an edge can route on it and the board can render any node.
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


@dataclass(frozen=True, slots=True)
class Node:
    """One step. The name is the key its result is stored under.

    What is worth trying again is declared here and enforced by the runner,
    not by the node: a node that has to remember to
    wrap itself is a node that one day does not.
    """

    name: str
    run: NodeFn
    #: What is worth another attempt — an explicit list, never a guess from the
    #: message. Anything else becomes `{status: error}` on its first occurrence.
    retry_on: tuple[type[Exception], ...] = ()
    max_attempts: int = 1
    #: The wait after the first failed attempt, doubling after each one.
    retry_backoff_seconds: float = 1.0
    #: The configured agent this node runs, if it calls a model. Model calls
    #: keep their own retry in the harness; a model node lists nothing in
    #: `retry_on`.
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
    or `ok` for a result that is not an envelope — an `Outcome`, a value.
    """

    dag_name: str
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
    branching perfectly well; a graph language that exists to replace `if` puts
    a dialect between the author and their own code.
    """

    src: str
    dst: str
    when: Callable[[DAGState], bool] = _always


@dataclass(frozen=True, slots=True)
class DAG:
    """Nodes plus the edges between them, with one entry point.

    Validated at construction: an edge naming a node that does not exist is a
    typo that would otherwise surface as a silent early stop, hours later.

    No `version` property: unlike the hand-written engine, this graph does not
    key its own checkpoints — DBOS does, by step name and application version.
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
            raise ValueError(f"DAG {self.name!r}: entry {self.entry!r} is not a node")

    def node(self, name: str) -> Node:
        for candidate in self.nodes:
            if candidate.name == name:
                return candidate
        raise KeyError(f"DAG {self.name!r} has no node {name!r}")

    def next_after(self, name: str, state: DAGState) -> str | None:
        """The first outgoing edge whose condition holds. None ends the run.

        First rather than all: parallel branches are not a goal here.
        `asyncio.gather` inside a node expresses fan-out where it is wanted.
        """
        for edge in self.edges:
            if edge.src == name and edge.when(state):
                return edge.dst
        return None
