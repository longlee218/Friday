"""Node 3: read the code that is running, and only read it.

The stack frame names a file inside a container — `/app/src/orders.ts` — and
the repository is a clone on the operator's own machine. This node maps one
to the other and reads a window around the line. Nothing else: no checkout,
no worktree, no fetch (finding H).

**The slice reads the clone at its current HEAD, not the running tag** (ticket
00, "what is in"). That is wrong often enough to matter — the deployed image
may be days behind — so it is said out loud in `not_checked` rather than
quietly assumed.

**A frame is reporter-influenced text.** It arrives from a log line, and a
log line carries whatever an attacker got the service to print. So the path
is resolved and checked against the repository root before anything is
opened: `/app/../../../../etc/passwd` is a file this node refuses to read,
not a file it reads because the log said so.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from friday.dag.engine import DAGDeps, DAGState, Node, envelope

__all__ = [
    "CONTAINER_ROOTS",
    "code_of",
    "excerpt",
    "read_failing_code_node",
    "repo_file",
]

log = logging.getLogger(__name__)

#: Where a service's source sits inside its image. Stripped from a frame
#: before it is joined to the clone. Ordered longest-first at use, so
#: `/usr/src/app` is not half-matched by `/app`.
CONTAINER_ROOTS = ("/usr/src/app", "/app", "/srv/app")

#: How much of the file to read around the frame — the spec's "±15 lines
#: around the first frame", which with its header lands inside the `≤ 40
#: lines` that table allows the whole check.
BEFORE = 15
AFTER = 15

#: How many frames are worth *trying*. The spec's stack rule is "frames under
#: `/app/dist/src/` kept, `node_modules` dropped, ≤ 5".
MAX_FRAMES = 5

#: Frames that are somebody else's code. Dropped before the cap rather than
#: after it: truncating first buried the throw site under five framework
#: frames, which is the shape a NestJS trace actually has.
NOT_OURS = ("node_modules", "/internal/", "node:internal")


def repo_file(frame: str, repo_path: str) -> Path | None:
    """The frame's file inside this clone, or `None` if it is not in it.

    `None` covers both "not ours" (a `node_modules` frame, a path from
    another image) and "trying to leave the clone". The caller cannot tell
    them apart and does not need to: neither is a file this node opens.
    """
    root = Path(repo_path).expanduser()
    relative = frame
    for prefix in sorted(CONTAINER_ROOTS, key=len, reverse=True):
        if frame.startswith(prefix + "/"):
            relative = frame[len(prefix) + 1:]
            break
    else:
        if frame.startswith("/"):
            # An absolute path that is not one of ours. Joining it would
            # silently produce the frame itself, outside the clone entirely.
            return None

    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        # Traversal, from a log line. The one thing this node must not do.
        log.warning("frame %r resolves outside %s — not read", frame, repo_path)
        return None
    return candidate if candidate.is_file() else None


def excerpt(path: Path, line: int, *, before: int = BEFORE, after: int = AFTER) -> str:
    """The lines around `line`, numbered, so a diagnosis can cite one."""
    text = path.read_text(errors="replace").splitlines()
    start = max(0, line - 1 - before)
    end = min(len(text), line + after)
    width = len(str(end))
    return "\n".join(
        f"{i + 1:>{width}} {'>' if i + 1 == line else ' '} {text[i]}"
        for i in range(start, end)
    )


def code_of(result: Any) -> tuple[str, tuple[str, ...]]:
    """Node 3's envelope read back: the excerpts, and what it did not read."""
    return result.get("code", ""), tuple(result.get("not_checked", ()))


def read_failing_code_node(*, timeout_seconds: float | None = None) -> Node:
    """Build node 3. No model: a frame is a path and a number."""

    async def _read(state: DAGState, deps: DAGDeps) -> Any:
        from friday.dag.api_issue.resolve import resolved

        _, project = resolved(state["resolve"])
        found = state["find_request_log"]
        frames = [
            (str(file), line)
            for file, line in (tuple(f) for f in found.get("frames", ()))
            if not any(part in str(file) for part in NOT_OURS)
        ][:MAX_FRAMES]

        if project is None:
            return envelope(
                "skipped",
                "no project row names a repository on this machine, so no "
                "code was read",
            )
        if not frames:
            return envelope(
                "empty",
                "the dossier carries no stack frame, so there is no file to "
                "open",
            )

        repo_path = project.get("repo_path") or ""
        if not repo_path:
            # `Path("")` is `.`, so an empty root turns the check below from
            # "inside the clone" into "inside whatever directory this process
            # happens to be in" — and the node then opens whatever a log line
            # names under it. A row missing this field is no row at all.
            return envelope(
                "skipped",
                f"the project row {project.get('name', '')!r} names no "
                "repository path, so no code was read",
            )
        pieces: list[str] = []
        not_checked: list[str] = [
            f"read at the clone's current HEAD in {repo_path}, not at the "
            f"version actually running — they may differ"
        ]
        for file, line in frames:
            if pieces:
                # The first frame that resolves is the throw site; the spec
                # reads around that one. The rest are named, not opened.
                not_checked.append(f"{file}:{line} is further down the stack — not read")
                continue
            path = repo_file(file, repo_path)
            if path is None:
                not_checked.append(f"{file} is not in this clone — not read")
                continue
            try:
                pieces.append(f"--- {file}:{line}\n{excerpt(path, int(line))}")
            except OSError as exc:  # noqa: PERF203 — one bad file is not the run
                not_checked.append(f"{file} could not be read: {exc}")

        if not pieces:
            return envelope(
                "empty",
                "no frame in the dossier names a file in this clone",
                not_checked=not_checked,
            )
        return envelope(
            "ok", "", code="\n\n".join(pieces), not_checked=not_checked
        )

    return Node("read_failing_code", _read, timeout_seconds=timeout_seconds)
