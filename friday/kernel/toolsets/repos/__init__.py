"""`core.repos`: read, grep and glob over the room's repositories, at a ref
the model names itself (build-the-spine ticket 23).

Generic in the kernel; a domain hands in what a repository is through the sdk
contract (`friday.sdk.sources.Repo`/`RepoRoom`) alone — the kernel names no
running-version mechanism. Replaces `backend.code`/`backend.docs`
(`read_code`, `search_code`, `what_code_means`, `read_docs`): one shape per
capability, modelled on Claude Code's own `Read`/`Grep`/`Glob`, instead of
four domain-specific tools a model had to learn from scratch.

**D7, amended 2026-09-30 (operator): the model finds the tag, not the
code.** An earlier version had a plugin toolset (`backend.release`) resolve
a repo's running tag once per run and bind it onto an `Evidence` hook every
read/search/listing consulted automatically. The operator rejected that: the
model is expected to call `release_status` or read the pod's own image tag
through the devops tools (ticket 28) and hand the tag to `read`/`grep`/`glob`
itself, as `ref`. So a ref is each tool's **own optional parameter** — with
one, `git show`/`git grep`/`git ls-tree` read exactly that ref; without one,
they read the checkout and say so in `not_checked`.

**Split into a package, one file per tool plus shared helpers**, once the
single `repos.py` module passed 850 lines:

| File | Holds |
| --- | --- |
| `read.py` | `read`'s constants, description, validator and body |
| `grep.py` | `grep`'s, plus its own `git grep` pathspec/flag assembly |
| `glob.py` | `glob`'s, plus its own `git ls-tree` call (no stdlib `glob` import) |
| `git.py` | shared git subprocess helpers (`at_ref`, `blob_kind`, `ref_resolves`) |
| `paths.py` | repo lookup, path confinement, secret checks, the `ref`-flag check and the `grep`/`glob`-shared validator |
| `sourcemap.py` | `.js.map` translation (`original`), used by `read` only |

This file builds the three into `repos_tools(run)` and exports `REPOS`.

**Descriptions are declared, never read off a docstring** (ticket 23's own
rule, generalized by ticket 26): each tool's `description=` is rendered by a
function over the constants it enforces, each parameter's by a `Field`
beside it, and the room's repository names reach the model through
`prepare=` (`_name_the_room` below). Semantic refusals that need no I/O run
in `args_validator=`, before the tool body runs at all; a `ref` git cannot
resolve is refused in the body, since checking it is a git call.
"""

from __future__ import annotations

from dataclasses import replace as _replace

from friday.kernel.toolsets.repos.glob import _build_glob
from friday.kernel.toolsets.repos.grep import _build_grep
from friday.kernel.toolsets.repos.read import _build_read
from friday.sdk.sources import RepoRoom
from friday.sdk.toolset import RunContext, ToolsetSpec

__all__ = ["REPOS", "repos_tools"]


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


def repos_tools(run: RunContext) -> list:
    """`core.repos`'s factory: `read`, `grep`, `glob` over this run's domain
    (a `RepoRoom`), each taking its own `ref` from the model."""
    domain: RepoRoom = run.domain
    evidence = run.evidence
    #: Unchanged-range dedup, one run's worth: the same file/ref/window read
    #: twice gets a stub instead of its text again. `read`'s own state,
    #: built here since it must survive across calls within one run.
    seen_ranges: dict[tuple, tuple[str, str]] = {}
    name_the_room = _name_the_room(domain)

    return [
        _build_read(domain, evidence, seen_ranges, name_the_room),
        _build_grep(domain, evidence, name_the_room),
        _build_glob(domain, evidence, name_the_room),
    ]


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
