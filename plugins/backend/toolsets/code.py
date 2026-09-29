"""`backend.code`: reading a repository of the room, and only reading it.

**One file per data source, tools and client together** (board
`domains-plug-in` ticket 09 §3): the git reads below and the three tools a
model calls over them — `read_code`, `search_code`, `what_code_means`. Over
the 200-line soft target for that reason: splitting the client from its tools
is the split this layout was decided against.

A frame names a file inside a container — `/app/src/orders.ts` — and the
repository is a clone on the operator's own machine. These functions map one
to the other and read a window around the line. Nothing else: no checkout, no
worktree, no fetch (finding H).

**The version read is the one running** (ticket 04 §6): each tool asks
`RunningVersion` for the service's tag and reads with `git show <tag>:<file>`.
When the tag cannot be known, or the clone does not have it, the tool reads
the checkout and says so in its answer and in `not_checked`.

**A frame is reporter-influenced text.** It arrives from a log line, and a
log line carries whatever an attacker got the service to print. So the path
is resolved and checked against the repository root before anything is
opened: `/app/../../../../etc/passwd` is a file this refuses to read, not a
file it reads because the log said so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from friday.sdk.evidence import Evidence
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS
from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.placement import Placement
from plugins.backend.toolsets.release import (
    RELEASE_SERVER,
    ReleaseSource,
    RunningVersion,
)

__all__ = [
    "CODE",
    "CONTAINER_ROOTS",
    "at_ref",
    "code_tools",
    "excerpt",
    "grep",
    "meanings",
    "original",
    "repo_file",
    "unknown_repo",
]

log = logging.getLogger(__name__)

#: Where a service's source sits inside its image, stripped from a stack frame
#: before it is joined to the clone. A constant since ticket 09: it was a
#: `backend:` setting nobody set.
CONTAINER_ROOTS = ("/usr/src/app", "/app", "/srv/app")

#: The most matches one `search_code` returns; past it the search says so.
MAX_HITS = 40

#: A matched line longer than this is cut: a minified bundle is one line.
HIT_CHARS = 300

#: How much of a file to read around a frame — the spec's "±15 lines around
#: the first frame", which with its header lands inside the `≤ 40 lines` that
#: table allows the whole check.
BEFORE = 15
AFTER = 15


def repo_file(
    frame: str,
    repo_path: str,
    *,
    container_roots: tuple[str, ...],
    exists: bool = True,
) -> Path | None:
    """The frame's file inside this clone, or `None` if it is not in it.

    `None` covers both "not ours" (a `node_modules` frame, a path from
    another image) and "trying to leave the clone". The caller cannot tell
    them apart and does not need to: neither is a file this node opens.

    `container_roots` is the image's source-root policy (`CONTAINER_ROOTS`),
    an argument so a test can name another layout. `exists=False` returns the
    confined path even when the checkout has no such file — one the running
    tag may still hold (`git show` reads it there).
    """
    root = Path(repo_path).expanduser()
    relative = frame
    for prefix in sorted(container_roots, key=len, reverse=True):
        if frame.startswith(prefix + "/"):
            relative = frame[len(prefix) + 1 :]
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
    return candidate if candidate.is_file() or not exists else None


def at_ref(repo_path: str, path: Path, ref: str) -> str | None:
    """The file's text as it is at a git ref, or `None`.

    **`git show`, never a checkout and never a worktree.** The clone belongs
    to the operator and is open in their editor; moving its HEAD to answer a
    question is the one thing this must not do, and a detached worktree is
    disk, cleanup and a failure mode for a read that needs none of it.

    `None` for every way of not having it — no such ref, the file did not
    exist at it, git not on PATH. The caller says so and reads what it has.
    """
    if not ref or ref.startswith("-"):
        # A ref out of an MCP answer, going onto a command line. `git show`
        # takes no `--` before its `rev:path`, so a leading dash would be
        # read as an option; nothing else here can make it one.
        return None
    root = Path(repo_path).expanduser().resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{relative.as_posix()}"],
            capture_output=True,
            text=True,
            timeout=TOOL_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # The docstring above promises `None` for "git not on PATH", and it
        # was not true: `FileNotFoundError` and `TimeoutExpired` both came
        # out of here. A caller that read the promise and did not guard is a
        # caller this function misled.
        log.warning("git show %s: %s", ref, exc)
        return None
    return done.stdout if done.returncode == 0 else None


def numbered(text: str, line: int, *, before: int = BEFORE, after: int = AFTER) -> str:
    """The lines around `line`, numbered, so a diagnosis can cite one.

    Split out of `excerpt` so the same window can be taken of text that is
    not on disk — what a file looked like at the tag that is running.
    """
    lines = text.splitlines()
    start = max(0, line - 1 - before)
    end = min(len(lines), line + after)
    width = len(str(end))
    return "\n".join(
        f"{i + 1:>{width}} {'>' if i + 1 == line else ' '} {lines[i]}"
        for i in range(start, end)
    )


def excerpt(path: Path, line: int, *, before: int = BEFORE, after: int = AFTER) -> str:
    """The lines around `line`, numbered, so a diagnosis can cite one."""
    return numbered(path.read_text(errors="replace"), line, before=before, after=after)


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
    loaded: dict,
    sources: list,
    mappings: str,
    compiled: Path,
    line: int,
    root: Path,
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


#: A markdown table row: `| \`ERR19\` | NAME | Meaning |`. The repo's own
#: `docs/llm/error-codes.md` is this shape, and a doc that is not yields
#: nothing — which the node reports rather than guessing a format.
_ROW = re.compile(r"^\s*\|(?P<cells>.+)\|\s*$")


def meanings(doc: Path | str, codes: Sequence[str], root: Path | str) -> dict[str, str]:
    """What the repo says each of these error codes means.

    **Only the codes that turned up.** Ticket 16 measured every one of 2,104
    HTTP 500s carrying `ERR19` — the generic code — so without this a
    diagnosis sees a number and can say nothing; and the doc it comes from
    is 271 lines, which is not a thing to put in a prompt to explain three.
    The same rule `describe_schema` gets: a check that needs a document needs
    the rows it asked about.

    Confined to `root` like everything else this module opens. The path
    arrives from a `project` row somebody typed, and a path somebody typed is
    still a path.

    A code the doc does not list is simply absent. The gap is the honest
    answer — a model told "ERR999 means nothing is known" has been told
    something; a model shown nothing for it has not.
    """
    where = Path(doc).expanduser().resolve()
    try:
        where.relative_to(Path(root).expanduser().resolve())
        text = where.read_text(errors="replace")
    except (ValueError, OSError):
        log.warning("%s is not a document inside %s", doc, root)
        return {}

    wanted = {code.strip().upper() for code in codes if code}
    found: dict[str, str] = {}
    for line in text.splitlines():
        row = _ROW.match(line)
        if row is None:
            continue
        cells = [
            cell.strip().strip("`").strip() for cell in row.group("cells").split("|")
        ]
        # **Two cells or three.** The real document has both: 130 rows of
        # `code | name | meaning` and 69 of `code | meaning`, and a parser
        # that wanted three silently dropped every Midas code — `ERR306`
        # among them, which ticket 16 measured 3,455 times in 30 days.
        if len(cells) < 2 or cells[0].upper() not in wanted:
            continue
        found[cells[0].upper()] = " — ".join(part for part in cells[1:3] if part)
    return found


def grep(repo_path: str, query: str, ref: str = "") -> tuple[list[str], bool] | None:
    """`file:line:text` for every line carrying `query`, at `ref` (the working
    tree when empty), and whether there were more than `MAX_HITS`.

    `git grep -F`, so `query` is a plain string, never a pattern; `-e` keeps
    a query that starts with a dash a query. `None` for every way git could
    not answer.
    """
    if ref.startswith("-"):
        return None
    root = Path(repo_path).expanduser().resolve()
    args = ["git", "-C", str(root), "grep", "-n", "-I", "-F", "--no-color", "-e", query]
    if ref:
        args.append(ref)
    args.append("--")
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=TOOL_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git grep %r: %s", query, exc)
        return None
    if done.returncode == 1:
        return [], False
    if done.returncode != 0:
        return None
    prefix = f"{ref}:" if ref else ""
    found = [
        line[len(prefix) :] if prefix and line.startswith(prefix) else line
        for line in done.stdout.splitlines()
    ]
    return [line[:HIT_CHARS] for line in found[:MAX_HITS]], len(found) > MAX_HITS


def unknown_repo(repo: str, placement: Placement) -> str:
    """The refusal every `repo`-taking tool gives a name that is not the room's."""
    names = sorted(p.name for p in placement.projects)
    return (
        f"{repo!r} is not one of this room's projects: {names or 'none are recorded'}."
    )


def _read_code(evidence: Evidence, placement: Placement, running: RunningVersion):
    @tool
    async def read_code(repo: str, file: str, line: int) -> str:
        """Read the code around a line, at the version that is running.

        Give the file exactly as a stack frame named it, or as `search_code`
        listed it. A compiled frame — `dist/src/x.js:80` — is mapped back to
        the TypeScript before it is read, because line 80 of the built file
        is not the line anybody wrote. The answer names the version it read.

        Args:
            repo: which of the room's projects, by name.
            file: the path from the stack frame or the search.
            line: the line number.
        """
        if spent := evidence.spent():
            return spent
        project = placement.repo(repo)
        if project is None:
            return unknown_repo(repo, placement)
        if not project.repo_path:
            return "No repository is recorded for this project, so no code can be read."
        evidence.reads += 1
        tag, why = await running.of(repo)
        # With a tag, the checkout need not have the file: the tag may (a file
        # renamed or deleted since the release).
        found = repo_file(
            file, project.repo_path, container_roots=CONTAINER_ROOTS, exists=not tag
        )
        if found is None:
            return (
                f"{file} is not in this clone — it is somebody else's code, "
                f"or outside the repository."
            )
        shown, at, mapped_note = found, int(line), ""
        mapped = (
            original(found, int(line), project.repo_path) if found.is_file() else None
        )
        if mapped is not None:
            shown, at = mapped
            if tag:
                # The map is the checkout's build, not the tag's: if the two
                # differ, the line can be off. Said, not guessed around.
                mapped_note = "; line mapped with the checkout's source map"
        text = (
            await asyncio.to_thread(at_ref, project.repo_path, shown, tag)
            if tag
            else None
        )
        if tag and text is None:
            why = f"the clone has no {shown.name} at {tag} — fetch its tags"
        if text is not None:
            where = f"at {tag}, the version running in {placement.env}{mapped_note}"
        else:
            try:
                text = shown.read_text(errors="replace")
            except OSError:
                return f"{file} is neither at the running version nor in the checkout ({why})."
            where = f"the clone's current checkout — running version unresolved: {why}"
            evidence.not_checked.append(
                f"{repo}/{shown.name} was read at the checkout, not the running version ({why})"
            )
        return f"--- {shown.name}:{at} ({where})\n" + evidence.show(
            numbered(text, at).splitlines()
        )

    return read_code


def _search_code(evidence: Evidence, placement: Placement, running: RunningVersion):
    @tool
    async def search_code(repo: str, query: str) -> str:
        """Find where a route, a symbol or a term is written in a repository.

        A plain substring search (not a regular expression) at the version
        that is running. Each match is `file:line: text`; follow one with
        `read_code` to see the lines around it.

        Args:
            repo: which of the room's projects, by name.
            query: the exact text to look for — a route path, a function name,
                an error code.
        """
        if spent := evidence.spent():
            return spent
        project = placement.repo(repo)
        if project is None:
            return unknown_repo(repo, placement)
        if not project.repo_path:
            return "No repository is recorded for this project, so nothing can be searched."
        if not query.strip():
            return "Give a string to search for."
        evidence.reads += 1
        tag, why = await running.of(repo)
        hits = (
            await asyncio.to_thread(grep, project.repo_path, query, tag)
            if tag
            else None
        )
        where = f"at {tag}"
        if hits is None:
            if tag:
                why = f"the clone does not have {tag} — fetch its tags"
            hits = await asyncio.to_thread(grep, project.repo_path, query)
            where = f"the clone's current checkout — running version unresolved: {why}"
            evidence.not_checked.append(
                f"the search for {query!r} in {repo} ran on the checkout, not the running version ({why})"
            )
        if hits is None:
            return f"{repo} could not be searched."
        found, more = hits
        if not found:
            return f"Nothing in {repo} ({where}) carries {query!r}."
        if more:
            evidence.not_checked.append(
                f"the search for {query!r} in {repo} was capped at {MAX_HITS} matches"
            )
        said = f"--- {len(found)}{'+' if more else ''} matches in {repo} ({where})\n"
        return said + evidence.show(found)

    return search_code


def _what_code_means(evidence: Evidence, placement: Placement):
    @tool
    def what_code_means(repo: str, code: str) -> str:
        """What one of this project's error codes stands for.

        Read out of the repository's own table, not guessed. Measured: every
        one of 2,104 HTTP 500s in thirty days carried `ERR19`, the generic
        code — so a number on its own says almost nothing, and what the
        table calls it can say a great deal.

        Args:
            repo: which of the room's projects, by name.
            code: the error code exactly as the log spelled it, e.g. `ERR19`.
        """
        if spent := evidence.spent():
            return spent
        project = placement.repo(repo)
        if project is None:
            return unknown_repo(repo, placement)
        evidence.reads += 1
        if not project.error_codes_doc:
            return "This project records no error-code table."
        found = meanings(project.error_codes_doc, [code], project.repo_path)
        return found.get(code) or f"{code} is not in this project's table."

    return what_code_means


def code_tools(run: RunContext) -> list:
    """`backend.code`'s factory: the three tools, over this run's placement,
    numbering and running version."""
    running = RunningVersion.for_run(run)
    return [
        _read_code(run.evidence, run.domain, running),
        _search_code(run.evidence, run.domain, running),
        _what_code_means(run.evidence, run.domain),
    ]


CODE = ToolsetSpec(
    name="backend.code",
    description=(
        "Read, search and explain the room's repositories at the version "
        "that is running: read_code, search_code, what_code_means."
    ),
    factory=code_tools,
    mcp={RELEASE_SERVER: ReleaseSource.TOOLS},
    domain_type=Placement,
)
