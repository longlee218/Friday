"""The core's toolsets, each a `ToolsetSpec` any action's contract may grant.

Build-the-spine ticket 08 (board `domains-plug-in` ticket 03, amendment
§3–4); was `friday/kernel/tools/`. One module per toolset:

| Module | Toolset | Tools |
| --- | --- | --- |
| `memory.py` | `core.memory` | `memory_search`, `memory_add`, `memory_propose`, `memory_update`, `memory_delete` |
| `skills.py` | `core.skills` | `fetch_skill`, `search_skills`, `describe_skill`, `read_skill_file` |
| `shell.py` | `core.shell` | `run_command` (read-command allowlist, local or SSH) |
| `workspace.py` | `core.workspace` | the pydantic-ai-harness file tools over `/tmp/friday/<task_id>/` |

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
    """The four core toolsets, their factories bound to `db` (the store),
    `skills` (a `SkillLibrary`) and `hosts` (`config.shell_hosts`)."""

    def memory(run: RunContext) -> list:
        from friday.kernel.toolsets.memory import memory_tools

        return memory_tools(_needs(db, "a store", "core.memory"))

    def skill(run: RunContext) -> list:
        from friday.kernel.toolsets.skills import skill_toolset

        return skill_toolset(_needs(skills, "a skill library", "core.skills"))

    def shell(run: RunContext) -> list:
        from friday.kernel.audit import AuditLog
        from friday.kernel.toolsets.shell import shell_tools

        return shell_tools(run, hosts=hosts, audit=AuditLog(_needs(db, "a store", "core.shell")))

    def workspace(run: RunContext) -> list:
        from friday.kernel.toolsets.workspace import workspace_tools

        return workspace_tools(run.task_id)

    return (
        ToolsetSpec(
            "core.memory",
            "What this channel already knows: search, add, propose, correct "
            "and delete short memories scoped to the room.",
            memory,
        ),
        ToolsetSpec(
            "core.skills",
            "The operator's written procedures: search, describe and read a "
            "skill and its supporting files.",
            skill,
        ),
        ToolsetSpec(
            "core.shell",
            "Run one read-only command (kubectl get/logs/describe/top, cat, "
            "grep, tail, …) on this machine or a declared SSH host.",
            shell,
        ),
        ToolsetSpec(
            "core.workspace",
            "A scratch folder for this task: read, write, edit and list files, "
            "e.g. long command output saved by core.shell.",
            workspace,
        ),
    )


def core_plugin(**deps: Any) -> Plugin:
    """The core as a registrant: the owner the `core.*` toolsets are
    recorded under, so the boot refusals treat them like any plugin's."""

    def register(api: Any) -> None:
        for spec in core_toolsets(**deps):
            api.toolset(spec)

    return Plugin(id="core", register=register)
