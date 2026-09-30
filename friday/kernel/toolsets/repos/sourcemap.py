"""`core.repos`'s `.js.map` translation: a compiled stack frame's line,
followed back to the TypeScript that produced it.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines. Used only by `read.py`, kept as its own file because the
VLQ decoder is a self-contained algorithm nobody needs to read while working
on the tool itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

__all__ = ["original"]

log = logging.getLogger(__name__)

#: Base64 VLQ, as source maps encode every number in `mappings`.
_VLQ = {
    c: i
    for i, c in enumerate(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    )
}


def _numbers(segment: str) -> list[int]:
    """One segment's fields, decoded. Each is a signed delta on the last.

    **An unknown character rejects the whole map**, rather than returning
    what was decoded so far — a half-read segment dropped along with its
    deltas would leave every later segment on that line off by whatever it
    carried, which is a confident wrong line rather than nothing.
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
    line 80 of the TypeScript is not the same code.** So the line is
    translated rather than carried.

    `None` when there is no map, it does not parse, or it has nothing for
    that line — every one of which means "read the compiled file as it is",
    not "guess".
    """
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
        log.warning("%s could not be followed to line %d", beside, line)
        return None


def _walk(
    loaded: dict,
    sources: list,
    mappings: str,
    compiled: Path,
    line: int,
    root: Path,
) -> tuple[Path, int] | None:
    source_index = original_line = 0
    for number, generated in enumerate(mappings.split(";"), start=1):
        for segment in generated.split(","):
            fields = _numbers(segment)
            if len(fields) < 4:
                continue
            source_index += fields[1]
            original_line += fields[2]
            if number == line:
                where = (
                    compiled.parent
                    / (loaded.get("sourceRoot") or "")
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
