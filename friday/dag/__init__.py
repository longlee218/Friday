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

import logging
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


@dataclass(frozen=True, slots=True)
class Node:
    """One step. The name is the key its result is stored under."""

    name: str
    run: NodeFn

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a node needs a name — it is the state key")


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
        on_checkpoint: Callable[[DAGState], Awaitable[None]] | None = None,
        max_steps: int = 50,
    ) -> None:
        self._dag = dag
        self._deps = deps or DAGDeps()
        self._state = state or DAGState.empty()
        self._on_checkpoint = on_checkpoint
        #: A cycle in the edges would otherwise spin forever. The graph is
        #: meant to be acyclic; this is the guard that says so out loud.
        self._max_steps = max_steps
        self._trail: list[str] = []

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
        """
        return list(self._trail)

    async def run(self) -> DAGState:
        """Run to the end. A node deciding to stop early is not this
        function's business to notice — the state carries its `Ask`, `Reply`
        or `HandOver` like any other result, and the caller reads it off the
        trail (see `WorkflowRunner._outcome`)."""
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
                # `_resume_point` normally walks past these before the loop
                # starts, so this branch is reached only when the state was
                # written by a differently-shaped graph — a node that used to
                # be skipped now sitting on the path. Which is exactly when
                # counting it matters.
                self._trail.append(current)
                current = self._dag.next_after(current, self._state)
                continue

            node = self._dag.node(current)
            log.info("dag %s: running %s", self._dag.name, node.name)
            self._trail.append(node.name)
            result = await node.run(self._state, self._deps)

            self._state = self._state.with_result(node.name, result)
            await self._checkpoint()

            current = self._dag.next_after(node.name, self._state)

        return self._state

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
            await self._on_checkpoint(self._state)
        except Exception:  # noqa: BLE001 - a failed save must not lose the run
            log.exception(
                "dag %s: could not checkpoint; the run continues but a "
                "restart will repeat from the last saved node",
                self._dag.name,
            )
