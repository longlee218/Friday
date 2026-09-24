"""The plugin contract: what a plugin is, and the API it registers against.

Contracts only — dataclasses and a Protocol, no I/O, no third-party imports. A
plugin is a `Plugin` value plus a `register(api)` function (no base class); the
kernel hands `register` a `PluginAPI`, and the plugin calls what it needs on it.
This is the seam the whole restructure turns on: a task type or a memory kind
becomes a call to `api.task_type(...)` / `api.memory_kind(...)`, so adding one is
adding a plugin, not editing the core.

Ticket 10 defined these types and a kernel that holds them; ticket 14 moved the
first real task type (`devops.api_issue`) and its pack kinds onto this API. The
`PluginAPI` surface is what a plugin needs today — `task_type`, `memory_kind`,
`reader`, plus the boot `caps` a graph builder reaches for. The DESIGN-v2 §4.2
methods `toolset`/`skills`/`source`/`check` arrive each with its own ticket and
the types it needs, rather than as empty stubs here.
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
    description to triage. `graph` builds the type's whole `DAG`; a plugin's
    graph builds node 0 through `api.caps.prepare_node` (so it never imports the
    kernel's node-0 machinery) and closes over `api.caps` for its model harness
    and tool servers. `deps` is the per-run **deps factory** (ticket 13). `needs`
    names the sources, kinds and agents the type uses, so the kernel can refuse a
    boot that cannot satisfy them.
    """

    name: str
    params: type
    #: Builds node 0's parameter extractor for a run. Its return is the
    #: `Extractor` in `friday.extraction`, typed `Any` here because the sdk
    #: holds the contract, not the runner — importing the extraction stack into
    #: a contracts-only module would invert the dependency the kernel owns.
    extractor: Callable[[Deps], Any] | None = None
    #: Builds the whole `DAG` for this type. The `Deps` argument is a legacy of
    #: the boot-built graph and is ignored; a plugin's builder closes over
    #: `api.caps` instead (the composition root's `prepare_node`/`make_harness`/
    #: servers), which is what lets it build node 0 and a model node without
    #: importing anything of the kernel's.
    graph: Callable[[Deps], DAG] | None = None
    #: The per-run deps factory (ticket 13, was a `type` in the ticket-10
    #: skeleton). Given the kernel-built base `Deps` — which already carries the
    #: task, the store and the tool servers resolved from the run's serializable
    #: scope key — it returns the type's own typed `Deps`, so the type's live
    #: handles are built per run and a boot check can confirm every field can be
    #: satisfied (DESIGN-v2 §5.2). `None` for a type whose base `Deps` is enough.
    deps: Callable[[Deps], Deps] | None = None
    needs: frozenset[str] = field(default_factory=frozenset)
    #: The longest one task of this type may hold a pool slot, checked at boot
    #: against the sum of the graph's node ceilings (`check_graph_clocks`), or
    #: `None` for a type with no bound of its own. On the spec now (ticket 14) so
    #: a plugin declares its own budget rather than the composition root passing
    #: it beside the spec.
    budget: float | None = None


@runtime_checkable
class PluginAPI(Protocol):
    """What a plugin's `register(api)` is handed.

    A Protocol, so the kernel's registry satisfies it by shape without the
    plugin importing the kernel — the dependency rule (a plugin imports `sdk`
    only) in the type system. `config` is the plugin's own validated config
    block (or `None` if it takes none); the register methods are how it
    contributes a capability.

    `register(api)` runs once per contribution lifecycle (memory kinds are
    filled at one point, task-type graphs at another), so a plugin declares
    everything each time and the host honours only the calls its lifecycle
    serves — the others are no-ops. `caps` is the composition root's boot
    capabilities (its `prepare_node`, `make_harness`, tool `servers`, and the
    `sender`/`approver` identities a queued row uses); it is present only in the
    task-type lifecycle, so a plugin reaches it only from inside a deferred
    `graph`/`deps` builder, never eagerly at register time.
    """

    config: Any
    #: The composition root's boot capabilities, or `None` outside the
    #: task-type lifecycle. Typed `Any` because its shape is the composition
    #: root's, not a contract the plugin should bind to — the plugin duck-types
    #: `caps.prepare_node(...)`, `caps.make_harness(...)`, `caps.servers`,
    #: `caps.sender`, `caps.approver` from inside a deferred builder.
    caps: Any

    def task_type(self, spec: TaskTypeSpec) -> None:
        """Register one task type. A duplicate name refuses the boot."""
        ...

    def memory_kind(self, spec: MemoryKindSpec) -> None:
        """Register one memory kind. A duplicate name refuses the boot."""
        ...

    def reader(self, name: str, needs: frozenset[str]) -> None:
        """Declare that a reader (an agent, or `"code"`) reads these kinds —
        the reverse of a kind naming its readers (DESIGN-v2 §9.2). Merges with
        the core routing, so a plugin adds its kinds to `code` without naming
        what the core already routes there."""
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
