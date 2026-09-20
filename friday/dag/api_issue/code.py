"""Node 3: which frame is worth opening, and what was not opened.

The reading itself is `friday/sources/code.py`; this is the formula over it
— drop somebody else's frames, take the first that resolves in the clone,
and name the rest rather than opening them (the spec's "±15 lines around the
first frame").

**The slice reads the clone at its current HEAD, not the running tag**
(ticket 00, "what is in"). That is wrong often enough to matter — the
deployed image may be days behind — so it is said out loud in `not_checked`
rather than quietly assumed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from friday.dag.engine import DAGDeps, DAGState, Node, envelope
from friday.sources.code import NOT_OURS, excerpt, repo_file

__all__ = ["code_of", "read_failing_code_node"]

log = logging.getLogger(__name__)

#: How many frames are worth *trying*. The spec's stack rule is "frames under
#: `/app/dist/src/` kept, `node_modules` dropped, ≤ 5".
MAX_FRAMES = 5


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
