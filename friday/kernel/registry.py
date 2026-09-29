"""Where a plugin's contributions land: the registry, and the `PluginAPI` view
each plugin registers through.

The kernel owns the invariants and names no plugin (spec § Shape and trust). A
plugin's `register(api)` is handed a `PluginRegistration` — one per plugin, so
it carries that plugin's own `config` and later `caps`, and every name it
registers is recorded as the plugin's (`owner_of`). Everything lands in one
`Registry`; no task-type, action or pack-kind literal lives in this module, so
a plugin removed is a plugin gone, not a string left behind in the core.

Registering only collects — no I/O, no connections (§4.3). The one rule
enforced here is that no name is claimed twice within a kind; the rest of the
boot refusals run over the whole registry once every plugin has registered
(`friday.kernel.boot_refusals`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from friday.sdk.action import Action
from friday.sdk.agent import AgentSpec
from friday.sdk.eval import EvalSpec
from friday.sdk.memory import MemoryKindSpec
from friday.sdk.plugin import Plugin, TaskTypeSpec
from friday.sdk.toolset import ToolsetSpec

__all__ = ["DuplicateRegistration", "PluginRegistration", "Registry"]


class DuplicateRegistration(ValueError):
    """Two contributions claim one id. Raised at registration rather than
    written, because the alternative is one silently shadowing the other — a
    capability that is present in config and absent in fact."""


def _claim(table: dict[str, Any], what: str, name: str, value: Any) -> None:
    if name in table:
        raise DuplicateRegistration(
            f"{what} {name!r} is already registered — two plugins cannot claim one id"
        )
    table[name] = value


class Registry:
    """The collection every plugin fills. Passive by design: it holds what was
    registered and hands it back. A plugin never sees this class — it sees the
    `PluginRegistration` that `apply` hands its `register`.
    """

    def __init__(self) -> None:
        self._task_types: dict[str, TaskTypeSpec] = {}
        self._memory_kinds: dict[str, MemoryKindSpec] = {}
        self._readers: dict[str, frozenset[str]] = {}
        self._actions: dict[str, Action] = {}
        self._agents: dict[str, AgentSpec] = {}
        self._toolsets: dict[str, ToolsetSpec] = {}
        self._evals: dict[str, EvalSpec] = {}
        #: Which plugin registered each name (the spine's surface and memory
        #: kinds), for the checks that need a name's domain.
        self._owners: dict[str, Plugin] = {}
        #: Which plugin declared each reader. Several may declare one (merge).
        self._reader_owners: dict[str, set[str]] = {}

    def apply(self, plugin: Plugin, config: Any = None) -> PluginRegistration:
        """Run one plugin's `register` once, with its config in hand. Returns
        the view it registered through, so the composition root can attach
        `caps` before a deferred graph builder reads it."""
        api = PluginRegistration(self, plugin, config)
        plugin.register(api)
        return api

    # ── registering (called through a PluginRegistration) ───────────────────

    def task_type(self, spec: TaskTypeSpec) -> None:
        _claim(self._task_types, "task type", spec.name, spec)

    def memory_kind(self, spec: MemoryKindSpec, owner: Plugin | None = None) -> None:
        _claim(self._memory_kinds, "memory kind", spec.name, spec)
        if owner is not None:
            self._owners[spec.name] = owner

    def reader(
        self, name: str, needs: frozenset[str], owner: Plugin | None = None
    ) -> None:
        """Declare that a reader reads these kinds (DESIGN-v2 §9.2). Merges, so a
        plugin adds its kinds to a reader the core already routes to."""
        self._readers[name] = self._readers.get(name, frozenset()) | frozenset(needs)
        if owner is not None:
            self._reader_owners.setdefault(name, set()).add(owner.id)

    def action(self, action: Action, owner: Plugin) -> None:
        _claim(self._actions, "action", action.name, action)
        self._owners[action.name] = owner

    def agent(self, spec: AgentSpec, owner: Plugin) -> None:
        _claim(self._agents, "agent", spec.name, spec)
        self._owners[spec.name] = owner

    def toolset(self, spec: ToolsetSpec, owner: Plugin) -> None:
        _claim(self._toolsets, "toolset", spec.name, spec)
        self._owners[spec.name] = owner

    def eval(self, spec: EvalSpec, owner: Plugin) -> None:
        _claim(self._evals, "eval", spec.name, spec)
        self._owners[spec.name] = owner

    # ── what the kernel reads back ───────────────────────────────────────────

    def task_types(self) -> Mapping[str, TaskTypeSpec]:
        return dict(self._task_types)

    def memory_kinds(self) -> Mapping[str, MemoryKindSpec]:
        return dict(self._memory_kinds)

    def readers(self) -> Mapping[str, frozenset[str]]:
        return dict(self._readers)

    def reader_owners(self, name: str) -> frozenset[str]:
        return frozenset(self._reader_owners.get(name, ()))

    def actions(self) -> Mapping[str, Action]:
        return dict(self._actions)

    def agents(self) -> Mapping[str, AgentSpec]:
        return dict(self._agents)

    def toolsets(self) -> Mapping[str, ToolsetSpec]:
        return dict(self._toolsets)

    def evals(self) -> Mapping[str, EvalSpec]:
        return dict(self._evals)

    def owner_of(self, name: str) -> Plugin | None:
        return self._owners.get(name)


class PluginRegistration:
    """The `PluginAPI` one plugin's `register` is handed: every call lands in
    the shared `Registry`, recorded as this plugin's.

    `config` is the plugin's own validated config block (temporary, ticket 09).
    `caps` is `None` while `register` runs; the composition root attaches it
    before building the task-type graphs, whose builders read `api.caps`
    lazily (ticket 16 deletes both).
    """

    def __init__(self, registry: Registry, plugin: Plugin, config: Any = None) -> None:
        self._registry = registry
        self.plugin = plugin
        self.config = config
        self.caps: Any = None

    def task_type(self, spec: TaskTypeSpec) -> None:
        self._registry.task_type(spec)

    def action(self, action: Action) -> None:
        self._registry.action(action, self.plugin)

    def agent(self, spec: AgentSpec) -> None:
        self._registry.agent(spec, self.plugin)

    def toolset(self, spec: ToolsetSpec) -> None:
        self._registry.toolset(spec, self.plugin)

    def eval(self, spec: EvalSpec) -> None:
        self._registry.eval(spec, self.plugin)

    def memory_kind(self, spec: MemoryKindSpec) -> None:
        self._registry.memory_kind(spec, self.plugin)

    def reader(self, name: str, needs: frozenset[str]) -> None:
        self._registry.reader(name, needs, self.plugin)
