"""`core.repos`'s `grep`: a regular-expression search over one of the room's
repositories, through `git grep`.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines. Declared explicitly, not from a docstring: `description=`
is rendered by `_grep_description` from the constants below, each
parameter's by a `Field` beside it, and the unknown-repo/no-clone/ref-flag
refusals run in `_validate_grep`'s `args_validator=`, before this body runs
at all. A `ref` git cannot resolve is refused in the body, since checking it
is a git call. `git grep` itself runs here, not in `git.py`: the pathspec
and flag assembly below is grep's own, nothing else shares it.

Over the 200-line soft target: `grep` takes nine parameters and the flag
assembly they drive (`-i`/`-A`/`-B`/`-C`, `-l`/`-c`, the pathspec excludes,
the ref/checkout fallback, three output-mode branches) is one sequence a
reader needs to follow in order, so it stays one function.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Annotated, Literal

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets.repos.git import ref_resolves
from friday.kernel.toolsets.repos.paths import REF_LINE, resolve_repo, validate_room
from friday.kernel.toolsets.shell import SECRET_DIRS, SECRET_FILES
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS, RepoRoom
from friday.sdk.toolset import tool

log = logging.getLogger(__name__)

#: A `grep` line longer than this is cut (Claude Code's `--max-columns 500`;
#: git has no such flag).
MAX_GREP_LINE_CHARS = 500

#: The most characters one `grep content` answer carries.
MAX_GREP_CHARS = 20_000

#: The most results one `grep` returns unless `head_limit` says otherwise.
#: `0` is the escape hatch, meaning unlimited.
DEFAULT_HEAD_LIMIT = 250


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
        f"never searched. {REF_LINE}"
    )


def _validate_grep(domain: RepoRoom):
    room = validate_room(domain, verb="searched")

    def check_args(ctx, **kwargs) -> None:
        room(ctx, **kwargs)
        if not kwargs["pattern"].strip():
            raise ModelRetry("Give a string to search for.")

    return check_args


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


def _build_grep(domain: RepoRoom, evidence, name_the_room):
    """`grep`, bound to this run's domain and evidence."""

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
        """`core.repos`'s `grep` — see `_grep_description`.

        `repo`/`ref`'s shape are already known good: `args_validator`
        (`_validate_grep`) ran the same lookup and flag check before this
        body runs at all. A `ref` git cannot resolve is refused here.
        """
        evidence.reads += 1
        resolved = resolve_repo(domain, repo, verb="searched")
        repo_path, root = resolved.repo.repo_path, resolved.root

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

        async def run_at(git_ref: str) -> subprocess.CompletedProcess[str] | None:
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
            if not await asyncio.to_thread(ref_resolves, repo_path, ref):
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

    return grep
