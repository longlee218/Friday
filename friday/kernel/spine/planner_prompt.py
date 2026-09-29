"""What the Planner is told and what it answers with (build-the-spine ticket
11; decision: board `domains-plug-in` ticket 12, as amended by 17).

The instructions are core text and hold no domain knowledge: whatever a domain
knows about planning reaches the Planner as its action's `planning`, the
operator's memory and skills, and the descriptions its agents and toolsets
declare. The answer is a `PlanAnswer` — goal and steps only; the task, the
version, `replaces` and the contract are the core's to fill in (`planner.py`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import TypeAdapter

from friday.kernel.harness.run_agent import terminal_tools
from friday.kernel.spine.plan import (
    AgentStep,
    AskStep,
    DraftStep,
    HandOverStep,
    Plan,
    Step,
)
from friday.sdk.action import Action
from friday.sdk.actions import Replan
from friday.sdk.agent import AgentSpec
from friday.sdk.intake import IntakeContext
from friday.sdk.toolset import ToolsetSpec

__all__ = [
    "INSTRUCTIONS",
    "PlanAnswer",
    "PlannedStep",
    "first_prompt",
    "refusal_prompt",
    "replan_prompt",
    "to_steps",
]

INSTRUCTIONS = """\
You are Friday's Planner. You write the plan for one task: a straight list of \
steps the core runs in order, no branches. You never investigate the \
reporter's system yourself — the agents do that. You may read what Friday \
already knows (memory, skills) first; then answer once, with the plan.

Step types:
- agent: run one of the allowed agents. `agent` names it; `toolsets` grants \
it toolsets, only from those listed for that agent; `brief` says what to \
establish and where to look first.
- ask: ask the reporter one question up front, only when the request is too \
vague to start and no agent could find the missing piece by reading. \
`question` is sent as written.
- hand_over: give the task to the operator up front, when nothing an agent \
can read would settle it. `reason` is for the operator.
- draft: the core writes the reply from the steps it `reads`.

Rules — a plan that breaks one is refused and comes back to you with every \
error:
- exactly the last step is draft, ask or hand_over; no earlier step is one \
of these;
- step ids are unique; `reads` names earlier steps only; a draft reads at \
least one step;
- only the step types, agents and toolsets the action allows, and no more \
steps than its limit.

Prefer the fewest steps that settle the request. When a plan comes back \
refused, fix every error it names and give the whole plan again."""


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """One step of the plan. `id` is short and unique ("s1"). `type` is
    agent | ask | hand_over | draft. Fill only the fields its type uses:
    agent → `agent`, `toolsets`, `brief`; ask → `question`; hand_over →
    `reason`; draft → nothing more. `reads` lists the ids of earlier steps
    whose result this step is handed."""

    id: str
    type: Literal["agent", "ask", "hand_over", "draft"]
    agent: str = ""
    toolsets: list[str] = field(default_factory=list)
    brief: str = ""
    question: str = ""
    reason: str = ""
    reads: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PlanAnswer:
    """The Planner's answer: the goal and the steps, in order."""

    goal: str = field(metadata={"doc": "one sentence: what this task must establish"})
    steps: list[PlannedStep] = field(
        metadata={"doc": "the steps in the order they run; the last one ends the plan"}
    )


def to_steps(answer: PlanAnswer) -> tuple[Step, ...]:
    """The answer's steps as the spine's own step types. A field the type
    does not use is dropped; one it needs and lacks stays empty for GatePlan
    to refuse."""
    steps: list[Step] = []
    for s in answer.steps:
        reads = tuple(s.reads)
        if s.type == "agent":
            steps.append(AgentStep(s.id, s.agent, tuple(s.toolsets), s.brief, reads))
        elif s.type == "ask":
            steps.append(AskStep(s.id, s.question, reads))
        elif s.type == "hand_over":
            steps.append(HandOverStep(s.id, s.reason, reads))
        else:
            steps.append(DraftStep(s.id, reads))
    return tuple(steps)


def first_prompt(
    action: Action,
    intake: IntakeContext,
    agents: Mapping[str, AgentSpec],
    toolsets: Mapping[str, ToolsetSpec],
) -> str:
    """The opening message: the case, the action, and what may be used."""
    return "\n\n".join(_case(intake) + _action(action, agents, toolsets))


def replan_prompt(
    action: Action,
    intake: IntakeContext,
    agents: Mapping[str, AgentSpec],
    toolsets: Mapping[str, ToolsetSpec],
    *,
    current: Plan,
    results: Mapping[str, Any],
    signal: Replan,
) -> str:
    """A replan's opening message: the same, plus the plan being replaced,
    the results of its finished steps and why it is being replaced. Steps
    that stay the same keep their results."""
    replacing = [
        f"## The current plan (v{current.plan_version}) — being replaced\n"
        f"{_json({'goal': current.goal, 'steps': current.steps})}",
        "## Results of its finished steps\n"
        + (
            "\n".join(f"- {sid}: {_json(value)}" for sid, value in results.items())
            or "none"
        ),
        f"## Why it is being replaced\n{signal.reason}\n\nWhat was found: "
        f"{signal.found}\n\nWrite the next plan. A step left exactly as it was "
        f"keeps its result and does not run again — so change or drop the "
        f"step that asked for this replan.",
    ]
    return "\n\n".join(_case(intake) + _action(action, agents, toolsets) + replacing)


def refusal_prompt(errors: tuple[str, ...], refused: Plan | None = None) -> str:
    """What a refused plan comes back as: the plan itself, when there is one,
    and every error."""
    listed = "\n".join(f"- {e}" for e in errors)
    plan = (
        ""
        if refused is None
        else f"The plan:\n{_json({'goal': refused.goal, 'steps': refused.steps})}\n\n"
    )
    return (
        f"GatePlan refused your plan.\n{plan}Errors:\n{listed}\n\n"
        f"Fix every error and give the whole plan again."
    )


def _case(intake: IntakeContext) -> list[str]:
    sections = [
        f"## The request (reported {intake.reported_at})\n{intake.request_text}"
    ]
    if intake.domain is not None:
        sections.append(f"## What intake placed\n{_json(intake.domain)}")
    if intake.memory:
        sections.append(
            "## What Friday remembers\n" + "\n".join(f"- {m}" for m in intake.memory)
        )
    if intake.skills:
        sections.append(
            "## Skills that may apply\n" + "\n".join(f"- {s}" for s in intake.skills)
        )
    return sections


def _action(
    action: Action,
    agents: Mapping[str, AgentSpec],
    toolsets: Mapping[str, ToolsetSpec],
) -> list[str]:
    contract = action.contract
    sections = [
        f"## The action: {action.name}\n"
        f"- step types allowed: {_names(contract.allowed_step_types)}\n"
        f"- at most {contract.limits.max_steps} steps\n"
        f"- constraints: {'; '.join(contract.constraints) or 'none'}\n"
        f"- a finished task has: {contract.acceptance_template}"
    ]
    if action.planning:
        sections.append(f"## How to plan this action\n{action.planning}")
    terminals = ", ".join(t.__name__ for t in terminal_tools(contract))
    described = []
    for name in sorted(contract.allowed_agents):
        spec = agents.get(name)
        if spec is None:
            continue
        granted = sorted(contract.allowed_toolsets & set(spec.toolsets))
        lines = [
            f"### {spec.name}",
            spec.description,
            f"- returns: {spec.result.__name__}, or ends early with: {terminals}",
            f"- budget: {spec.budget.max_turns} turns, {spec.budget.tokens} tokens",
            "- toolsets it may be granted:" + ("" if granted else " none"),
        ]
        lines += [
            f"  - {t}: {toolsets[t].description}" if t in toolsets else f"  - {t}"
            for t in granted
        ]
        described.append("\n".join(lines))
    sections.append(
        "## Agents allowed\n" + ("\n\n".join(described) if described else "none")
    )
    return sections


def _json(value: Any) -> str:
    """`value` as compact JSON for the prompt — dataclasses by field."""
    try:
        plain = TypeAdapter(Any).dump_python(value, mode="json")
    except Exception:  # noqa: BLE001 — a value that will not serialise is shown as text
        return str(value)
    return json.dumps(plain, ensure_ascii=False)


def _names(names) -> str:
    return ", ".join(sorted(names)) or "none"
