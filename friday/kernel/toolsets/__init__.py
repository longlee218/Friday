"""The core's toolsets, each a `ToolsetSpec` any action's contract may grant.

Build-the-spine ticket 08 (board `domains-plug-in` ticket 03, amendment
§3–4); was `friday/kernel/tools/`. One module per toolset:

| Module | Toolset | Tools |
| --- | --- | --- |
| `memory/` | `core.memory` | `memory_search`, `memory_propose` |
| `memory/` | `core.memory_write` | `memory_add`, `memory_update`, `memory_delete` |
| `skills.py` | `core.skills` | `fetch_skill`, `search_skills`, `describe_skill`, `read_skill_file` |
| `bash/` | `core.shell` | `bash` — full rights, no allowlist (ticket 24; was `shell.py`'s `run_command`, read-only) |
| `todo/` | `core.todo` | `todo_write` — the agent's own checklist, replaced whole per call (ticket 24) |
| `workspace.py` | `core.workspace` | the pydantic-ai-harness file tools over `/tmp/friday/<task_id>/` |
| `repos/` | `core.repos` | `read`, `grep`, `glob` over a domain's `RepoRoom`, each taking an optional `ref` the model finds itself (ticket 23; split into a package, one file per tool plus shared helpers, once the module passed 850 lines) |

**The list of tools is asserted, not described.** `tests/test_tools.py`
asserts every name, builds the factories as well as scanning the modules, and
forbids declaring a tool anywhere else in the core — so "what can the agents
do?" has one answer that cannot drift. A door that is not in the room is
deleted rather than described (board `every-answer-has-a-shape`, D14).

The core toolsets need what only the process holds — the store, the skill
library, the declared shell hosts — so `core_toolsets` closes over them
rather than widening `RunContext`, which every plugin factory sees. Before
the store is open (a boot that only checks declarations) they are `None`, and
a factory built without what it needs raises `NotWired` naming it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from friday.sdk.plugin import Plugin
from friday.sdk.toolset import RunContext, ToolsetSpec

__all__ = ["NotWired", "core_plugin", "core_toolsets"]


class NotWired(RuntimeError):
    """A core toolset was built without the process dependency it needs."""


def _needs(value: Any, what: str, toolset: str) -> Any:
    if value is None:
        raise NotWired(f"{toolset} was built without {what}: pass it to core_toolsets")
    return value


def core_toolsets(
    *, db: Any = None, skills: Any = None, hosts: Sequence[str] = ()
) -> tuple[ToolsetSpec, ...]:
    """The seven core toolsets, their factories bound to `db` (the store),
    `skills` (a `SkillLibrary`) and `hosts` (`config.shell_hosts`). `core.repos`
    and `core.todo` need none of these — `core.todo` reads `run.todos` alone —
    so their specs are reused as-is rather than closed over here."""

    def memory(run: RunContext) -> list[Any]:
        return _memory("core.memory")

    def memory_write(run: RunContext) -> list[Any]:
        return _memory("core.memory_write")

    def _memory(toolset: str) -> list[Any]:
        from friday.kernel.toolsets.memory import (
            MEMORY_READS,
            MEMORY_WRITES,
            memory_tools,
        )

        names = MEMORY_READS if toolset == "core.memory" else MEMORY_WRITES
        built = memory_tools(_needs(db, "a store", toolset))
        # A `Tool` carries its name; a bare function is named by `__name__`.
        return [
            t
            for t in built
            if (getattr(t, "name", None) or getattr(t, "__name__", None)) in names
        ]

    def skill(run: RunContext) -> list[Any]:
        from friday.kernel.toolsets.skills import skill_toolset

        return skill_toolset(_needs(skills, "a skill library", "core.skills"))

    def shell(run: RunContext) -> list[Any]:
        from friday.kernel.audit import AuditLog
        from friday.kernel.toolsets.bash import shell_tools

        return shell_tools(
            run, hosts=hosts, audit=AuditLog(_needs(db, "a store", "core.shell"))
        )

    def workspace(run: RunContext) -> list[Any]:
        from friday.kernel.toolsets.workspace import workspace_tools

        return workspace_tools(run.task_id)

    from friday.kernel.toolsets.repos import REPOS
    from friday.kernel.toolsets.todo import TODO

    return (
        ToolsetSpec(
            "core.memory",
            "What this channel already knows: search short memories scoped to "
            "the room, and propose one for the operator to review.",
            memory,
        ),
        ToolsetSpec(
            "core.memory_write",
            "Write this channel's memory directly: add a memory read back as "
            "fact, correct or delete one — no operator review.",
            memory_write,
        ),
        ToolsetSpec(
            "core.skills",
            "The operator's written procedures: search, describe and read a "
            "skill and its supporting files.",
            skill,
        ),
        ToolsetSpec(
            "core.shell",
            "Run any command, full rights, on this machine or a declared SSH "
            "host — no allowlist.",
            shell,
        ),
        ToolsetSpec(
            "core.workspace",
            "A scratch folder for this task: read, write, edit and list files, "
            "e.g. long command output saved by core.shell.",
            workspace,
        ),
        REPOS,
        TODO,
    )


def core_plugin(**deps: Any) -> Plugin:
    """The core as a registrant: the owner the `core.*` toolsets and the
    core's own evals (`core.triage`, `core.planner`) are recorded under, so the boot refusals
    treat them like any plugin's."""

    def register(api: Any) -> None:
        from friday.kernel.evals.planner import PLANNER_EVAL
        from friday.kernel.evals.triage import TRIAGE_EVAL

        for spec in core_toolsets(**deps):
            api.toolset(spec)
        api.eval(TRIAGE_EVAL)
        api.eval(PLANNER_EVAL)

    return Plugin(id="core", register=register)
