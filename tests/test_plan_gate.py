"""GatePlan: what a plan must pass before it runs.

Build-the-spine ticket 06 (decision: `domains-plug-in` ticket 11, amended by
17 — no time check). Order: schema + shape (stop on fail) → contract (every
error gathered; refuse, never clip) → limits (`max_steps`) → freeze + hash.
"""

from __future__ import annotations

from dataclasses import replace

from friday.kernel.spine.plan import AgentStep, AskStep, DraftStep, plan_hash
from friday.kernel.spine.plan_gate import Frozen, Refused, gate_plan
from friday.sdk import AgentSpec, Budget, Limits
from tests.test_plan import CONTRACT, P1, V1, _plan


class Diagnosis:
    pass


DIAGNOSE = AgentSpec(
    name="demo.diagnose",
    description="finds why a request failed",
    instructions="read, then say why",
    result=Diagnosis,
    tier="strong",
    toolsets=("demo.logs", "demo.code"),
    budget=Budget(max_turns=20, tokens=100_000),
    temperature=0.0,
)
ROGUE = replace(DIAGNOSE, name="demo.rogue")
AGENTS = {a.name: a for a in (DIAGNOSE, ROGUE)}


def test_a_plan_inside_the_contract_is_frozen_with_its_hash():
    result = gate_plan(V1, AGENTS)
    assert result == Frozen(plan=V1, plan_hash=plan_hash(V1))


def test_a_shape_failure_stops_before_the_contract_is_read():
    plan = _plan(replace(P1, agent="demo.rogue"), DraftStep("d", reads=()))
    assert gate_plan(plan, AGENTS) == Refused(
        errors=("d: a draft must read at least one step",)
    )


def test_a_contract_breach_returns_every_error_never_a_clipped_plan():
    narrow = replace(
        CONTRACT,
        allowed_step_types=frozenset({"agent", "draft"}),
        limits=Limits(max_replans=2, max_steps=5),
    )
    plan = _plan(
        replace(P1, toolsets=("demo.logs", "demo.db")),  # outside the contract
        AgentStep("p2", "demo.diagnose", ("core.memory",), "x"),  # outside the agent
        AgentStep("p3", "demo.rogue", ("demo.logs",), "y"),  # agent not allowed
        AskStep("p4", "which env?"),  # step type not allowed
        contract=narrow,
    )
    result = gate_plan(plan, AGENTS)
    assert isinstance(result, Refused)
    assert set(result.errors) == {
        "p1: toolset demo.db is outside what demo.diagnose may be granted "
        "(demo.code, demo.logs)",
        "p2: toolset core.memory is outside what demo.diagnose may be granted "
        "(demo.code, demo.logs)",
        "p3: agent demo.rogue is not allowed by the contract (demo.diagnose)",
        "p4: step type ask is not allowed by the contract (agent, draft)",
    }
    assert len(result.errors) == 4


def test_an_unregistered_agent_is_refused_not_granted_the_contract():
    """Even one the contract allows: with no spec there is no ceiling to
    intersect, and the gate fails closed."""
    wide = replace(CONTRACT, allowed_agents=CONTRACT.allowed_agents | {"demo.ghost"})
    plan = _plan(
        replace(P1, agent="demo.ghost"), DraftStep("d", reads=("p1",)), contract=wide
    )
    assert gate_plan(plan, AGENTS) == Refused(
        errors=("p1: agent demo.ghost is not registered",)
    )


def test_a_plan_longer_than_max_steps_is_refused():
    plan = _plan(
        P1, replace(P1, id="p2"), replace(P1, id="p3"), DraftStep("d", reads=("p1",))
    )
    assert gate_plan(plan, AGENTS) == Refused(
        errors=("the plan has 4 steps; the contract allows at most 3",)
    )


def test_a_plan_of_exactly_max_steps_passes():
    plan = _plan(P1, replace(P1, id="p2"), DraftStep("d", reads=("p1",)))
    assert isinstance(gate_plan(plan, AGENTS), Frozen)


def test_contract_and_limit_errors_are_returned_together():
    plan = _plan(
        P1,
        replace(P1, id="p2"),
        replace(P1, id="p3", agent="demo.rogue"),
        DraftStep("d", reads=("p1",)),
    )
    assert set(gate_plan(plan, AGENTS).errors) == {
        "p3: agent demo.rogue is not allowed by the contract (demo.diagnose)",
        "the plan has 4 steps; the contract allows at most 3",
    }
