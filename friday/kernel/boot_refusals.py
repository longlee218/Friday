"""The boot refusals: what a registry is refused for, once every plugin has
registered.

Offline checks on registered data only (board `domains-plug-in`, tickets 02 §5
and 03 with its amendment, 12 §2). A duplicate name is refused earlier, by the
`Registry` itself. Not checked here: whether an allowlisted MCP tool exists on
its server — that needs the network, and the server filter and `Reads` refuse
it at call time. A `core.shell` host not declared in `config.yaml` is refused
once `core.shell` exists (build-the-spine ticket 08).

Returns every refusal rather than the first, so one boot shows the operator
the whole list.
"""

from __future__ import annotations

import typing
from collections.abc import Iterable

from friday.kernel.registry import Registry
from friday.sdk.action import Action
from friday.sdk.plugin import Plugin

__all__ = ["refusals"]

#: The namespace of everything the core registers; any plugin may grant it.
CORE_PREFIX = "core."

#: A reader that is not an agent: the kernel's own code path.
_CODE_READER = "code"


def domain_type(plugin: Plugin | None) -> type | None:
    """The plugin's domain type: its enricher's return annotation, or `None`
    for a domain with no enricher. An annotation that cannot be resolved
    raises here, naming the type, rather than reading as "no domain"."""
    if plugin is None or plugin.enricher is None:
        return None
    return typing.get_type_hints(plugin.enricher).get("return")


def refusals(
    registry: Registry, *, tiers: Iterable[str] | None, servers: Iterable[str] | None
) -> list[str]:
    """Every reason this registry cannot boot, or `[]`. `tiers` are the tier
    names `config.yaml` declares; `servers` the MCP server names it declares.
    `None` means no config is in hand (a script or test registering memory
    kinds only), so that check is skipped rather than failed."""
    out: list[str] = []
    out += _namespaces(registry)
    out += _descriptions(registry)
    out += _agents(registry, None if tiers is None else set(tiers))
    if servers is not None:
        out += _toolsets(registry, set(servers))
    out += _readers(registry)
    for action in registry.actions().values():
        out += _contract(registry, action)
        out += _recognition(registry, action)
    out += _shared_examples(registry)
    return out


def _namespaces(registry: Registry) -> list[str]:
    names = [*registry.actions(), *registry.agents(), *registry.toolsets(), *registry.memory_kinds()]
    out = []
    for name in names:
        owner = registry.owner_of(name)
        if owner is not None and not name.startswith(f"{owner.id}."):
            out.append(f"{name!r} is registered by plugin {owner.id!r} but not named under {owner.id!r}.")
    return out


def _descriptions(registry: Registry) -> list[str]:
    specs = [("agent", s) for s in registry.agents().values()]
    specs += [("toolset", s) for s in registry.toolsets().values()]
    return [f"{what} {s.name!r} has no description" for what, s in specs if not s.description.strip()]


def _agents(registry: Registry, tiers: set[str] | None) -> list[str]:
    out = []
    toolsets = registry.toolsets()
    for agent in registry.agents().values():
        if tiers is not None and agent.tier not in tiers:
            out.append(f"agent {agent.name!r} runs on tier {agent.tier!r}, which config.yaml does not declare")
        out += [f"agent {agent.name!r} names toolset {t!r}, which is not registered"
                for t in agent.toolsets if t not in toolsets]
    return out


def _toolsets(registry: Registry, servers: set[str]) -> list[str]:
    return [
        f"toolset {ts.name!r} reads MCP server {server!r}, which config.yaml does not declare"
        for ts in registry.toolsets().values()
        for server in ts.mcp
        if server not in servers
    ]


def _readers(registry: Registry) -> list[str]:
    """A plugin's reader names one of its agents, or `code`. Only for a plugin
    that registers at least one `AgentSpec`: until then its readers name DAG
    agents that have no spec (temporary, ticket 16 deletes the DAG path)."""
    with_specs = {registry.owner_of(n).id for n in registry.agents() if registry.owner_of(n)}
    agents = registry.agents()
    return [
        f"reader {name!r} is not a registered agent (nor {_CODE_READER!r})"
        for name in registry.readers()
        if name != _CODE_READER and name not in agents
        and registry.reader_owners(name) & with_specs
    ]


def _contract(registry: Registry, action: Action) -> list[str]:
    owner = registry.owner_of(action.name)
    mine = f"{owner.id}." if owner else CORE_PREFIX
    contract = action.contract
    agents, toolsets = registry.agents(), registry.toolsets()
    out = [f"action {action.name!r} grants agent {a!r}, which is not registered"
           for a in sorted(contract.allowed_agents) if a not in agents]
    out += [f"action {action.name!r} grants toolset {t!r}, which is not registered"
            for t in sorted(contract.allowed_toolsets) if t not in toolsets]
    out += [f"action {action.name!r} grants {x!r}, which belongs to another plugin"
            for x in sorted(contract.allowed_agents | contract.allowed_toolsets)
            if not (x.startswith(mine) or x.startswith(CORE_PREFIX))]
    domain = domain_type(owner)
    for name in sorted(contract.allowed_toolsets):
        ts = toolsets.get(name)
        if ts is not None and ts.domain_type is not None and ts.domain_type is not domain:
            out.append(
                f"action {action.name!r} grants toolset {name!r}, which reads a "
                f"{ts.domain_type.__name__} domain, but its domain is "
                f"{getattr(domain, '__name__', None)}"
            )
    return out


def _recognition(registry: Registry, action: Action) -> list[str]:
    r = action.recognition
    out = []
    if not r.means.strip():
        out.append(f"action {action.name!r} has an empty recognition `means`")
    if not any(s.strip() for s in r.pick_when):
        out.append(f"action {action.name!r} has an empty recognition `pick_when`")
    if not r.examples:
        out.append(f"action {action.name!r} has no recognition examples")
    actions = registry.actions()
    for _signal, other in r.not_when:
        if other == action.name:
            out.append(f"action {action.name!r} names itself in `not_when`")
        elif other not in actions:
            out.append(f"action {action.name!r} names {other!r} in `not_when`, which is not registered")
    return out


def _shared_examples(registry: Registry) -> list[str]:
    seen: dict[str, str] = {}
    out = []
    for action in registry.actions().values():
        for text in action.recognition.examples:
            first = seen.setdefault(text.strip(), action.name)
            if first != action.name:
                out.append(f"example {text!r} is claimed by both {first!r} and {action.name!r}")
    return out
