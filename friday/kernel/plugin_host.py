"""Loading the plugins a build is configured with, and the two APIs they
register against.

Composition, not kernel: this module imports plugin packages by path and both
registries, which the kernel may not. A plugin's `register(api)` runs once per
contribution lifecycle — memory kinds are filled before the database opens,
task-type graphs when the run's tool servers exist — so there are two `PluginAPI`
implementations, each honouring only the calls its lifecycle serves and treating
the rest as no-ops. That is what lets one `register(api)` declare a plugin's
kinds and its task type together without the host caring which came first.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "MemoryKindAPI",
    "TaskTypeAPI",
    "configured_plugins",
]


def configured_plugins(config: Any) -> list[tuple[Any, Any]]:
    """The `(plugin, validated config block)` for every plugin this build
    names. `config.plugins` is a list of import paths; each package exposes one
    `PLUGIN` and, optionally, a `load_config(raw)` that validates its own block
    (read from `config.plugin_blocks` by the plugin's id)."""
    out: list[tuple[Any, Any]] = []
    for path in getattr(config, "plugins", None) or ("plugins.devops", "plugins.docs"):
        module = importlib.import_module(path)
        plugin = module.PLUGIN
        raw = (getattr(config, "plugin_blocks", None) or {}).get(plugin.id)
        loader = getattr(module, "load_config", None)
        if loader is not None:
            cfg = loader(raw)
        elif plugin.config is not None:
            cfg = plugin.config(**(raw or {}))
        else:
            cfg = None
        out.append((plugin, cfg))
    return out


class TaskTypeAPI:
    """The `PluginAPI` a plugin registers task types against. Its `memory_kind`
    and `reader` are no-ops — memory kinds are filled in their own lifecycle;
    here a `task_type` call builds the type's `DAG` (from `caps`) and registers
    it."""

    def __init__(self, caps: Any, config: Any) -> None:
        self.caps = caps
        self.config = config

    def task_type(self, spec: Any) -> None:
        from friday.kernel.dag import registry

        assert spec.graph is not None, f"task type {spec.name!r} registered no graph"
        registry.register_task_type(spec, dag=spec.graph(None))

    def memory_kind(self, spec: Any) -> None:
        return None

    def reader(self, name: str, needs: frozenset[str]) -> None:
        return None


class MemoryKindAPI:
    """The `PluginAPI` a plugin registers memory kinds and reader routing
    against. Its `task_type` is a no-op (and `caps` is `None`), so a plugin's
    graph/deps builders never run in this lifecycle — the kinds are static and
    need no boot capabilities."""

    caps = None

    def __init__(self, config: Any) -> None:
        self.config = config

    def memory_kind(self, spec: Any) -> None:
        from friday.kernel.memory import registry as memory_registry

        memory_registry.register_memory_kind(spec)

    def reader(self, name: str, needs: frozenset[str]) -> None:
        from friday.kernel.memory import registry as memory_registry

        memory_registry.register_reader(name, needs)

    def task_type(self, spec: Any) -> None:
        return None
