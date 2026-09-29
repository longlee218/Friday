"""Loading the plugins a build is configured with into one registry.

Composition, not kernel: this module imports plugin packages by path. Each
plugin's `register(api)` runs once per load against its own
`PluginRegistration`; every call is recorded, then the boot refusals run over
the whole registry and a broken declaration refuses the boot. The memory
registry and the task-type graphs each read what a load recorded — there is
no per-lifecycle API whose other calls are no-ops. Boot still loads more than
once (memory kinds before the db opens, then `check_graphs` and
`register_dags`) until ticket 16 deletes the DAG path.

The task-type graphs still need `caps`, which exist only once the run's tool
servers do, so `attach_caps` hands them to each plugin's registration after
`register` has returned (ticket 16 deletes `caps` with the DAG path).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from friday.kernel.boot_refusals import refusals
from friday.kernel.config import ConfigError
from friday.kernel.registry import DuplicateRegistration, PluginRegistration, Registry
from friday.kernel.toolsets import core_plugin
from friday.sdk.action import Action

__all__ = [
    "BootRefused",
    "Loaded",
    "configured_plugins",
    "load_plugins",
    "registered_actions",
]


class BootRefused(ConfigError):
    """The plugins' declarations do not add up; every reason is in the message."""


def configured_plugins(config: Any) -> list[tuple[Any, Any]]:
    """The `(plugin, validated config block)` for every plugin this build
    names. `config.plugins` is a list of import paths; each package exposes one
    `PLUGIN` and, optionally, a `load_config(raw)` that validates its own block
    (read from `config.plugin_blocks` by the plugin's id)."""
    out: list[tuple[Any, Any]] = []
    for path in getattr(config, "plugins", None) or ("plugins.backend", "plugins.ops"):
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


@dataclass(frozen=True)
class Loaded:
    """What one load recorded: the registry, and each plugin's registration."""

    registry: Registry
    registrations: tuple[PluginRegistration, ...]

    def attach_caps(self, caps: Any) -> None:
        for api in self.registrations:
            api.caps = caps


def _names(servers: Any) -> list[str] | None:
    return None if servers is None else [s.name for s in servers]


def load_plugins(config: Any) -> Loaded:
    """Run every configured plugin's `register` once, then refuse the boot on
    anything the registry cannot run with. `config.tiers` and
    `config.mcp_servers` are what an agent's tier and a toolset's servers are
    checked against; a config without them (the memory-kinds default) skips
    those two checks. The core's own toolsets (`core.*`) register first, under
    a `core` owner, with the config's `shell_hosts`."""
    registry = Registry()
    shell_hosts = getattr(config, "shell_hosts", None)
    core = core_plugin(hosts=shell_hosts or ())
    try:
        registry.apply(core)
        apis = tuple(
            registry.apply(plugin, cfg) for plugin, cfg in configured_plugins(config)
        )
    except DuplicateRegistration as exc:
        raise BootRefused(str(exc)) from exc
    errors = refusals(
        registry,
        tiers=getattr(config, "tiers", None),
        servers=_names(getattr(config, "mcp_servers", None)),
        shell_hosts=shell_hosts,
    )
    if errors:
        raise BootRefused("the plugins cannot boot:\n  " + "\n  ".join(errors))
    return Loaded(registry, apis)


def registered_actions(config: Any = None) -> list[Action]:
    """The actions the configured plugins register — what triage's prompt and
    closed set are built from. Loads only the plugin selection: the tier and
    MCP-server checks need the whole config and are the boot's (`register_all`
    runs them), not triage's; the recognition checks still refuse here."""

    class _Selection:
        plugins = getattr(config, "plugins", None)
        plugin_blocks = getattr(config, "plugin_blocks", None)

    return list(load_plugins(_Selection()).registry.actions().values())
