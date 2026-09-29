"""GatePlan: what a plan must pass before it runs. Plain code, no model call.

Build-the-spine ticket 06 (decision: board `domains-plug-in` ticket 11, as
amended by 17). Run on every new plan version, in order:

1. schema + shape — a breach stops here, with the shape errors alone;
2. contract — every step type, agent and granted toolset inside the action
   contract (a toolset also inside the agent's own ceiling); every breach is
   gathered and the plan is **refused, never clipped**;
3. limits — `max_steps`, counted over the whole plan (no time check);
4. freeze + hash.

Sensitivity and egress are deferred until the first `egress` toolset lands;
the check goes here when it does.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from friday.kernel.spine.plan import AgentStep, Plan, plan_hash, shape_errors
from friday.sdk.action import ActionContract
from friday.sdk.agent import AgentSpec

__all__ = [
    "Frozen",
    "Refused",
    "full_grant",
    "gate_plan",
]


def full_grant(contract: ActionContract, spec: AgentSpec) -> frozenset[str]:
    """The most an agent step may be granted: the contract's toolsets that
    are also inside the agent's own ceiling. The Planner's empty `toolsets`
    means all of it; GatePlan refuses anything beyond it."""
    return contract.allowed_toolsets & frozenset(spec.toolsets)


@dataclass(frozen=True, slots=True)
class Frozen:
    """The plan passed; `plan_hash` is what it is stored and replaced by."""

    plan: Plan
    plan_hash: str


@dataclass(frozen=True, slots=True)
class Refused:
    """The plan did not pass; every error, as text the Planner rewrites from."""

    errors: tuple[str, ...]


def gate_plan(plan: Plan, agents: Mapping[str, AgentSpec]) -> Frozen | Refused:
    """Gate `plan` against its own contract and the registered `agents`."""
    shape = shape_errors(plan)
    if shape:
        return Refused(tuple(shape))
    errors = _contract_errors(plan, agents) + _limit_errors(plan)
    if errors:
        return Refused(tuple(errors))
    return Frozen(plan=plan, plan_hash=plan_hash(plan))


def _contract_errors(plan: Plan, agents: Mapping[str, AgentSpec]) -> list[str]:
    contract = plan.contract
    errors: list[str] = []
    for step in plan.steps:
        if step.type not in contract.allowed_step_types:
            errors.append(
                f"{step.id}: step type {step.type} is not allowed by the "
                f"contract ({_names(contract.allowed_step_types)})"
            )
        if not isinstance(step, AgentStep):
            continue
        if step.agent not in contract.allowed_agents:
            errors.append(
                f"{step.id}: agent {step.agent} is not allowed by the "
                f"contract ({_names(contract.allowed_agents)})"
            )
        spec = agents.get(step.agent)
        if spec is None:
            errors.append(f"{step.id}: agent {step.agent} is not registered")
            continue
        ceiling = full_grant(contract, spec)
        errors += [
            f"{step.id}: toolset {t} is outside what {step.agent} may be "
            f"granted ({_names(ceiling)})"
            for t in step.toolsets
            if t not in ceiling
        ]
    return errors


def _limit_errors(plan: Plan) -> list[str]:
    most = plan.contract.limits.max_steps
    if len(plan.steps) > most:
        return [
            f"the plan has {len(plan.steps)} steps; the contract allows at most {most}"
        ]
    return []


def _names(names) -> str:
    return ", ".join(sorted(names)) or "none"
