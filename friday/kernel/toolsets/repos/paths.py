"""`core.repos`'s repo/path/ref argument helpers, shared by `read.py`,
`grep.py` and `glob.py`.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines: every tool has to resolve `repo` against the room's
`RepoRoom`, confine a `path` to that repo's clone, refuse a secret file or
directory, and refuse a `ref` shaped like a flag — this is where those live
so no tool file has to duplicate them or import a sibling.
"""

from __future__ import annotations

import logging
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from friday.kernel.harness.harness import ModelRetry
from friday.kernel.toolsets.shell import SECRET_DIRS, SECRET_FILES
from friday.sdk.sources import Repo, RepoRoom

__all__ = ["numbered", "repo_file"]

log = logging.getLogger(__name__)

#: The line every tool's description gives for `ref`: find the tag first (a
#: devops tool, e.g. `release_status` or a pod's own image tag), then pass
#: it here.
REF_LINE = (
    "Give `ref` — a tag, branch or sha, e.g. from `release_status` or the "
    "pod's own image tag — to read the version that is actually running. "
    "Without one this reads the checkout, which may not match."
)


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


def numbered(text: str, line: int, *, before: int, after: int) -> str:
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


def repo_of(domain: RepoRoom, name: str) -> Repo | None:
    return next((r for r in domain.repos() if r.name == name), None)


def unknown_repo(domain: RepoRoom, repo: str) -> str:
    names = sorted(r.name for r in domain.repos())
    return (
        f"{repo!r} is not one of this room's projects: {names or 'none are recorded'}."
    )


def is_secret(path: Path, repo_path: str) -> bool:
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


def refuse_ref_flag(ref: str | None) -> None:
    """`args_validator`'s half of `ref` checking — no I/O, so it runs before
    the tool body. A `ref` git cannot resolve needs a git call and is refused
    in the body instead (`git.ref_resolves`)."""
    if ref and ref.startswith("-"):
        raise ModelRetry(
            f"{ref!r} looks like a flag, not a git ref — a leading '-' is refused."
        )


def validate_room(domain: RepoRoom, *, verb: str):
    """The unknown-repo / no-clone refusal shared by `grep` and `glob`."""

    def check_args(ctx, **kwargs) -> None:
        repo = kwargs["repo"]
        refuse_ref_flag(kwargs.get("ref"))
        found = repo_of(domain, repo)
        if found is None:
            raise ModelRetry(unknown_repo(domain, repo))
        if not found.repo_path:
            raise ModelRetry(
                f"No repository is recorded for {repo!r}, so nothing can be {verb}."
            )

    return check_args
