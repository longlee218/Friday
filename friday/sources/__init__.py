"""Where facts come from, and nothing about what they are wanted for.

**One package touches the outside world's read surfaces** — Loki, `kubectl`,
the operator's clone — and nothing else does (board
`read-it-the-way-the-operator-does`, ticket 15;
`tests/test_sources_are_the_only_door.py` is the guard that says so). A node
that shells out itself is a node that has to be read to know what it can
reach.

The layering the spec draws, and the reason this package is not inside
`friday/dag/api_issue/`:

| Layer | What it is | Here |
| --- | --- | --- |
| **Source** | a capability, flat and reusable: a primitive that reads one kind of thing | this package |
| **Check** | a *formula* over primitives — `FindRequestLog` is "the correlationId's lines, then path plus identifier" | the graph's own modules |
| **Node** | the frame a run is checkpointed, timed and retried in | `friday/dag/` |

A source is read-only by construction rather than by instruction: there is
no verb here that writes. It also holds no judgement — which window, which
identifiers, which service is somebody else's decision, arriving as
arguments. That is what lets the same `LogSource` serve a check that decides
by rule and, later, a Collector tool a model drives (ticket 15: "The
difference is **who decides what to look for**, never what is called").

**No agent imports.** Nothing here may reach for `friday/agent/`: a source
that can call a model is a source that can be talked into reading something
else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

__all__ = ["DECLARED", "Lines", "LogSource", "Placement", "Reads", "declared"]


@dataclass(frozen=True, slots=True)
class Reads:
    """A tool server, narrowed to the calls one reader declared.

    **The allow-list is code, not configuration** (the operator's call,
    2026-09-21). It was a line in `config.yaml`, and a guard that a file can
    widen is a guard the file's next editor widens by accident — on a server
    that also offers `release_rollback` and `godaddy_dns_edit_record`.

    Two layers, and the second is the one that holds. The server is built
    with a filter over the same declarations, so an agent handed it never
    sees anything else; and every call goes through here, so code that asks
    for a tool its class did not declare is refused in this process whatever
    the filter did. Configuration may still choose *which* declared tool to
    use — a server that spells the same read differently — and cannot add
    one.
    """

    server: Any
    allowed: frozenset[str]

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Named `call` rather than `call_tool`, which is what the server
        underneath calls it. A `Reads` is deliberately *not* a drop-in for a
        raw server: if it were, handing a reader an unnarrowed server would
        type-check and run, and the narrowing would be the thing somebody
        forgets. It is an `AttributeError` at the first call instead."""
        if tool not in self.allowed:
            raise PermissionError(
                f"{tool!r} is not declared by this reader: it may call "
                f"{sorted(self.allowed)}. Add it to the class's own TOOLS if "
                f"it is a read, and nowhere else."
            )
        return await self.server.call_tool(tool, arguments)


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one service's logs are, for the environment in hand.

    The address a `LogSource` reads from, and the reason it lives here rather
    than beside whatever resolved it: a source that imported the graph that
    calls it would make the capability depend on the workflow, which is the
    direction this package exists to prevent.

    Flattened out of `ServiceData`'s two halves on purpose — every reader
    wants one place, and a reader that has to remember which half to read is
    one that one day reads the other.
    """

    env: str
    service: str
    #: Production: the Loki labels. Dev: empty.
    cluster: str = ""
    namespace: str = ""
    app: str = ""
    #: Dev: the pod name pattern to grep for. Production: empty.
    pod_pattern: str = ""


@dataclass(frozen=True, slots=True)
class Lines:
    """What a source returned, and what it can say about what it did not.

    `oldest` is the oldest line the back end handed over, **whatever the
    window asked for** — the first real run needed it. A dev pod keeps only
    what it has logged since its last restart, and a request from sixteen
    hours earlier is simply gone; without this the node could not tell "your
    window holds nothing" from "this pod does not reach back that far", and
    the second is the one an operator can act on.

    `None` when the source cannot say, which is every source whose back end
    honours the window itself.
    """

    lines: tuple[str, ...] = ()
    oldest: datetime | None = None
    #: The back end capped what it returned, so this is a sample rather than
    #: the window. Loki says so outright (`truncated: true`, with its own
    #: instruction: "narrow the query rather than assuming you saw
    #: everything"); a node that does not pass that on is a node quietly
    #: reasoning over a sample it believes is everything.
    truncated: bool = False


class LogSource(Protocol):
    """One place log lines come from.

    `since`/`until` are absolute rather than a duration, because the window a
    caller wants is around the reporter's message and not around now (D5).
    Every relative form any of these back ends offers is relative to the
    clock on the far side.

    **A source returns only what is inside the window.** Clipping is the
    source's job because only the source knows how its back end stamps a
    line, and a back end that cannot be asked for an upper bound has to be
    clipped after the fact rather than trusted.
    """

    name: str

    async def lines(
        self, placement: Placement, *, since: datetime, until: datetime, limit: int
    ) -> Lines: ...


def declared() -> frozenset[str]:
    """Every tool any reader in this package says it calls.

    What a tool server is filtered down to before anything is handed it, so
    a server offering forty tools offers the four that have a caller. Read
    off the classes rather than listed here: a list beside the classes is a
    list that disagrees with them.
    """
    from friday.sources.db import DbSource
    from friday.sources.logs import LokiSource

    return frozenset().union(
        *(source.TOOLS for source in (LokiSource, DbSource))
    )


#: The same set, resolved once for a caller that wants a constant.
DECLARED = declared()
