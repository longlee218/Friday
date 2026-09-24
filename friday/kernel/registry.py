"""Where a plugin's contributions land: the registry that is the `PluginAPI`.

The kernel owns the invariants and names no plugin (spec § Shape and trust). The
registry is the first piece of that kernel: a plugin's `register(api)` is handed
this, and every `api.task_type(...)` / `api.memory_kind(...)` collects a spec
here — no task-type or pack-kind literal in this module, so a plugin removed is a
plugin gone, not a string left behind in the core.

Ticket 10 builds the container and its one boot check (a duplicate id refuses the
boot, §4.3); the wiring that reads these specs into the router and the memory
write path is tickets 11 and 12. Registering only collects — no I/O, no
connections (§4.3) — so this stays a plain in-memory map the composition root
fills before anything runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from friday.sdk.memory import MemoryKindSpec
from friday.sdk.plugin import Plugin, TaskTypeSpec

__all__ = ["DuplicateRegistration", "Registry"]


class DuplicateRegistration(ValueError):
    """Two contributions claim one id. Raised at registration rather than
    written, because the alternative is one silently shadowing the other — a
    capability that is present in config and absent in fact."""


class Registry:
    """The `PluginAPI` the kernel hands each plugin, and the collection it fills.

    Passive by design: it holds what was registered and hands it back, and the
    only rule it enforces now is that no id is claimed twice. The plugin never
    sees this class — it sees the `PluginAPI` Protocol it satisfies, which is how
    a plugin registers without importing the kernel.
    """

    def __init__(self, config: Any = None) -> None:
        #: The plugin's own validated config block, or `None`. Set per plugin by
        #: `apply`; the register methods may read it off `self`.
        self.config = config
        #: The composition root's boot capabilities (its `prepare_node`,
        #: `make_harness`, servers, identities), or `None`. Part of the
        #: `PluginAPI` a graph builder reaches through; this passive registry
        #: carries none, so it is `None` here.
        self.caps: Any = None
        self._task_types: dict[str, TaskTypeSpec] = {}
        self._memory_kinds: dict[str, MemoryKindSpec] = {}
        self._readers: dict[str, frozenset[str]] = {}

    def apply(self, plugin: Plugin, config: Any = None) -> None:
        """Run one plugin's `register`, with its config in hand. The one call the
        composition root makes per plugin; everything the plugin contributes goes
        through the `PluginAPI` methods below."""
        self.config = config
        plugin.register(self)

    # ── PluginAPI ────────────────────────────────────────────────────────────

    def task_type(self, spec: TaskTypeSpec) -> None:
        if spec.name in self._task_types:
            raise DuplicateRegistration(
                f"task type {spec.name!r} is already registered — two plugins "
                "cannot claim one id"
            )
        self._task_types[spec.name] = spec

    def memory_kind(self, spec: MemoryKindSpec) -> None:
        if spec.name in self._memory_kinds:
            raise DuplicateRegistration(
                f"memory kind {spec.name!r} is already registered — two plugins "
                "cannot claim one id"
            )
        self._memory_kinds[spec.name] = spec

    def reader(self, name: str, needs: frozenset[str]) -> None:
        """Declare that a reader reads these kinds (DESIGN-v2 §9.2). Merges, so a
        plugin adds its kinds to a reader the core already routes to."""
        self._readers[name] = self._readers.get(name, frozenset()) | frozenset(needs)

    # ── what the kernel reads back ───────────────────────────────────────────

    def task_types(self) -> Mapping[str, TaskTypeSpec]:
        return dict(self._task_types)

    def memory_kinds(self) -> Mapping[str, MemoryKindSpec]:
        return dict(self._memory_kinds)

    def readers(self) -> Mapping[str, frozenset[str]]:
        return dict(self._readers)
