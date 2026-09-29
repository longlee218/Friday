"""The spine, bound once at boot from the configured plugins (build-the-spine
ticket 14): their actions, agents and toolsets, their domains' enrichers,
the core toolsets bound to the store and the skill library, the tiers, the
Planner's config and the open tool servers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from friday.kernel.plugin_host import configured_plugins, load_plugins
from friday.kernel.spine.planner import PLANNER
from friday.kernel.spine.workflow import Spine
from friday.kernel.toolsets import core_toolsets

__all__ = ["build_spine"]


def build_spine(
    config: Any,
    *,
    db: Any,
    actions: Iterable[str],
    skills: Any = None,
    servers: Mapping[str, Any] | None = None,
    responder: Any = None,
    record: Any = None,
) -> Spine:
    """The `Spine` that runs `actions` — the rest stay on the DAG path until
    ticket 16. A name no plugin registers refuses the boot."""
    registry = load_plugins(config).registry
    registered = registry.actions()
    unknown = [name for name in actions if name not in registered]
    if unknown:
        raise ValueError(f"the spine cannot run unregistered actions: {unknown}")
    bound = {
        spec.name: spec
        for spec in core_toolsets(
            db=db, skills=skills, hosts=getattr(config, "shell_hosts", ()) or ()
        )
    }
    return Spine(
        db=db,
        actions={name: registered[name] for name in actions},
        agents=dict(registry.agents()),
        # The registry's core toolsets were declared before the store was
        # open; these are the same four, bound to it.
        toolsets={**registry.toolsets(), **bound},
        tiers=dict(config.tiers),
        planner=config.agent(PLANNER),
        enrichers={
            plugin.id: plugin.enricher for plugin, _ in configured_plugins(config)
        },
        servers=dict(servers or {}),
        responder=responder,
        record=record,
        auto_ask=config.workflows.auto_ask_for_details,
    )
