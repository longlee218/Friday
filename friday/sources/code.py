"""Reading the source that a stack frame names, and only reading it.

A frame names a file inside a container — `/app/src/orders.ts` — and the
repository is a clone on the operator's own machine. These two functions map
one to the other and read a window around the line. Nothing else: no
checkout, no worktree, no fetch (finding H).

**A frame is reporter-influenced text.** It arrives from a log line, and a
log line carries whatever an attacker got the service to print. So the path
is resolved and checked against the repository root before anything is
opened: `/app/../../../../etc/passwd` is a file this refuses to read, not a
file it reads because the log said so.

`CodeSource`'s other primitives — `grep`, `explore`, `doc` — are ticket 04's.
They are not declared here as empty protocols: a shape with one
implementation and no second caller is a guess about the second one.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = ["CONTAINER_ROOTS", "NOT_OURS", "excerpt", "repo_file"]

log = logging.getLogger(__name__)

#: Where a service's source sits inside its image. Stripped from a frame
#: before it is joined to the clone. Ordered longest-first at use, so
#: `/usr/src/app` is not half-matched by `/app`.
CONTAINER_ROOTS = ("/usr/src/app", "/app", "/srv/app")

#: Frames that are somebody else's code. Named here beside the mapping,
#: applied by whoever is choosing frames: which of them to open is a
#: judgement, and this package holds none.
NOT_OURS = ("node_modules", "/internal/", "node:internal")

#: How much of a file to read around a frame — the spec's "±15 lines around
#: the first frame", which with its header lands inside the `≤ 40 lines` that
#: table allows the whole check.
BEFORE = 15
AFTER = 15


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
