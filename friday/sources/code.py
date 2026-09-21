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

import json
import logging
from pathlib import Path

__all__ = ["CONTAINER_ROOTS", "NOT_OURS", "excerpt", "original", "repo_file"]

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


#: Base64 VLQ, as source maps encode every number in `mappings`.
_VLQ = {c: i for i, c in enumerate(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)}


def _numbers(segment: str) -> list[int]:
    """One segment's fields, decoded. Each is a signed delta on the last.

    **An unknown character rejects the whole map**, rather than returning
    what was decoded so far. Every field is a delta on the last, so a
    half-read segment is dropped by the caller *along with its deltas* and
    every later segment on that line is then off by whatever it carried — a
    corrupt map would answer with a confident wrong line, which is the one
    thing this function exists to prevent. Found by review.
    """
    found, value, shift = [], 0, 0
    for char in segment:
        digit = _VLQ.get(char)
        if digit is None:
            raise ValueError(f"{char!r} is not base64 VLQ")
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
            continue
        found.append(-(value >> 1) if value & 1 else value >> 1)
        value, shift = 0, 0
    return found


def original(compiled: Path, line: int, root: Path | str) -> tuple[Path, int] | None:
    """The source file and line a compiled one came from, via its `.js.map`.

    **A stack frame from a NestJS service names `dist/src/x/y.js:80`, and
    line 80 of the TypeScript is not the same code.** Mapping the file and
    keeping the line would hand `Diagnose` fifteen lines of the wrong place
    and call it the throw site — the same shape of confident-wrong as reading
    yesterday's log window.

    So the line is translated rather than carried. The operator's clone
    builds with source maps (measured 2026-09-21: 1,725 of them beside
    `dist/src`), and this reads the one next to the frame.

    `None` when there is no map, it does not parse, or it has nothing for
    that line — every one of which means "read the compiled file as it is and
    say so", not "guess".
    """
    # No `is_file()` first: a missing map raises `OSError` here like an
    # unreadable one, and two ways to reach the same `None` is one more
    # branch than the behaviour has.
    beside = compiled.with_suffix(compiled.suffix + ".map")
    try:
        loaded = json.loads(beside.read_text())
        sources = loaded["sources"]
        mappings = loaded["mappings"]
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("%s is not a source map this can read", beside)
        return None

    try:
        return _walk(loaded, sources, mappings, compiled, line, Path(root))
    except (ValueError, TypeError, IndexError, OSError):
        # A `sources` holding `null` (legal in v3 beside `sourcesContent`),
        # an index the deltas ran past the end of, a character that is not
        # VLQ. Every one of them is a map this cannot read, and the caller's
        # contract is `None` — not an exception out of a graph node.
        log.warning("%s could not be followed to line %d", beside, line)
        return None


def _walk(
    loaded: dict, sources: list, mappings: str,
    compiled: Path, line: int, root: Path,
) -> tuple[Path, int] | None:
    # Walk to the generated line, carrying the deltas: every field in a
    # source map is relative to the previous segment, and `source` and
    # `originalLine` carry across lines while the column resets.
    source_index = original_line = 0
    for number, generated in enumerate(mappings.split(";"), start=1):
        for segment in generated.split(","):
            fields = _numbers(segment)
            if len(fields) < 4:
                continue
            source_index += fields[1]
            original_line += fields[2]
            if number == line:
                # No isinstance guard on `sources[i]`: a `null` there — legal
                # in v3 beside `sourcesContent` — makes this `TypeError`, and
                # the caller already turns that into the same `None`. Two
                # roads to one answer is one branch more than the behaviour.
                where = (
                    compiled.parent / (loaded.get("sourceRoot") or "")
                    / sources[source_index]
                ).resolve()
                try:
                    where.relative_to(root.expanduser().resolve())
                except ValueError:
                    log.warning("%s names %s, outside the clone", compiled, where)
                    return None
                return (where, original_line + 1) if where.is_file() else None
        if number > line:
            break
    return None
