"""The source ports: read-surface contracts a plugin's sources implement.

Contracts only — dataclasses and Protocols, no I/O, no third-party imports.
A *source* is a capability that reads one kind of thing (log lines, a file at a
frame); the concrete readers (`LokiSource`, `SshKubectlSource`, a repo clone)
live in the plugin that ships them and implement these ports, so the kernel and
the workflow port name the shape without depending on the reader (DESIGN-v2 §4,
§6.4).

Moved here from `friday/sources/__init__.py` in ticket 14: the ports are what a
plugin codes against, so they belong in `sdk`; the doors that actually shell out
move into `plugins/devops/sources/`. A source is read-only by construction —
there is no verb here that writes — and holds no judgement: which window, which
identifiers, which service arrive as arguments.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "CodeSource",
    "Lines",
    "LogSource",
    "Placement",
    "Reads",
    "TOOL_CALL_TIMEOUT_SECONDS",
]

#: How long one tool call — an SSH `kubectl`, an MCP call, a git read — may
#: take. **The only time limit left** (board `domains-plug-in`, ticket 17): an
#: agent run stops on turns or tokens, and this is what keeps a hung read from
#: holding a pool slot. Measured reads take one to two seconds.
TOOL_CALL_TIMEOUT_SECONDS = 30.0


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
        return await asyncio.wait_for(
            self.server.call_tool(tool, arguments), timeout=TOOL_CALL_TIMEOUT_SECONDS
        )


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one case lives — the single type, folded from `Resolve`'s old
    `Placement` + `project` dict and `plugins.devops.graph.intake`'s own
    (ticket 6): every reader wants one place, and a reader that has to
    remember which of two shapes to read is one that one day reads the
    other.

    The address a `LogSource` reads from, and the reason it lives here rather
    than beside whatever resolved it: a source that imported the graph that
    calls it would make the capability depend on the workflow, which is the
    direction this package exists to prevent.

    Flattened out of a service's two halves on purpose — every reader wants
    one place, and a reader that has to remember which half to read is one that
    one day reads the other.
    """

    env: str
    service: str = ""
    #: Production: the Loki labels. Dev: empty.
    cluster: str = ""
    namespace: str = ""
    app: str = ""
    #: Dev: the pod name pattern to grep for. Production: empty.
    pod_pattern: str = ""
    #: Where the operator's clone is. `clone_path`/`repo_path` are the same
    #: path today — kept as two fields because `Resolve`'s two halves named
    #: them separately and nothing yet forces them to agree.
    clone_path: str = ""
    repo_path: str = ""
    #: The running version, when a `CodeSource` can compare a frame against
    #: it, or `""` when it could not be resolved.
    release_tag: str = ""
    #: What the repository's own error-code doc says each code means.
    error_code_doc: str = ""
    #: The service's tech stack (e.g. "NestJS"), a hint the diagnose prompt
    #: gives the model so it reads a stack trace in that framework's idiom.
    stack: str = ""
    #: The container source roots and vendored-path markers a `CodeSource`
    #: maps a frame against.
    container_roots: tuple[str, ...] = ()
    dbs: tuple[str, ...] = ()
    #: The room's candidate services, when `service` is unresolved (vague or
    #: no match). Empty once a service is resolved.
    candidates: tuple[str, ...] = ()


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

    `newest` is the other end, and it is there because `truncated` alone was
    not enough. A back end that caps at N lines returns the N *newest*, so a
    thirty-five minute window came back as its last eighty-four seconds and
    said only "this is a sample". Which eighty-four seconds is the difference
    between a dossier that could have held the request and one that never
    could: with both ends the node can say so, and a reader can tell "your
    request is not in the log" from "your request is not in what was read".
    """

    lines: tuple[str, ...] = ()
    oldest: datetime | None = None
    newest: datetime | None = None
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

    **`needle` is here, and not left to the caller to filter for, because
    `limit` is a tail rather than a sample.** Measured against production on
    2026-09-21: a thirty-five minute window of one busy service is ~12,400
    lines, `limit=400` returned the newest 400 — eighty-four seconds, three
    per cent — and the request being investigated was twenty-three minutes
    outside it. Filtering after the read cannot recover a line the read
    never fetched. Asking the back end for the lines that carry a string
    returned that request whole, in two lines, untruncated.

    So *which* lines is part of the read, not a step after it. Which back
    ends can narrow a read, and how, is exactly the kind of thing this layer
    exists to know; a source that cannot is free to ignore it, and the
    caller's own filtering still runs either way.
    """

    name: str

    async def lines(
        self,
        placement: Placement,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        needle: str = "",
    ) -> Lines: ...


class CodeSource(Protocol):
    """The operator's clone, read the way a diagnosis needs it.

    A frame names a file inside a container (`/app/src/orders.ts:80`) and the
    repository is a clone on the operator's machine; a `CodeSource` maps one to
    the other and reads a window around the line — nothing else: no checkout, no
    worktree, no fetch (finding H). **A frame is reporter-influenced text**, so
    a path that resolves outside the clone is refused, not read.

    The port is the read surface the `Diagnose` loop's `read_code` tool calls;
    the concrete reader (a repo clone, its container-root policy loaded from
    config or a memory row) implements it in the plugin. Every method returns
    `None` for "not here / cannot read", never an exception into a graph node.
    """

    def repo_file(self, frame: str) -> Path | None:
        """The frame's file inside this clone, or `None` if it is not in it —
        which covers both "not ours" and "trying to leave the clone"."""
        ...

    def excerpt(self, path: Path, line: int) -> str:
        """The lines around `line`, numbered, so a diagnosis can cite one."""
        ...

    def original(self, compiled: Path, line: int) -> tuple[Path, int] | None:
        """The source file and line a compiled one came from, via its
        `.js.map`, or `None` when there is no usable map."""
        ...

    def at_ref(self, path: Path, ref: str) -> str | None:
        """The file's text as it is at a git ref (`git show`, never a
        checkout), or `None` for every way of not having it."""
        ...

    def meanings(self, codes: tuple[str, ...]) -> dict[str, str]:
        """What the repo's own error-code doc says each of these codes means —
        only the codes that turned up; a code the doc does not list is absent."""
        ...
