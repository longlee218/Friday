"""Ticket 15's first guard, brought forward by ticket 00.

*"An `ast` test: nothing outside the Source package imports the Loki,
kubectl, git or database clients."*

The property is worth more than the packages it currently protects. "Every
tool a graph is given is a read" (`docs/DESIGN.md`, Running it) is a claim
about the whole system, and it is only checkable if there is one place to
look. A node that shells out for itself moves that claim from "read this
package" to "read every node, and read the next one somebody writes".

Read with `ast` rather than by grepping, so a call written
`subprocess . run` or reached through an alias is still seen, and a mention
inside a docstring is not.
"""

from __future__ import annotations

import ast
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Both trees a shell-out could hide in: the core, and the plugins. Ticket 14
#: moved the concrete backend sources out to `plugins/backend/sources/`, so the
#: guard has to read there too or it would pass by looking at the wrong tree.
PACKAGES = (ROOT / "friday", ROOT / "plugins")

#: Where the outside world may be reached (build-the-spine ticket 09: the
#: sources folded into each plugin's `toolsets/`, one file per data source
#: holding the client and its tools). Globs over repo-relative posix paths.
ALLOWED = (
    "plugins/*/toolsets/*.py",
    # `core.shell` (ticket 08): the one core toolset that runs a command.
    "friday/kernel/toolsets/shell.py",
    # `core.repos` (ticket 23): `read`/`grep`/`glob` start `git` themselves,
    # generic over any domain's repositories.
    "friday/kernel/toolsets/repos.py",
    # `Reads` is the narrowing every MCP call goes through — the one place
    # allowed to hold a server and pass a call on, and the place that refuses
    # a tool no toolset declared. An sdk port since ticket 14.
    "friday/sdk/sources.py",
)


def allowed(name: str) -> bool:
    return any(fnmatch(name, pattern) for pattern in ALLOWED)


#: Starting a process. The whole of how this system reaches `ssh`, `kubectl`
#: and `git` today, and the thing a node must not do for itself.
SPAWNS = {
    "create_subprocess_exec",
    "create_subprocess_shell",
    "run",  # subprocess.run
    "Popen",
    "check_output",
    "system",
}

#: Asking a tool server for something. `build` in `agent/mcp.py` constructs
#: servers; this is the call that reads through one.
READS_THROUGH_MCP = {"call_tool"}


def modules() -> list[tuple[str, ast.Module]]:
    found = []
    for package in PACKAGES:
        if not package.exists():
            continue
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in relative:
                continue
            found.append((relative, ast.parse(path.read_text())))
    return found


def _calls(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def test_only_the_source_package_starts_a_process():
    """`ssh` and `kubectl` are how dev is reached, and they are subprocesses.
    A second module that spawns one is a second door."""
    offenders = {
        name
        for name, tree in modules()
        if not allowed(name)
        and _calls(tree)
        & {
            "create_subprocess_exec",
            "create_subprocess_shell",
            "Popen",
            "system",
            "check_output",
        }
    }

    assert offenders == set(), (
        f"these modules start a process without being a source: {sorted(offenders)}"
    )


def test_only_the_source_package_reads_through_a_tool_server():
    """Loki is behind the devops MCP. Anything that calls a tool on a server
    is reading the outside world, whatever it calls itself."""
    offenders = {
        name
        for name, tree in modules()
        if not allowed(name) and _calls(tree) & READS_THROUGH_MCP
    }

    assert offenders == set(), (
        f"these modules read through a tool server without being a source: "
        f"{sorted(offenders)}"
    )


def test_a_source_may_not_reach_for_an_agent():
    """A source that can call a model is a source that can be talked into
    reading something else. It is also the import direction that would make
    the capability depend on the thing calling it."""
    reached = {}
    for name, tree in modules():
        if not fnmatch(name, "plugins/*/toolsets/*.py"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                ("friday.kernel.harness", "friday.kernel.dag", "friday.kernel.pool")
            ):
                reached.setdefault(name, []).append(node.module)

    assert reached == {}, f"a source imported what calls it: {reached}"
