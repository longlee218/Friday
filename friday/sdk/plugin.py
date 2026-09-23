"""The plugin contract: what a plugin is, and the API it registers against.

Contracts only — dataclasses and a Protocol, no I/O, no third-party imports. A
plugin is a `Plugin` value plus a `register(api)` function (no base class); the
kernel hands `register` a `PluginAPI`, and the plugin calls what it needs on it.
This is the seam the whole restructure turns on: a task type or a memory kind
becomes a call to `api.task_type(...)` / `api.memory_kind(...)`, so adding one is
adding a plugin, not editing the core.

Ticket 10 defines these types and a kernel that holds them; tickets 11–14 fill
the registry and move the first real task type onto this API. The `PluginAPI`
surface is **only what is buildable today** — `task_type` and `memory_kind`. The
DESIGN-v2 §4.2 methods `toolset`/`skills`/`source`/`check` arrive each with its
own ticket and the types it needs, rather than as empty stubs here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from friday.sdk.memory import MemoryKindSpec
from friday.sdk.workflow import DAG, Deps

__all__ = ["Plugin", "PluginAPI", "TaskTypeSpec"]


@dataclass(frozen=True)
class TaskTypeSpec:
    """One task type, as its plugin declares it.

    **Trimmed to the fields the router uses today** (spec § Registration and
    scope): `name`, `params`, `extractor`, `graph`, `deps`, `needs`. The
    triage-at-scale fields of DESIGN-v2 §6.1 (`accepts`, `examples`,
    `contrasts`, `eval_cases`) are deferred with triage-across-plugins.

    `params` is the parameter dataclass whose docstring is the type's
    description to triage. `extractor` builds node 0 (the parameter extractor)
    for a run; `graph` builds the rest, or `None` for the one-node simple graph
    every type but `api_issue` uses. `deps` is the per-run dependency type
    (ticket 13). `needs` names the sources, kinds and agents the type uses, so
    the kernel can refuse a boot that cannot satisfy them.
    """

    name: str
    params: type
    #: Builds node 0's parameter extractor for a run. Its return is the
    #: `Extractor` in `friday.extraction`, typed `Any` here because the sdk
    #: holds the contract, not the runner — importing the extraction stack into
    #: a contracts-only module would invert the dependency the kernel owns.
    extractor: Callable[[Deps], Any] | None = None
    graph: Callable[[Deps], DAG] | None = None
    deps: type | None = None
    needs: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class PluginAPI(Protocol):
    """What a plugin's `register(api)` is handed.

    A Protocol, so the kernel's registry satisfies it by shape without the
    plugin importing the kernel — the dependency rule (a plugin imports `sdk`
    only) in the type system. `config` is the plugin's own validated config
    block (or `None` if it takes none); the register methods are how it
    contributes a capability.
    """

    config: Any

    def task_type(self, spec: TaskTypeSpec) -> None:
        """Register one task type. A duplicate name refuses the boot."""
        ...

    def memory_kind(self, spec: MemoryKindSpec) -> None:
        """Register one memory kind. A duplicate name refuses the boot."""
        ...


@dataclass(frozen=True)
class Plugin:
    """A plugin as the kernel sees it: an id it namespaces everything under, the
    one `register` entrypoint, the plugin ids it `requires` (load order and
    presence, never an import), and the dataclass its config block is validated
    against (`None` if it takes none). A package exposes exactly one `PLUGIN`.
    """

    id: str
    register: Callable[[PluginAPI], None]
    requires: tuple[str, ...] = ()
    config: type | None = None
