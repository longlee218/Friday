"""`core.repos`'s `read`: a file from one of the room's repositories,
`cat -n`-style.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines. Declared explicitly, not from a docstring (ticket 23's own
rule): `description=` is rendered by `_read_description` from the constants
below, each parameter's by a `Field` beside it, and semantic refusals that
need no I/O (an unknown repo, a path outside the clone, a secret file, a
`ref` shaped like a flag) run in `_validate_read`'s `args_validator=`, before
this body runs at all. A `ref` git cannot resolve is refused in the body,
since checking it is a git call.

Over the 200-line soft target: the ref/source-map/checkout-fallback logic is
one sequence a reader needs to follow in order, so it stays one function
rather than being split across files for a line count.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from pydantic import Field

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets.repos.git import at_ref, blob_kind, ref_resolves
from friday.kernel.toolsets.repos.paths import (
    REF_LINE,
    is_secret,
    numbered,
    refuse_ref_flag,
    repo_file,
    repo_of,
    unknown_repo,
)
from friday.kernel.toolsets.repos.sourcemap import original
from friday.sdk.sources import RepoRoom
from friday.sdk.toolset import tool

#: Without `limit`, a file past this many bytes or this many estimated tokens
#: (`len(text) // 4`) is refused rather than truncated — Claude Code's own
#: `Read`: an error is cheaper than a truncation nobody notices.
MAX_READ_BYTES = 256 * 1024
MAX_READ_TOKENS = 25_000

#: Lines shown when neither `offset` nor `limit` is given, for a file small
#: enough to pass the refusal above.
DEFAULT_READ_LINES = 2000


def _read_description() -> str:
    return (
        "Read a file from one of the room's repositories. `cat -n`-style, "
        "with an id beside each line you can cite. Without `limit`, a file "
        f"over {MAX_READ_BYTES // 1024} KB or an estimated {MAX_READ_TOKENS} "
        "tokens is refused rather than truncated: give `offset`/`limit` to "
        f"read part of it. Up to {DEFAULT_READ_LINES} lines are shown when "
        "you give neither. A directory, a binary file, or a secret file or "
        f"directory (credentials, `.env`, `.ssh`, …) is refused. {REF_LINE}"
    )


def _validate_read(domain: RepoRoom):
    def check_args(ctx, **kwargs) -> None:
        repo, path = kwargs["repo"], kwargs["path"]
        refuse_ref_flag(kwargs.get("ref"))
        found = repo_of(domain, repo)
        if found is None:
            raise ModelRetry(unknown_repo(domain, repo))
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
        if is_secret(confined, found.repo_path):
            raise ModelRetry(
                f"{path!r} names a credential file or directory and may not be read."
            )

    return check_args


def _build_read(domain: RepoRoom, evidence, seen_ranges: dict, name_the_room):
    """`read`, bound to this run's domain, evidence and unchanged-range cache
    (`seen_ranges`, one run's worth, owned by `friday.kernel.toolsets.repos`
    and shared across calls within the run)."""

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
        """`core.repos`'s `read` — see `_read_description` for what the model is told.

        `repo`/`path`/`ref`'s shape are already known good: `args_validator`
        (`_validate_read`) ran the same lookup, confinement and flag check
        before this body runs at all. A `ref` git cannot resolve is refused
        here, since checking it is a git call.
        """
        evidence.reads += 1
        found = repo_of(domain, repo)
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
            if not await asyncio.to_thread(ref_resolves, found.repo_path, ref):
                raise ModelRetry(
                    f"{ref!r} could not be resolved in {repo}'s clone — check the "
                    f"spelling, or fetch it."
                )
            kind = await asyncio.to_thread(blob_kind, found.repo_path, relative, ref)
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

    return read
