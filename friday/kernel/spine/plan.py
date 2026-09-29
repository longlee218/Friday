"""The plan: what one pass of a task will do, as a straight list of steps.

Plain values, not wired yet (build-the-spine ticket 06; decisions in board
`domains-plug-in`, tickets 10 and 14 §5). A plan is a **straight list** — no
branch, no parallel; a change of direction is a replan, a new version through
GatePlan (`plan_gate.py`). Four core-owned step types:

- `agent` runs a named agent with the toolsets granted to it;
- `ask` / `hand_over` are the Planner deciding that up front;
- `draft` is the core responder writing the `Reply` from the steps it reads.

`reads` names the earlier steps whose stored result a step is handed; every
step also gets the intake context. Results are stored by `(task_id,
step_key)`, so a replan's identical step reuses its result and a changed one
re-runs with everything that reads it.

`Ask`/`Reply`/`HandOver` and their union `Outcome` are what a plan comes to.
They still live in `friday/sdk/actions.py` because the DAG path's nodes import
them through the sdk; ticket 16 moves them here. Re-exported so the spine
names them from one place.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Literal

from friday.sdk.action import ActionContract, Limits
from friday.sdk.actions import Ask, HandOver, Outcome, Reply

__all__ = [
    "STEP_TYPES",
    "AgentStep",
    "Ask",
    "AskStep",
    "DraftStep",
    "HandOver",
    "HandOverStep",
    "Outcome",
    "Plan",
    "Reply",
    "Step",
    "plan_from_json",
    "plan_hash",
    "plan_json",
    "shape_errors",
    "step_keys",
]


@dataclass(frozen=True, slots=True)
class AgentStep:
    """Run `agent` with `toolsets` (⊆ contract ∩ the agent's ceiling). `brief`
    says what to establish and where to look first. Its result is the
    agent's declared result type, or an `Ask`/`HandOver` that stops the plan."""

    id: str
    agent: str
    toolsets: tuple[str, ...]
    brief: str
    reads: tuple[str, ...] = ()
    type: Literal["agent"] = field(default="agent", init=False)


@dataclass(frozen=True, slots=True)
class AskStep:
    """The Planner asks the reporter up front (the request is too vague)."""

    id: str
    question: str
    reads: tuple[str, ...] = ()
    type: Literal["ask"] = field(default="ask", init=False)


@dataclass(frozen=True, slots=True)
class HandOverStep:
    """The Planner decides up front that a person must take it."""

    id: str
    reason: str
    reads: tuple[str, ...] = ()
    type: Literal["hand_over"] = field(default="hand_over", init=False)


@dataclass(frozen=True, slots=True)
class DraftStep:
    """The core responder writes the `Reply` from `reads` + the intake
    context, under one fixed prompt — no per-plan guidance."""

    id: str
    reads: tuple[str, ...]
    type: Literal["draft"] = field(default="draft", init=False)


Step = AgentStep | AskStep | HandOverStep | DraftStep
STEP_TYPES = ("agent", "ask", "hand_over", "draft")
_TERMINAL = ("draft", "ask", "hand_over")


@dataclass(frozen=True, slots=True)
class Plan:
    """One version of a task's plan. `plan_version` is 1, then +1 per replan;
    `replaces` is the hash of the version it replaced (`None` for v1). The
    action's contract is copied in and hashed with the plan."""

    task_id: int
    action: str
    plan_version: int
    replaces: str | None
    contract: ActionContract
    goal: str
    steps: tuple[Step, ...]


def _plain(value: Any) -> Any:
    """`value` as JSON-ready data: dataclasses by field, sets sorted (their
    order moves with `PYTHONHASHSEED`), tuples as lists."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _sha256(value: Any) -> str:
    body = json.dumps(
        _plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(body.encode()).hexdigest()


def plan_json(plan: Plan) -> dict:
    """The plan as JSON data, for the `plans` table (ticket 14)."""
    return _plain(plan)


_STEP_OF = {
    "agent": AgentStep,
    "ask": AskStep,
    "hand_over": HandOverStep,
    "draft": DraftStep,
}


def plan_from_json(body: dict) -> Plan:
    """`plan_json` undone: the same plan, the same hash."""
    steps = []
    for raw in body["steps"]:
        fields_ = {k: v for k, v in raw.items() if k != "type"}
        for name in _TUPLE_FIELDS:
            if name in fields_:
                fields_[name] = tuple(fields_[name])
        steps.append(_STEP_OF[raw["type"]](**fields_))
    c = body["contract"]
    contract = ActionContract(
        allowed_step_types=frozenset(c["allowed_step_types"]),
        allowed_agents=frozenset(c["allowed_agents"]),
        allowed_toolsets=frozenset(c["allowed_toolsets"]),
        constraints=tuple(c["constraints"]),
        approval_policy=c["approval_policy"],
        acceptance_template=c["acceptance_template"],
        limits=Limits(**c["limits"]),
    )
    return Plan(
        task_id=body["task_id"],
        action=body["action"],
        plan_version=body["plan_version"],
        replaces=body["replaces"],
        contract=contract,
        goal=body["goal"],
        steps=tuple(steps),
    )


def plan_hash(plan: Plan) -> str:
    """sha256 of the canonical JSON of the whole plan, contract included."""
    return _sha256(plan)


def step_keys(plan: Plan, placement_identity: tuple) -> dict[str, str]:
    """Each step's memo key, by step id: its fields minus `id` and `reads`,
    the keys of the steps it reads, and the task's placement identity. The
    id and the plan version are not in it, so an identical step keeps its
    key across replans; a changed placement changes every key. Call it on a
    plan whose shape is clean — `reads` must name earlier steps."""
    keys: dict[str, str] = {}
    for step in plan.steps:
        body = {k: v for k, v in _plain(step).items() if k not in ("id", "reads")}
        keys[step.id] = _sha256(
            {
                "step": body,
                "reads": [keys[r] for r in step.reads],
                "placement": placement_identity,
            }
        )
    return keys


_TUPLE_FIELDS = ("reads", "toolsets")


def _field_type_errors(step: Step) -> list[str]:
    """`reads`/`toolsets` must be tuples of strings, every other field a
    string — a bare `reads="p1"` would otherwise read steps `p` and `1`."""
    errors = []
    for f in fields(step):
        value = getattr(step, f.name)
        if f.name in _TUPLE_FIELDS:
            if not (
                isinstance(value, tuple) and all(isinstance(v, str) for v in value)
            ):
                errors.append(f"{step.id!r}: {f.name} must be a tuple of strings")
        elif not isinstance(value, str):
            errors.append(f"{step.id!r}: {f.name} must be a string")
    return errors


def shape_errors(plan: Plan) -> list[str]:
    """The schema and shape rules, every breach as text the Planner can act
    on: each step is one of the four types with fields of the right type, ids are unique, exactly the last step is
    terminal, `reads` names earlier steps only, a `draft` reads something."""
    if not plan.steps:
        return ["the plan has no steps"]
    alien = [s for s in plan.steps if not isinstance(s, Step)]
    if alien:
        return [f"{s!r} is not a step ({' | '.join(STEP_TYPES)})" for s in alien]
    mistyped = [e for step in plan.steps for e in _field_type_errors(step)]
    if mistyped:
        return mistyped
    errors: list[str] = []
    ids = [s.id for s in plan.steps]
    errors += [
        f"duplicate step id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})
    ]
    errors += [
        f"{s.id}: {s.type} only as the last step"
        for s in plan.steps[:-1]
        if s.type in _TERMINAL
    ]
    last = plan.steps[-1]
    if last.type not in _TERMINAL:
        errors.append(f"the last step must be {' | '.join(_TERMINAL)}")
    for i, step in enumerate(plan.steps):
        errors += [
            f"{step.id}: reads {r}, not an earlier step"
            for r in step.reads
            if r not in ids[:i]
        ]
    if isinstance(last, DraftStep) and not last.reads:
        errors.append(f"{last.id}: a draft must read at least one step")
    return errors
