"""The runner: walks a frozen plan's steps in order. Plain code, not wired to
the pool yet (build-the-spine ticket 12; decisions in board `domains-plug-in`,
ticket 13 as amended by 16 and 17, ticket 09 §8).

```
for each step:
  result stored at (task_id, step_key)?  → skip, hand it to readers
  run the step, STEP_ATTEMPTS tries      → else HandOver step_failed
  result                                 → store, next step
  Ask | HandOver | Retriage | Reply      → store, the plan stops there
  Replan                                 → store; replans used = max_replans
                                           → HandOver replans_exhausted, else
                                           Planner → GatePlan → v(n+1) from its first step
```

A stored step never runs again, so a `Replan` reused by a later version is
data for its readers, never a new signal, and no loop is possible. A `Replan`
stored by the version being walked is still the signal — the walk was cut
between storing it and the Planner's answer, so it is answered now. What a step runs *with* — the
agent's tier, toolsets, run context, the responder, the Planner — is the
pass's to bind (ticket 14), handed in as `Steps`. The runner is the only
writer of `step_results` (guarded in `tests/test_runner.py`).

A stored `Ask` keeps its message history but not its `Evidence`; where that
is stored is ticket 14's call (operator, 2026-09-29). A `HandOver` is stored
without `interruption`, the DAG path's field (deleted in 16).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

from pydantic import TypeAdapter

from friday.kernel.spine.plan import (
    AgentStep,
    AskStep,
    DraftStep,
    HandOverStep,
    Step,
    step_keys,
)
from friday.kernel.spine.plan_gate import Frozen
from friday.sdk.actions import Ask, HandOver, Replan, Reply, Retriage
from friday.sdk.agent import AgentSpec

__all__ = ["STEP_ATTEMPTS", "RunEnd", "Steps", "run_plan"]

log = logging.getLogger(__name__)

#: Tries of one step (model API error, timeout, crash) before `HandOver
#: step_failed`. A core constant, the same for every step (ticket 13 §3).
STEP_ATTEMPTS = 2

_STOPS = (Ask, HandOver, Retriage, Reply)


@dataclass(frozen=True, slots=True)
class Steps:
    """What the pass binds for the runner (ticket 14), each closing over the
    task's intake context: `agent` runs an agent step (via `run_agent`),
    `draft` writes the `Reply` over what the step reads + the intake context,
    `planner` returns the next version for a `Replan` — gated and frozen — or
    a `HandOver` (`planner_failed`)."""

    agent: Callable[[AgentStep, Mapping[str, Any]], Awaitable[Any]]
    draft: Callable[[DraftStep, Mapping[str, Any]], Awaitable[Reply]]
    planner: Callable[[Frozen, Mapping[str, Any], Replan], Awaitable[Frozen | HandOver]]


@dataclass(frozen=True, slots=True)
class RunEnd:
    """How the run ended, every plan version it walked, and the replans used
    so far on this task. Every result is in `step_results`."""

    outcome: Ask | HandOver | Retriage | Reply
    plans: tuple[Frozen, ...]
    replans_used: int


async def run_plan(
    db: Any,
    frozen: Frozen,
    *,
    agents: Mapping[str, AgentSpec],
    placement_identity: tuple,
    steps: Steps,
    replans_used: int = 0,
) -> RunEnd:
    """Walk `frozen`, replanning on a fresh `Replan` until the plan stops.
    `replans_used` is what the task has already used (13 §5 counts
    reply-driven replans too); a replan counts once the Planner is called,
    whether it answers with a plan or `planner_failed`."""
    plans = [frozen]
    while True:
        results, end = await _walk(db, frozen, agents, placement_identity, steps)
        if not isinstance(end, Replan):
            return RunEnd(end, tuple(plans), replans_used)
        if replans_used >= frozen.plan.contract.limits.max_replans:
            return RunEnd(
                HandOver(f"replans_exhausted: {end.found}"), tuple(plans), replans_used
            )
        replans_used += 1
        nxt = await steps.planner(frozen, results, end)
        if isinstance(nxt, HandOver):
            return RunEnd(nxt, tuple(plans), replans_used)
        frozen = nxt
        plans.append(frozen)


async def _walk(
    db: Any,
    frozen: Frozen,
    agents: Mapping[str, AgentSpec],
    placement_identity: tuple,
    steps: Steps,
) -> tuple[dict[str, Any], Ask | HandOver | Retriage | Reply | Replan]:
    """One version, first step to where it stops: `(results by step id, the
    outcome)`, the outcome a fresh `Replan` when an agent just raised one."""
    plan = frozen.plan
    keys = step_keys(plan, placement_identity)
    stored = await db.step_results(plan.task_id)
    results: dict[str, Any] = {}
    for step in plan.steps:
        row = stored.get(keys[step.id])
        if row is not None:
            value = _decode(row.kind, row.body, step, agents)
            if isinstance(value, Replan) and row.plan_version == plan.plan_version:
                results[step.id] = value
                return results, value
        else:
            reads = {r: results[r] for r in step.reads}
            try:
                value = await _run_step(step, reads, steps)
            except _StepFailed as failed:
                return results, HandOver(f"step_failed: {step.id}: {failed}")
            kind, body = _encode(value, step, agents)
            await db.put_step_result(
                task_id=plan.task_id,
                step_key=keys[step.id],
                step_id=step.id,
                plan_version=plan.plan_version,
                kind=kind,
                body=body,
            )
            if isinstance(value, Replan):
                results[step.id] = value
                return results, value
        results[step.id] = value
        if isinstance(value, _STOPS):
            return results, value
    # Unreachable: GatePlan lets only a plan whose last step stops through.
    raise AssertionError(f"plan {plan.plan_version} ran past its last step")


class _StepFailed(Exception):
    pass


async def _run_step(step: Step, reads: Mapping[str, Any], steps: Steps) -> Any:
    if isinstance(step, AskStep):
        return Ask(step.question)
    if isinstance(step, HandOverStep):
        return HandOver(step.reason)
    run = steps.agent if isinstance(step, AgentStep) else steps.draft
    reason = ""
    for attempt in range(1, STEP_ATTEMPTS + 1):
        try:
            return await run(step, reads)
        except Exception as exc:  # noqa: BLE001 — every step failure is an attempt
            # `AgentRunFailed.reason` is scrubbed; any other text may not be.
            reason = getattr(exc, "reason", None) or type(exc).__name__
            log.warning(
                "step %s attempt %d/%d failed: %s",
                step.id,
                attempt,
                STEP_ATTEMPTS,
                reason,
            )
    raise _StepFailed(reason)


_TYPES = {
    "ask": Ask,
    "hand_over": HandOver,
    "replan": Replan,
    "retriage": Retriage,
    "reply": Reply,
}
_KINDS = {t: kind for kind, t in _TYPES.items()}
#: Not stored: `Ask.evidence` (ticket 14), `HandOver.interruption` (DAG only).
_NOT_STORED = ("evidence", "interruption")


def _encode(
    value: Any, step: Step, agents: Mapping[str, AgentSpec]
) -> tuple[str, dict]:
    """`(kind, JSON body)`. An agent's own result goes through its declared
    `result` type; an `Ask` drops its `Evidence` (ticket 14)."""
    kind = _KINDS.get(type(value))
    if kind is None:
        return "result", TypeAdapter(agents[step.agent].result).dump_python(
            value, mode="json"
        )
    return kind, {
        f.name: getattr(value, f.name)
        for f in fields(value)
        if f.name not in _NOT_STORED
    }


def _decode(kind: str, body: dict, step: Step, agents: Mapping[str, AgentSpec]) -> Any:
    if kind == "result":
        return TypeAdapter(agents[step.agent].result).validate_python(body)
    return _TYPES[kind](**body)
