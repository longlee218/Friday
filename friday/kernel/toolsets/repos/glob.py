"""`core.repos`'s `glob`: repository paths matching a glob pattern, at a ref
or the checkout.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines. Declared explicitly, not from a docstring: `description=`
is rendered by `_glob_description` from the constant below, each
parameter's by a `Field` beside it, and the unknown-repo/no-clone/ref-flag
refusals run in `_validate_glob`'s `args_validator=`, before this body runs
at all. A `ref` git cannot resolve is refused in the body. `git ls-tree`
itself runs here, not in `git.py`: nothing else needs it.

Named `glob` without importing the stdlib `glob` module — matching is done
with `PurePosixPath.full_match`, Python 3.13's own `**`-aware glob matcher.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets.repos.git import ref_resolves
from friday.kernel.toolsets.repos.paths import REF_LINE, repo_of, validate_room
from friday.kernel.toolsets.shell import SECRET_DIRS, SECRET_FILES
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS, RepoRoom
from friday.sdk.toolset import tool

log = logging.getLogger(__name__)

#: The most paths one `glob` returns.
MAX_GLOB_RESULTS = 100


def _glob_description() -> str:
    return (
        "List the room's repository paths matching a glob (`**` included). "
        f"At most {MAX_GLOB_RESULTS} results are shown; narrow the pattern "
        "for more. Secret files and directories are never listed. "
        f"{REF_LINE}"
    )


def _validate_glob(domain: RepoRoom):
    return validate_room(domain, verb="listed")


def _build_glob(domain: RepoRoom, evidence, name_the_room):
    """`glob`, bound to this run's domain and evidence."""

    @tool(
        description=_glob_description(),
        prepare=name_the_room,
        args_validator=_validate_glob(domain),
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
        """`core.repos`'s `glob` — see `_glob_description`.

        `repo`/`ref`'s shape are already known good: `args_validator`
        (`_validate_glob`) ran the same lookup and flag check before this
        body runs at all. A `ref` git cannot resolve is refused here.
        """
        evidence.reads += 1
        found = repo_of(domain, repo)
        root = Path(found.repo_path).expanduser().resolve()

        paths: list[str] = []
        if ref:
            if not await asyncio.to_thread(ref_resolves, found.repo_path, ref):
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
                # `ref` is already known to resolve (`ref_resolves` above), so
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

    return glob
