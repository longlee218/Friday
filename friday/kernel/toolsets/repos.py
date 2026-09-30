"""`core.repos`: read, grep and glob over the room's repositories, at a ref
the model names itself (build-the-spine ticket 23).

Generic in the kernel; a domain hands in what a repository is through the sdk
contract (`friday.sdk.sources.Repo`/`RepoRoom`) alone — the kernel names no
running-version mechanism. Replaces `backend.code`/`backend.docs`
(`read_code`, `search_code`, `what_code_means`, `read_docs`): one shape per
capability, modelled on Claude Code's own `Read`/`Grep`/`Glob`, instead of
four domain-specific tools a model had to learn from scratch.

**D7, amended 2026-09-30 (operator): the model finds the tag, not the
code.** An earlier version of this ticket had a plugin toolset
(`backend.release`) resolve a repo's running tag once per run and bind it
onto `Evidence.resolve_ref`, which every read/search/listing consulted
automatically. The operator rejected that: the model is expected to call
`release_status` or read the pod's own image tag through the devops tools
(ticket 28) and hand the tag to `read`/`grep`/`glob` itself, as `ref`. So a
ref is `read`/`grep`/`glob`'s **own optional parameter** — with one, `git
show`/`git grep`/`git ls-tree` read exactly that ref; without one, they read
the checkout and say so in `not_checked`, the same honesty the old
per-run-cached version gave for a repo the release lookup could not place.

**Descriptions are declared, never read off a docstring** (ticket 23's own
rule, generalized by ticket 26): each tool's `description=` is rendered by a
function over the constants it enforces, each parameter's by a `Field`
beside it, and the room's repository names reach the model through
`prepare=`. Semantic refusals that need no I/O (an unknown repo, a path
outside the clone, a secret file or directory, a `ref` shaped like a flag)
run in `args_validator=`, before the tool body runs at all; a `ref` git
cannot resolve is refused in the body, since checking it is a git call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import replace as _replace
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets.shell import SECRET_DIRS, SECRET_FILES
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS, Repo, RepoRoom
from friday.sdk.toolset import RunContext, ToolsetSpec, tool

__all__ = [
    "REPOS",
    "at_ref",
    "numbered",
    "original",
    "repo_file",
    "repos_tools",
]

log = logging.getLogger(__name__)

#: Without `limit`, a file past this many bytes or this many estimated tokens
#: (`len(text) // 4`) is refused rather than truncated — Claude Code's own
#: `Read`: an error is cheaper than a truncation nobody notices.
MAX_READ_BYTES = 256 * 1024
MAX_READ_TOKENS = 25_000

#: Lines shown when neither `offset` nor `limit` is given, for a file small
#: enough to pass the refusal above.
DEFAULT_READ_LINES = 2000

#: A `grep` line longer than this is cut (Claude Code's `--max-columns 500`;
#: git has no such flag).
MAX_GREP_LINE_CHARS = 500

#: The most characters one `grep content` answer carries.
MAX_GREP_CHARS = 20_000

#: The most results one `grep` returns unless `head_limit` says otherwise.
#: `0` is the escape hatch, meaning unlimited.
DEFAULT_HEAD_LIMIT = 250

#: The most paths one `glob` returns.
MAX_GLOB_RESULTS = 100


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

    `container_roots` is the image's source-root policy — the plugin's, off
    `Repo.container_roots`. `exists=False` returns the confined path even
    when the checkout has no such file — one the running tag may still hold
    (`git show` reads it there).
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
        # Traversal, from a log line or a model. The one thing this must not do.
        log.warning("path %r resolves outside %s — not read", frame, repo_path)
        return None
    return candidate if candidate.is_file() or not exists else None


def at_ref(repo_path: str, path: Path, ref: str) -> str | None:
    """The file's text as it is at a git ref, or `None`.

    **`git show`, never a checkout and never a worktree.** The clone belongs
    to the operator and is open in their editor; moving its HEAD to answer a
    question is the one thing this must not do.

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
        log.warning("git show %s: %s", ref, exc)
        return None
    return done.stdout if done.returncode == 0 else None


def _blob_kind(repo_path: str, relative: Path, ref: str) -> str:
    """`"blob"`, `"tree"`, or `""` for every way of not knowing, at `ref` —
    cheap, so `read` can refuse a directory without pulling its text over."""
    root = Path(repo_path).expanduser().resolve()
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-t", f"{ref}:{relative.as_posix()}"],
            capture_output=True,
            text=True,
            timeout=TOOL_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git cat-file -t %s:%s: %s", ref, relative, exc)
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def numbered(
    text: str, line: int, *, before: int = 0, after: int = DEFAULT_READ_LINES - 1
) -> str:
    """The lines from `line - before` through `line + after`, numbered —
    `cat -n`-style when `before=0` (a sequential read from `line`), a
    centred excerpt otherwise (a mapped stack frame)."""
    lines = text.splitlines()
    start = max(0, line - 1 - before)
    end = min(len(lines), line + after)
    width = len(str(end)) or 1
    return "\n".join(
        f"{i + 1:>{width}} {'>' if i + 1 == line else ' '} {lines[i]}"
        for i in range(start, end)
    )


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


def _repo_of(domain: RepoRoom, name: str) -> Repo | None:
    return next((r for r in domain.repos() if r.name == name), None)


def _unknown_repo(domain: RepoRoom, repo: str) -> str:
    names = sorted(r.name for r in domain.repos())
    return (
        f"{repo!r} is not one of this room's projects: {names or 'none are recorded'}."
    )


def _is_secret(path: Path, repo_path: str) -> bool:
    """Whether `path` (inside `repo_path`) names a credential file or sits
    under a credential directory — never read, searched or listed."""
    root = Path(repo_path).expanduser().resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return False
    posix = PurePosixPath(relative.as_posix())
    if any(part in SECRET_DIRS for part in posix.parts):
        return True
    return any(fnmatch(posix.name.lower(), glob) for glob in SECRET_FILES)


def _ref_resolves(repo_path: str, ref: str) -> bool:
    """Whether `ref` (a tag, a branch or a sha) names a real commit in this
    clone — checked once per call so a typo'd or unfetched `ref` is refused
    rather than silently read as the checkout."""
    root = Path(repo_path).expanduser().resolve()
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--verify",
                "--quiet",
                f"{ref}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            timeout=TOOL_CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git rev-parse %s: %s", ref, exc)
        return False
    return done.returncode == 0


def _refuse_ref_flag(ref: str | None) -> None:
    """`args_validator`'s half of `ref` checking — no I/O, so it runs before
    the tool body. A `ref` git cannot resolve needs a git call and is refused
    in the body instead."""
    if ref and ref.startswith("-"):
        raise ModelRetry(
            f"{ref!r} looks like a flag, not a git ref — a leading '-' is refused."
        )


def _name_the_room(domain: RepoRoom):
    """A `prepare=`: the room's repository names, appended to the tool's
    static description per run — kept out of the constants-only
    `description=` so that half stays testable on its own."""
    names = ", ".join(sorted(r.name for r in domain.repos())) or "none recorded"

    async def prepare(ctx, tool_def):
        return _replace(
            tool_def,
            description=f"{tool_def.description}\n\nThis room's repositories: {names}.",
        )

    return prepare


def _validate_read(domain: RepoRoom):
    def check_args(ctx, **kwargs) -> None:
        repo, path = kwargs["repo"], kwargs["path"]
        _refuse_ref_flag(kwargs.get("ref"))
        found = _repo_of(domain, repo)
        if found is None:
            raise ModelRetry(_unknown_repo(domain, repo))
        if not found.repo_path:
            raise ModelRetry(
                f"No repository is recorded for {repo!r}, so nothing can be read."
            )
        confined = repo_file(
            path, found.repo_path, container_roots=found.container_roots, exists=False
        )
        if confined is None:
            raise ModelRetry(
                f"{path!r} is not in {repo}'s clone — it is somebody else's code, "
                f"or outside the repository."
            )
        if _is_secret(confined, found.repo_path):
            raise ModelRetry(
                f"{path!r} names a credential file or directory and may not be read."
            )

    return check_args


def _validate_room(domain: RepoRoom, *, verb: str):
    """The unknown-repo / no-clone refusal shared by `grep` and `glob`."""

    def check_args(ctx, **kwargs) -> None:
        repo = kwargs["repo"]
        _refuse_ref_flag(kwargs.get("ref"))
        found = _repo_of(domain, repo)
        if found is None:
            raise ModelRetry(_unknown_repo(domain, repo))
        if not found.repo_path:
            raise ModelRetry(
                f"No repository is recorded for {repo!r}, so nothing can be {verb}."
            )

    return check_args


def _validate_grep(domain: RepoRoom):
    room = _validate_room(domain, verb="searched")

    def check_args(ctx, **kwargs) -> None:
        room(ctx, **kwargs)
        if not kwargs["pattern"].strip():
            raise ModelRetry("Give a string to search for.")

    return check_args


#: The line every description gives for `ref`: find the tag first (a devops
#: tool, e.g. `release_status` or a pod's own image tag), then pass it here.
_REF_LINE = (
    "Give `ref` — a tag, branch or sha, e.g. from `release_status` or the "
    "pod's own image tag — to read the version that is actually running. "
    "Without one this reads the checkout, which may not match."
)


def _read_description() -> str:
    return (
        "Read a file from one of the room's repositories. `cat -n`-style, "
        "with an id beside each line you can cite. Without `limit`, a file "
        f"over {MAX_READ_BYTES // 1024} KB or an estimated {MAX_READ_TOKENS} "
        "tokens is refused rather than truncated: give `offset`/`limit` to "
        f"read part of it. Up to {DEFAULT_READ_LINES} lines are shown when "
        "you give neither. A directory, a binary file, or a secret file or "
        f"directory (credentials, `.env`, `.ssh`, …) is refused. {_REF_LINE}"
    )


def _grep_description() -> str:
    return (
        "Search the room's repositories with a regular expression. "
        "`output_mode` is `files_with_matches` (default: a sorted file "
        "list), `content` (matched lines, grounded with an id you can cite) "
        "or `count`. `context_before`/`context_after` add lines of context; "
        f"`context` sets both. At most {DEFAULT_HEAD_LIMIT} results are "
        "shown unless `head_limit` says otherwise (`0` is unlimited); a "
        f"line is cut at {MAX_GREP_LINE_CHARS} characters and the whole "
        f"answer at {MAX_GREP_CHARS}. Secret files and directories are "
        f"never searched. {_REF_LINE}"
    )


def _glob_description() -> str:
    return (
        "List the room's repository paths matching a glob (`**` included). "
        f"At most {MAX_GLOB_RESULTS} results are shown; narrow the pattern "
        "for more. Secret files and directories are never listed. "
        f"{_REF_LINE}"
    )


def _windowed(
    entries: list[str], *, offset: int, head_limit: int
) -> tuple[list[str], bool]:
    """`entries[offset:]`, capped at `head_limit` (`0` is unlimited), and
    whether that cut anything off."""
    rest = entries[max(0, int(offset)) :]
    if head_limit <= 0:
        return rest, False
    window = rest[: int(head_limit)]
    return window, len(window) < len(rest)


def repos_tools(run: RunContext) -> list:
    """`core.repos`'s factory: `read`, `grep`, `glob` over this run's domain
    (a `RepoRoom`), each taking its own `ref` from the model."""
    domain: RepoRoom = run.domain
    evidence = run.evidence
    #: Unchanged-range dedup, one run's worth: the same file/ref/window read
    #: twice gets a stub instead of its text again.
    seen_ranges: dict[tuple, tuple[str, str]] = {}
    name_the_room = _name_the_room(domain)

    @tool(
        description=_read_description(),
        prepare=name_the_room,
        args_validator=_validate_read(domain),
    )
    async def read(
        repo: Annotated[
            str, Field(description="One of this room's repositories, by name.")
        ],
        path: Annotated[
            str,
            Field(
                description="The file, exactly as a stack frame named it, or repo-relative."
            ),
        ],
        ref: Annotated[
            str | None,
            Field(
                description="A tag, branch or sha to read at. Omit to read the checkout."
            ),
        ] = None,
        offset: Annotated[
            int, Field(description="The first line to show, 1-indexed.")
        ] = 1,
        limit: Annotated[
            int | None,
            Field(description="How many lines to show. Required for a large file."),
        ] = None,
    ) -> str:
        """The read half of `core.repos` — see `_read_description` for what the model is told.

        `repo`/`path`/`ref`'s shape are already known good: `args_validator`
        (`_validate_read`) ran the same lookup, confinement and flag check
        before this body runs at all. A `ref` git cannot resolve is refused
        here, since checking it is a git call.
        """
        evidence.reads += 1
        found = _repo_of(domain, repo)
        frame = repo_file(
            path, found.repo_path, container_roots=found.container_roots, exists=False
        )

        root = Path(found.repo_path).expanduser().resolve()
        shown, at, translated = frame, max(1, int(offset)), False
        if frame.is_file():
            mapped = await asyncio.to_thread(original, frame, at, found.repo_path)
            if mapped is not None:
                shown, at, translated = *mapped, True

        relative = shown.relative_to(root)

        text: str | None = None
        if ref:
            if not await asyncio.to_thread(_ref_resolves, found.repo_path, ref):
                raise ModelRetry(
                    f"{ref!r} could not be resolved in {repo}'s clone — check the "
                    f"spelling, or fetch it."
                )
            kind = await asyncio.to_thread(_blob_kind, found.repo_path, relative, ref)
            if kind == "tree":
                raise ModelRetry(f"{path!r} is a directory; use grep or glob instead.")
            if kind == "blob":
                text = await asyncio.to_thread(at_ref, found.repo_path, shown, ref)

        served_from_ref = text is not None
        if served_from_ref:
            mapped_note = (
                "; line mapped with the checkout's source map" if translated else ""
            )
            where = f"at {ref}{mapped_note}"
        else:
            if shown.is_dir():
                raise ModelRetry(f"{path!r} is a directory; use grep or glob instead.")
            try:
                text = shown.read_text(errors="replace")
            except OSError:
                if ref:
                    raise ModelRetry(
                        f"{path!r} is neither at {ref!r} nor in the checkout."
                    ) from None
                raise ModelRetry(
                    f"{path!r} is not in the checkout, and no ref was given to read it at."
                ) from None
            if ref:
                where = f"the clone's current checkout — {shown.name} is not at {ref!r}"
                evidence.not_checked.append(
                    f"{repo}/{shown.name} was read at the checkout: not at {ref!r}"
                )
            else:
                where = "the clone's current checkout — no ref was given"
                evidence.not_checked.append(
                    f"{repo}/{shown.name} was read at the checkout, not a pinned ref "
                    f"— pass `ref` (from release_status or the pod's image tag) to "
                    f"read the version that is running"
                )

        if "\0" in text:
            raise ModelRetry(f"{path!r} looks like a binary file; read is for text.")
        if limit is None:
            size, tokens = len(text.encode("utf-8", "replace")), len(text) // 4
            if size > MAX_READ_BYTES or tokens > MAX_READ_TOKENS:
                raise ModelRetry(
                    f"{path!r} is {size} bytes (~{tokens} tokens) — give offset/limit "
                    f"to read part of it rather than the whole file."
                )
        window = DEFAULT_READ_LINES if limit is None else max(1, int(limit))

        mtime = (
            shown.stat().st_mtime_ns if not served_from_ref and shown.exists() else 0
        )
        key = (
            repo,
            relative.as_posix(),
            ref if served_from_ref else f"checkout:{mtime}",
            at,
            window,
        )
        header = f"--- {shown.name}:{at} ({where})\n"
        cached = seen_ranges.get(key)
        if cached is not None:
            return f"{header}(unchanged since last read — see {cached[0]}-{cached[1]})"

        rendered = evidence.show(
            numbered(text, at, before=0, after=window - 1).splitlines()
        )
        ids = [
            ln.split(" | ", 1)[0] for ln in rendered.splitlines() if ln.startswith("L")
        ]
        if ids:
            seen_ranges[key] = (ids[0], ids[-1])
        return header + rendered

    @tool(
        description=_grep_description(),
        prepare=name_the_room,
        args_validator=_validate_grep(domain),
    )
    async def grep(
        repo: Annotated[
            str, Field(description="One of this room's repositories, by name.")
        ],
        pattern: Annotated[
            str, Field(description="A regular expression to search for.")
        ],
        glob: Annotated[
            str | None, Field(description="Only search paths matching this glob.")
        ] = None,
        ref: Annotated[
            str | None,
            Field(
                description="A tag, branch or sha to search at. Omit to search the checkout."
            ),
        ] = None,
        output_mode: Annotated[
            Literal["files_with_matches", "content", "count"],
            Field(
                description="A sorted file list, matched lines, or a per-file count."
            ),
        ] = "files_with_matches",
        case_insensitive: Annotated[
            bool, Field(description="Match without regard to case.")
        ] = False,
        context_before: Annotated[
            int, Field(description="Lines of context before each match.")
        ] = 0,
        context_after: Annotated[
            int, Field(description="Lines of context after each match.")
        ] = 0,
        context: Annotated[
            int | None,
            Field(
                description="Lines of context on both sides; overrides context_before/context_after."
            ),
        ] = None,
        head_limit: Annotated[
            int, Field(description="The most results to return; 0 is unlimited.")
        ] = DEFAULT_HEAD_LIMIT,
        offset: Annotated[
            int, Field(description="How many results to skip before head_limit.")
        ] = 0,
    ) -> str:
        """The search half of `core.repos` — see `_grep_description`.

        `repo`/`ref`'s shape are already known good: `args_validator`
        (`_validate_grep`) ran the same lookup and flag check before this
        body runs at all. A `ref` git cannot resolve is refused here.
        """
        evidence.reads += 1
        found = _repo_of(domain, repo)
        root = Path(found.repo_path).expanduser().resolve()

        prefix = ["git", "-C", str(root), "grep", "--extended-regexp", "-n"]
        if case_insensitive:
            prefix.append("-i")
        if context is not None:
            prefix += ["-C", str(int(context))]
        else:
            if context_before:
                prefix += ["-B", str(int(context_before))]
            if context_after:
                prefix += ["-A", str(int(context_after))]
        if output_mode == "files_with_matches":
            prefix.append("-l")
        elif output_mode == "count":
            prefix.append("-c")
        prefix += ["-e", pattern] if pattern.startswith("-") else [pattern]

        suffix = ["--"]
        if glob:
            suffix.append(f":(glob){glob}")
        suffix.extend(f":(exclude,glob){name}" for name in SECRET_FILES)
        suffix.extend(f":(exclude,glob)**/{name}/**" for name in sorted(SECRET_DIRS))

        async def run_at(git_ref: str) -> subprocess.CompletedProcess | None:
            args = [*prefix, *([git_ref] if git_ref else []), *suffix]
            try:
                return await asyncio.to_thread(
                    subprocess.run,
                    args,
                    capture_output=True,
                    text=True,
                    timeout=TOOL_CALL_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("git grep %r: %s", pattern, exc)
                return None

        if ref:
            if not await asyncio.to_thread(_ref_resolves, found.repo_path, ref):
                raise ModelRetry(
                    f"{ref!r} could not be resolved in {repo}'s clone — check the "
                    f"spelling, or fetch it."
                )
            done = await run_at(ref)
            where = f"at {ref}"
        else:
            done = await run_at("")
            where = "the clone's current checkout"
            evidence.not_checked.append(
                f"the search for {pattern!r} in {repo} ran on the checkout, not a "
                f"pinned ref — pass `ref` (from release_status or the pod's image "
                f"tag) to search the version that is running"
            )

        if done is None or done.returncode not in (0, 1):
            detail = done.stderr.strip()[:300] if done is not None else ""
            return f"{repo} could not be searched" + (f": {detail}" if detail else ".")

        lines = [ln[:MAX_GREP_LINE_CHARS] for ln in done.stdout.splitlines()]

        if output_mode == "files_with_matches":
            files = sorted(set(lines))
            shown, truncated = _windowed(files, offset=offset, head_limit=head_limit)
            if not shown:
                return f"Nothing in {repo} ({where}) carries {pattern!r}."
            note = (
                f"\n({len(files) - len(shown) - int(offset)} more — narrow the pattern, "
                f"add a glob, or raise head_limit)"
                if truncated
                else ""
            )
            return (
                f"Found {len(files)} file{'s' if len(files) != 1 else ''} ({where})\n"
                + "\n".join(shown)
                + note
            )

        if output_mode == "count":
            shown, truncated = _windowed(lines, offset=offset, head_limit=head_limit)
            if not shown:
                return f"Nothing in {repo} ({where}) carries {pattern!r}."
            note = "\n(more — raise head_limit for the rest)" if truncated else ""
            return "\n".join(shown) + note

        if not lines:
            return f"Nothing in {repo} ({where}) carries {pattern!r}."
        shown, truncated = _windowed(lines, offset=offset, head_limit=head_limit)
        body = "\n".join(shown)
        if len(body) > MAX_GREP_CHARS:
            body, truncated = body[:MAX_GREP_CHARS], True
        said = f"--- matches in {repo} ({where})\n" + evidence.show(body.splitlines())
        if truncated:
            said += (
                "\n(more matched — narrow the pattern, add a glob, or raise head_limit)"
            )
        return said

    @tool(
        description=_glob_description(),
        prepare=name_the_room,
        args_validator=_validate_room(domain, verb="listed"),
    )
    async def glob(
        repo: Annotated[
            str, Field(description="One of this room's repositories, by name.")
        ],
        pattern: Annotated[
            str, Field(description="A glob over repo-relative paths, e.g. src/**/*.ts.")
        ],
        ref: Annotated[
            str | None,
            Field(
                description="A tag, branch or sha to list at. Omit to list the checkout."
            ),
        ] = None,
    ) -> str:
        """The listing half of `core.repos` — see `_glob_description`.

        `repo`/`ref`'s shape are already known good: `args_validator`
        (`_validate_room`) ran the same lookup and flag check before this
        body runs at all. A `ref` git cannot resolve is refused here.
        """
        evidence.reads += 1
        found = _repo_of(domain, repo)
        root = Path(found.repo_path).expanduser().resolve()

        paths: list[str] = []
        if ref:
            if not await asyncio.to_thread(_ref_resolves, found.repo_path, ref):
                raise ModelRetry(
                    f"{ref!r} could not be resolved in {repo}'s clone — check the "
                    f"spelling, or fetch it."
                )
            try:
                done = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref],
                    capture_output=True,
                    text=True,
                    timeout=TOOL_CALL_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("git ls-tree %s: %s", ref, exc)
                done = None
            if done is None or done.returncode != 0:
                # `ref` is already known to resolve (`_ref_resolves` above), so
                # this is a real git-level failure — not indistinguishable from
                # a true zero-match answer, the way an empty `paths` would be.
                detail = done.stderr.strip()[:300] if done is not None else ""
                return f"{repo} could not be listed at {ref!r}" + (
                    f": {detail}" if detail else "."
                )
            paths = done.stdout.splitlines()
        else:
            evidence.not_checked.append(
                f"the listing of {repo} ran on the checkout, not a pinned ref — pass "
                f"`ref` (from release_status or the pod's image tag) to list the "
                f"version that is running"
            )
            paths = [
                p.relative_to(root).as_posix()
                for p in root.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(root).parts
            ]

        candidates = [
            p
            for p in paths
            if not any(part in SECRET_DIRS for part in PurePosixPath(p).parts)
            and not any(
                fnmatch(PurePosixPath(p).name.lower(), secret)
                for secret in SECRET_FILES
            )
        ]
        matched = sorted(p for p in candidates if PurePosixPath(p).full_match(pattern))
        if not matched:
            return f"Nothing in {repo} matches {pattern!r}."
        shown = matched[:MAX_GLOB_RESULTS]
        note = (
            f"\n({len(matched) - len(shown)} more matched — use a more specific pattern)"
            if len(matched) > MAX_GLOB_RESULTS
            else ""
        )
        return "\n".join(shown) + note

    return [read, grep, glob]


REPOS = ToolsetSpec(
    name="core.repos",
    description=(
        "Read, search and list the room's repositories: read, grep, glob. "
        "Each takes an optional ref (tag, branch or sha) to read the "
        "version that is running rather than the checkout."
    ),
    factory=repos_tools,
    domain_type=RepoRoom,
)
