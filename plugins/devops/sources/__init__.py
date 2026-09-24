"""Where facts come from, and nothing about what they are wanted for.

**One package touches the outside world's read surfaces** — Loki, `kubectl`,
the operator's clone — and nothing else does (board
`read-it-the-way-the-operator-does`, ticket 15;
`tests/test_sources_are_the_only_door.py` is the guard that says so). A node
that shells out itself is a node that has to be read to know what it can
reach.

The layering the spec draws, and the reason this package sits beside the graph
(`plugins/devops/sources/`) rather than inside it (`plugins/devops/graph/`):

| Layer | What it is | Here |
| --- | --- | --- |
| **Source** | a capability, flat and reusable: a primitive that reads one kind of thing | this package |
| **Check** | a *formula* over primitives — `FindRequestLog` is "the correlationId's lines, then path plus identifier" | `plugins/devops/graph/` |
| **Node** | the frame a run is checkpointed, timed and retried in | `friday/dag/` (the shared runner) |

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

from friday.sdk.sources import Lines, LogSource, Placement, Reads

__all__ = ["DECLARED", "Lines", "LogSource", "Placement", "Reads", "declared"]


def declared() -> frozenset[str]:
    """Every tool any reader in this package says it calls.

    What a tool server is filtered down to before anything is handed it, so
    a server offering forty tools offers the four that have a caller. Read
    off the classes rather than listed here: a list beside the classes is a
    list that disagrees with them.
    """
    from plugins.devops.sources.db import DbSource
    from plugins.devops.sources.logs import LokiSource
    from plugins.devops.sources.release import ReleaseSource

    return frozenset().union(
        *(source.TOOLS for source in (LokiSource, DbSource, ReleaseSource))
    )


#: The same set, resolved once for a caller that wants a constant.
DECLARED = declared()
