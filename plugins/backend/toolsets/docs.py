"""`backend.docs`: reading a project's own docs, only under its `docs_paths`.

`read_docs(repo, path, line)` reads a document the `backend.project` row
declared as docs (board `domains-plug-in` ticket 06 §2), at the version that
is running like `read_code` (ticket 04 §6), falling back to the checkout and
saying so. With no `path` it lists what can be read.

A path is model text: it is resolved and refused unless it lands inside one of
the project's `docs_paths`, which themselves must lie inside the clone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from friday.sdk.evidence import Evidence
from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.placement import Placement, Project
from plugins.backend.toolsets.code import at_ref, numbered, unknown_repo
from plugins.backend.toolsets.release import (
    RELEASE_SERVER,
    ReleaseSource,
    RunningVersion,
)

__all__ = ["DOCS", "docs_tools"]

#: Lines one `read_docs` shows from `line` on; call again further down for more.
DOC_LINES = 80

#: The most files a listing names.
MAX_LISTED = 100


def _roots(project: Project) -> list[Path]:
    """The project's docs directories (or files), each confined to the clone.

    Strictly inside it: a `docs_paths` entry of `""` or `"."` would make the
    whole clone "docs" — `.env`, `.git/config` — so it is dropped, not read.
    """
    clone = Path(project.repo_path).expanduser().resolve()
    roots = []
    for declared in project.docs_paths:
        where = (clone / declared).resolve()
        if where != clone and where.is_relative_to(clone):
            roots.append(where)
    return roots


def _doc(project: Project, path: str) -> Path | None:
    """`path` (relative to the clone) if it lies under a docs root. Not
    required to exist in the checkout: the running tag may hold it."""
    clone = Path(project.repo_path).expanduser().resolve()
    where = (clone / path).resolve()
    if any(where == root or where.is_relative_to(root) for root in _roots(project)):
        return where
    return None


def _listing(project: Project) -> list[str]:
    clone = Path(project.repo_path).expanduser().resolve()
    found: list[str] = []
    for root in _roots(project):
        files = (
            [root]
            if root.is_file()
            else sorted(p for p in root.rglob("*") if p.is_file())
        )
        found += [f.relative_to(clone).as_posix() for f in files]
    return found[:MAX_LISTED]


def _read_docs(evidence: Evidence, placement: Placement, running: RunningVersion):
    @tool
    async def read_docs(repo: str, path: str = "", line: int = 1) -> str:
        """Read one of a project's docs, at the version that is running.

        Only files under the project's declared docs folders can be read.
        Leave `path` empty to list them. Long documents come in windows:
        call again with a larger `line` to read further.

        Args:
            repo: which of the room's projects, by name.
            path: the document, as the listing names it. Empty to list.
            line: the first line to show.
        """
        if spent := evidence.spent():
            return spent
        project = placement.repo(repo)
        if project is None:
            return unknown_repo(repo, placement)
        if not project.repo_path or not project.docs_paths:
            return f"{repo} records no docs folders, so there are no docs to read."
        if not path:
            evidence.reads += 1
            listed = _listing(project)
            return (
                "\n".join(listed) if listed else f"{repo}'s docs folders hold no files."
            )
        found = _doc(project, path)
        if found is None:
            return f"{path} is not a document under {repo}'s docs folders."
        evidence.reads += 1
        tag, why = await running.of(repo)
        text = (
            await asyncio.to_thread(at_ref, project.repo_path, found, tag)
            if tag
            else None
        )
        if tag and text is None:
            why = f"the clone has no {path} at {tag} — fetch its tags"
        if text is not None:
            where = f"at {tag}"
        else:
            try:
                text = found.read_text(errors="replace")
            except OSError:
                return f"{path} is neither at the running version nor in the checkout ({why})."
            where = f"the clone's current checkout — running version unresolved: {why}"
            evidence.not_checked.append(
                f"{repo}/{path} was read at the checkout, not the running version ({why})"
            )
        start = max(1, int(line))
        total = len(text.splitlines())
        shown = numbered(text, start, before=0, after=DOC_LINES - 1)
        more = (
            f" of {total}; more from line {start + DOC_LINES}"
            if start + DOC_LINES <= total
            else ""
        )
        return f"--- {path} ({where}){more}\n" + evidence.show(shown.splitlines())

    return read_docs


def docs_tools(run: RunContext) -> list:
    """`backend.docs`'s factory."""
    return [_read_docs(run.evidence, run.domain, RunningVersion.for_run(run))]


DOCS = ToolsetSpec(
    name="backend.docs",
    description=(
        "Read the docs a project declares (its docs_paths), at the version "
        "that is running: read_docs."
    ),
    factory=docs_tools,
    mcp={RELEASE_SERVER: ReleaseSource.TOOLS},
    domain_type=Placement,
)
