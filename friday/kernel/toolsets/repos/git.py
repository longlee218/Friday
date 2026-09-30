"""`core.repos`'s shared git subprocess helpers.

Split out of the single `repos.py` (build-the-spine ticket 23) once it
passed 850 lines. Every function here starts a `git` subprocess — the
allowed-to-do-that list (`tests/test_sources_are_the_only_door.py`) covers
this file plus `grep.py`/`glob.py`, which run their own `git grep`/`git
ls-tree` inline rather than through a shared helper (each has its own
pathspec/flag assembly, so there is nothing to share).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS

__all__ = ["at_ref"]

log = logging.getLogger(__name__)


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


def blob_kind(repo_path: str, relative: Path, ref: str) -> str:
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


def ref_resolves(repo_path: str, ref: str) -> bool:
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
