"""The Planner: one core agent that writes every action's plan (build-the-spine
ticket 11; decision: board `domains-plug-in` ticket 12, as amended by 17).
Run by the spine pass's `plan` step and, on a `Replan`, by the runner
(ticket 14).

```
plan / replan → Planner answers → GatePlan
  Frozen                          → the plan runs
  Refused (or no answer at all)   → the errors go back in the same
                                    conversation; PLAN_REWRITES more tries
  still refused                   → PlannerFailed (a HandOver, planner_failed)
```

- **Always runs**, one-step cases included; the same agent and instructions
  for every action. It reads only what Friday knows — `core.memory` and
  `core.skills` (their read tools only), fixed here, never from a contract — and runs through the
  `Harness` directly, not `run_agent`: it gets no terminal tools, since an
  `ask` or a `hand_over` is a step it writes into the plan.
- **A refusal continues the conversation**: the refused plan is in its
  history, the errors are the next message, what it already read is kept.
  Running out of budget with no plan counts as a refusal.
- **A replan is a fresh conversation** with the current plan, the stored
  results of its steps and the replan's reason; it gets its own rewrites.
- `PlannerFailed` carries every version it wrote, each version's errors and
  what it read, for the operator. It is a `HandOver`, so the runner treats
  it as one; its reason starts `planner_failed` so it is told apart from a
  `hand_over` step the Planner chose.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from friday.kernel.config import AgentConfig
from friday.kernel.harness.harness import Harness
from friday.kernel.harness.model_client import ANSWER
from friday.kernel.spine.plan import Plan
from friday.kernel.spine.plan_gate import Frozen, gate_plan
from friday.kernel.spine.planner_prompt import (
    INSTRUCTIONS,
    PlanAnswer,
    first_prompt,
    refusal_prompt,
    replan_prompt,
    to_steps,
)
from friday.sdk.action import Action
from friday.sdk.actions import HandOver, Replan
from friday.sdk.agent import AgentDeclaration, AgentSpec
from friday.sdk.intake import IntakeContext
from friday.sdk.toolset import RunContext, ToolsetSpec, ToolSpec

__all__ = [
    "PLANNER",
    "PLANNER_READS",
    "PLANNER_TOOLSETS",
    "PLAN_REWRITES",
    "PlannerFailed",
    "Planning",
    "plan",
    "replan",
]

#: A strong tier: a bad plan steers the whole run, and GatePlan checks rules,
#: not quality. Its budget is the same for every action (ticket 17).
PLANNER = AgentDeclaration(
    name="planner",
    tier="strong",
    temperature=0.0,
    max_turns=10,
    tokens=200_000,
    request_timeout_seconds=120.0,
)
#: Rewrites after a refusal, per plan version (GatePlan, ticket 11) — not
#: `max_replans`, which counts new directions.
PLAN_REWRITES = 2
#: What the Planner reads: what Friday knows, never the reporter's system.
PLANNER_TOOLSETS = ("core.memory", "core.skills")
#: Of those toolsets' tools, only the reads: the Planner never writes
#: memory (decision 12 §5 — "light read tools").
PLANNER_READS = frozenset(
    {
        "memory_search",
        "fetch_skill",
        "search_skills",
        "describe_skill",
        "read_skill_file",
    }
)


@dataclass(frozen=True, slots=True)
class PlannerFailed(HandOver):
    """No plan passed GatePlan within `PLAN_REWRITES`. `plans[i]` is the
    version the Planner wrote on try `i` (`None`: nothing came back),
    `errors[i]` why it was refused; `read` is every tool call in the
    conversation it ended with (a run that failed keeps no messages, so its
    calls are in the recorded model calls only)."""

    plans: tuple[Plan | None, ...] = ()
    errors: tuple[tuple[str, ...], ...] = ()
    read: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Planning:
    """What one task's Planner runs with, bound once by the pass (ticket 14):
    `config` is `PLANNER` on its tier; `agents` / `toolsets` are the
    registered specs; `state` is the `FridayState` the memory tools scope by.
    `model` (a scripted transport) and `record` pass through to the Harness."""

    config: AgentConfig
    task_id: int
    action: Action
    intake: IntakeContext
    agents: Mapping[str, AgentSpec]
    toolsets: Mapping[str, ToolsetSpec]
    state: Any = None
    model: Any = None
    record: Any = None


async def plan(
    p: Planning, *, version: int = 1, replaces: str | None = None
) -> Frozen | PlannerFailed:
    """A first plan for the task — version 1, or the next version on a
    hand-back with no frozen plan to replan from (ticket 14)."""
    prompt = first_prompt(p.action, p.intake, p.agents, p.toolsets)
    return await _write(p, prompt, version=version, replaces=replaces)


async def replan(
    p: Planning,
    current: Frozen,
    results: Mapping[str, Any],
    signal: Replan,
    *,
    version: int | None = None,
) -> Frozen | PlannerFailed:
    """The next version after `current`, from `results` (by step id) and the
    agent's `signal` — the runner's `Steps.planner`, once bound to `p`.
    `version` numbers it past a refused version stored after `current`
    (ticket 14); `current`'s + 1 when not given."""
    prompt = replan_prompt(
        p.action,
        p.intake,
        p.agents,
        p.toolsets,
        current=current.plan,
        results=results,
        signal=signal,
    )
    return await _write(
        p,
        prompt,
        version=version or current.plan.plan_version + 1,
        replaces=current.plan_hash,
    )


async def _write(
    p: Planning, opening: str, *, version: int, replaces: str | None
) -> Frozen | PlannerFailed:
    harness = _harness(p)
    prompt, history = opening, None
    plans: list[Plan | None] = []
    errors: list[tuple[str, ...]] = []
    for _ in range(1 + PLAN_REWRITES):
        answer = await harness.run_structured(
            prompt,
            context=p.state,
            task_id=p.task_id,
            node=PLANNER.name,
            history=history,
        )
        history = harness.messages or history
        if answer is None:
            # Nothing came back (budget spent, provider down, a prose answer
            # that did not fit): the last plan's errors still stand.
            written = None
            last = next(
                (e for w, e in zip(reversed(plans), reversed(errors)) if w),
                (),
            )
            refused: tuple[str, ...] = (
                f"no plan came back: {harness.last_error or 'no answer'}",
                *last,
            )
        else:
            written = Plan(
                task_id=p.task_id,
                action=p.action.name,
                plan_version=version,
                replaces=replaces,
                contract=p.action.contract,
                goal=answer.goal,
                steps=to_steps(answer, p.action.contract, p.agents),
            )
            verdict = gate_plan(written, p.agents)
            if isinstance(verdict, Frozen):
                return verdict
            refused = verdict.errors
        plans.append(written)
        errors.append(refused)
        # The refusal names the plan it is about, so it reads the same when
        # the conversation it answers was lost (a failed run keeps no
        # messages); with no conversation yet, it rides on the opening.
        shown = next((w for w in reversed(plans) if w is not None), None)
        refusal = refusal_prompt(refused, shown)
        prompt = refusal if history is not None else f"{opening}\n\n{refusal}"
    tried = sum(w is not None for w in plans)
    return PlannerFailed(
        reason=f"planner_failed: {len(plans)} tries, {tried} plans refused; last: "
        + "; ".join(errors[-1]),
        plans=tuple(plans),
        errors=tuple(errors),
        read=_reads(history),
    )


def _harness(p: Planning) -> Harness:
    run = RunContext(
        task_id=p.task_id,
        domain=p.intake.domain,
        evidence=None,
        mcp={},
        reported_at=datetime.fromisoformat(p.intake.reported_at),
    )
    missing = [name for name in PLANNER_TOOLSETS if name not in p.toolsets]
    if missing:
        raise ValueError(f"the Planner needs {', '.join(missing)}: pass them in")
    tools = [
        built
        for name in PLANNER_TOOLSETS
        for built in p.toolsets[name].factory(run)
        if _tool_name(built) in PLANNER_READS
    ]
    return Harness(
        config=p.config,
        instructions=INSTRUCTIONS,
        tools=tools,
        model=p.model,
        record=p.record,
        answers=PlanAnswer,
    )


def _tool_name(built: Any) -> str:
    if isinstance(built, ToolSpec):
        return built.options.get("name") or built.fn.__name__
    return getattr(built, "name", "")


def _reads(history: list[Any] | None) -> tuple[str, ...]:
    """Every tool call in the conversation but the answer, as `name(args)`."""
    calls = []
    for message in history or ():
        for part in message.get("parts", ()):
            if part.get("part_kind") == "tool-call" and part.get("tool_name") != ANSWER:
                args = part.get("args")
                shown = (
                    args
                    if isinstance(args, str)
                    else json.dumps(args, ensure_ascii=False)
                )
                calls.append(f"{part['tool_name']}({shown})")
    return tuple(calls)
