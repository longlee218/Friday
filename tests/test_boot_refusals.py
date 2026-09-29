"""The boot refusals: a registry the spine cannot run with is refused at boot.

One test per refusal (build-the-spine ticket 05). Each starts from `_demo`, a
plugin whose declarations boot clean, and breaks exactly one thing, so a test
that passes proves its own guard and nothing else. Refusal 9 (`core.shell`
granted with no `shell_hosts` declared) landed with `core.shell` in ticket 08.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from friday.kernel.boot_refusals import refusals
from friday.kernel.plugin_host import BootRefused, load_plugins
from friday.kernel.registry import DuplicateRegistration, Registry
from friday.sdk import (
    Action,
    ActionContract,
    AgentSpec,
    Budget,
    EvalSpec,
    Limits,
    Plugin,
    Recognition,
    ToolsetSpec,
)

TIERS = {"strong"}
SERVERS = {"devops-generic"}


@dataclass(frozen=True)
class Place:
    service: str = ""


@dataclass(frozen=True)
class OtherPlace:
    host: str = ""


@dataclass(frozen=True)
class Finding:
    cause: str


def enrich(seed: object) -> Place:
    return Place()


def _toolset(name: str = "demo.logs", **kw) -> ToolsetSpec:
    return ToolsetSpec(
        **{
            "name": name,
            "description": "log lines for the case window",
            "factory": lambda run: [],
            "mcp": {"devops-generic": frozenset({"loki_query"})},
            "domain_type": Place,
            **kw,
        }
    )


def _agent(name: str = "demo.diagnose", **kw) -> AgentSpec:
    return AgentSpec(
        **{
            "name": name,
            "description": "reads logs to find why a request failed",
            "instructions": "find the cause",
            "result": Finding,
            "tier": "strong",
            "toolsets": ("demo.logs",),
            "budget": Budget(max_turns=10, tokens=10_000),
            "temperature": 0.0,
            **kw,
        }
    )


def _contract(**kw) -> ActionContract:
    return ActionContract(
        **{
            "allowed_step_types": frozenset({"agent", "draft"}),
            "allowed_agents": frozenset({"demo.diagnose"}),
            "allowed_toolsets": frozenset({"demo.logs"}),
            "constraints": ("every ref points at a line that was read",),
            "approval_policy": "a reply waits for approval",
            "acceptance_template": "the cause, with refs",
            "limits": Limits(max_replans=1, max_steps=3),
            **kw,
        }
    )


def _recognition(**kw) -> Recognition:
    return Recognition(
        **{
            "means": "a request of ours fails",
            "pick_when": ("an error, a status",),
            "not_when": (("asks how, not why", "demo.explain"),),
            "examples": ("POST /v1/x returns 400",),
            **kw,
        }
    )


def _explain() -> Action:
    return Action(
        "demo.explain",
        Recognition(
            "how a rule works", ("a how question",), (), ("how is x counted?",)
        ),
        _contract(allowed_agents=frozenset(), allowed_toolsets=frozenset()),
    )


def _eval(name: str = "demo.trace") -> EvalSpec:
    return EvalSpec(
        name, "each case's label", cases=lambda: (), checks={}, report=lambda r: ""
    )


def _demo(
    *, toolsets=None, agents=None, actions=None, readers=None, evals=(), enricher=enrich
) -> Plugin:
    toolsets = [_toolset()] if toolsets is None else toolsets
    agents = [_agent()] if agents is None else agents
    actions = (
        [Action("demo.trace", _recognition(), _contract()), _explain()]
        if actions is None
        else actions
    )
    readers = {"demo.diagnose": frozenset({"fact"})} if readers is None else readers

    def register(api) -> None:
        for t in toolsets:
            api.toolset(t)
        for a in agents:
            api.agent(a)
        for a in actions:
            api.action(a)
        for name, kinds in readers.items():
            api.reader(name, kinds)
        for e in evals:
            api.eval(e)

    return Plugin(id="demo", register=register, enricher=enricher)


def _refusals(*plugins: Plugin) -> list[str]:
    registry = Registry()
    for p in plugins:
        registry.apply(p)
    return refusals(registry, tiers=TIERS, servers=SERVERS)


def _one(*plugins: Plugin) -> str:
    errors = _refusals(*plugins)
    assert len(errors) == 1, errors
    return errors[0]


def test_the_demo_plugin_boots_clean():
    assert _refusals(_demo()) == []


# 1 ─ a duplicate name
@pytest.mark.parametrize("kind", ["action", "agent", "toolset", "eval"])
def test_1_a_duplicate_name_refuses(kind):
    dup = {
        "action": dict(actions=[_explain(), _explain()]),
        "agent": dict(agents=[_agent(), _agent()]),
        "toolset": dict(toolsets=[_toolset(), _toolset()]),
        "eval": dict(evals=[_eval(), _eval()]),
    }[kind]
    with pytest.raises(DuplicateRegistration):
        _refusals(_demo(**dup))


# 2 ─ a name not under the plugin's id
def test_2_a_name_outside_the_plugins_namespace_refuses():
    assert "not named under 'demo'" in _one(
        _demo(toolsets=[_toolset(), _toolset("core.extra")])
    )


def test_2_an_eval_outside_the_plugins_namespace_refuses():
    assert "'core.triage' is registered by plugin 'demo'" in _one(
        _demo(evals=[_eval(), _eval("core.triage")])
    )


# 3 ─ a tier config.yaml does not declare
def test_3_an_undeclared_tier_refuses():
    assert "tier 'super'" in _one(_demo(agents=[_agent(tier="super")]))


# 4 ─ a dangling reference
def test_4_an_agent_naming_an_unregistered_toolset_refuses():
    assert "names toolset 'demo.db'" in _one(
        _demo(agents=[_agent(toolsets=("demo.logs", "demo.db"))])
    )


def test_4_a_contract_granting_an_unregistered_agent_refuses():
    action = Action(
        "demo.trace",
        _recognition(),
        _contract(allowed_agents=frozenset({"demo.diagnose", "demo.ghost"})),
    )
    assert "grants agent 'demo.ghost'" in _one(_demo(actions=[action, _explain()]))


def test_4_a_contract_granting_an_unregistered_toolset_refuses():
    action = Action(
        "demo.trace",
        _recognition(),
        _contract(allowed_toolsets=frozenset({"demo.logs", "demo.db"})),
    )
    assert "grants toolset 'demo.db'" in _one(_demo(actions=[action, _explain()]))


def test_4_a_reader_naming_no_agent_refuses():
    assert "reader 'demo.ghost'" in _one(
        _demo(readers={"demo.ghost": frozenset({"fact"}), "code": frozenset({"fact"})})
    )


def test_4_a_plugin_with_no_agent_specs_yet_keeps_its_dag_readers():
    """Temporary until ticket 16: a plugin still on the DAG path names readers
    that have no `AgentSpec`; the reader check waits for its first spec."""
    assert (
        _refusals(
            _demo(
                toolsets=[],
                agents=[],
                actions=[],
                readers={"demo.old_agent": frozenset({"fact"})},
            )
        )
        == []
    )


# 5 ─ a toolset of another domain type
def test_5_a_toolset_reading_another_domain_type_refuses():
    assert "reads a OtherPlace domain" in _one(
        _demo(toolsets=[_toolset(domain_type=OtherPlace)])
    )


def test_5_a_domain_toolset_in_a_plugin_without_an_enricher_refuses():
    assert "its domain is None" in _one(_demo(enricher=None))


# 6 ─ an MCP server config.yaml does not declare
def test_6_an_undeclared_mcp_server_refuses():
    assert "MCP server 'db-generic'" in _one(
        _demo(toolsets=[_toolset(mcp={"db-generic": frozenset({"query"})})])
    )


# 7 ─ the recognition checks
def _with_recognition(**kw) -> Plugin:
    return _demo(
        actions=[Action("demo.trace", _recognition(**kw), _contract()), _explain()]
    )


def test_7_not_when_naming_an_unregistered_action_refuses():
    assert "names 'demo.ghost' in `not_when`" in _one(
        _with_recognition(not_when=(("x", "demo.ghost"),))
    )


def test_7_not_when_naming_itself_refuses():
    assert "names itself" in _one(_with_recognition(not_when=(("x", "demo.trace"),)))


def test_7_an_example_claimed_by_two_actions_refuses():
    assert "claimed by both" in _one(_with_recognition(examples=("how is x counted?",)))


def test_7_an_action_with_no_examples_refuses():
    assert "no recognition examples" in _one(_with_recognition(examples=()))


def test_7_an_empty_means_refuses():
    assert "empty recognition `means`" in _one(_with_recognition(means="  "))


def test_7_an_empty_pick_when_refuses():
    assert "empty recognition `pick_when`" in _one(_with_recognition(pick_when=("",)))


# 8 ─ a cross-plugin grant
def test_8_a_contract_granting_another_plugins_toolset_refuses():
    ops_action = Action(
        "ops.grant",
        Recognition("access", ("grant me",), (), ("let me in",)),
        _contract(
            allowed_agents=frozenset(), allowed_toolsets=frozenset({"demo.logs"})
        ),
    )

    def ops_register(api) -> None:
        api.action(ops_action)

    ops = Plugin(id="ops", register=ops_register)
    errors = _refusals(_demo(), ops)
    assert any("belongs to another plugin" in e for e in errors), errors


# + a missing description
def test_an_agent_without_a_description_refuses():
    assert "agent 'demo.diagnose' has no description" in _one(
        _demo(agents=[_agent(description=" ")])
    )


def test_a_toolset_without_a_description_refuses():
    assert "toolset 'demo.logs' has no description" in _one(
        _demo(toolsets=[_toolset(description="")])
    )


# 9: core.shell granted, no host declared
def test_granting_core_shell_with_no_shell_hosts_refuses(monkeypatch):
    shell = _contract(allowed_toolsets=frozenset({"demo.logs", "core.shell"}))
    demo = _demo(actions=[Action("demo.trace", _recognition(), shell), _explain()])
    monkeypatch.setattr(
        "friday.kernel.plugin_host.configured_plugins", lambda config: [(demo, None)]
    )
    with pytest.raises(BootRefused) as info:
        load_plugins(SimpleNamespace(shell_hosts=()))
    assert (
        "action 'demo.trace' grants 'core.shell', but config.yaml declares no shell_hosts"
        in str(info.value)
    )

    load_plugins(SimpleNamespace(shell_hosts=("dev",)))
    load_plugins(SimpleNamespace())  # no config in hand: skipped


# the host
def test_the_host_refuses_the_boot_with_every_reason(monkeypatch):
    broken = _demo(agents=[_agent(tier="super", description="")])
    monkeypatch.setattr(
        "friday.kernel.plugin_host.configured_plugins", lambda config: [(broken, None)]
    )
    config = SimpleNamespace(
        tiers={"strong": None}, mcp_servers=(SimpleNamespace(name="devops-generic"),)
    )
    with pytest.raises(BootRefused) as info:
        load_plugins(config)
    assert "tier 'super'" in str(info.value)
    assert "has no description" in str(info.value)


def test_register_runs_once_per_load(monkeypatch):
    calls = []
    plugin = Plugin(id="demo", register=lambda api: calls.append(api))
    monkeypatch.setattr(
        "friday.kernel.plugin_host.configured_plugins", lambda config: [(plugin, None)]
    )
    load_plugins(SimpleNamespace())
    assert len(calls) == 1


def test_a_config_without_tiers_or_servers_skips_those_checks(monkeypatch):
    """The memory-kinds default config carries neither; it must not refuse an
    agent's tier or a toolset's server it has no way to know."""
    monkeypatch.setattr(
        "friday.kernel.plugin_host.configured_plugins", lambda config: [(_demo(), None)]
    )
    load_plugins(SimpleNamespace())
