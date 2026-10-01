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

**Passes** (ticket 14; board `domains-plug-in` ticket 14 §4, §9). A result is
stored under the pass that ran it; a key keeps one row per pass and the
newest is read. Within its own pass every result is reused (a crash re-run).
From an earlier pass:

```
Ask with history      a continuation point: the step runs again from its
                      messages and `Evidence`, the reporter's reply as the brief
Ask without history   the Planner's own question, answered: a `Replan`
                      (counts toward `max_replans`), the reply as `found`
HandOver              never reused: the step runs again
anything else         reused
```

A stored `Ask` keeps its message history, its `Evidence` and its `core.todo`
checklist (`Todos`, build-the-spine ticket 24), each as JSON. A
`HandOver` is stored without `interruption`, the DAG path's field (deleted in
16). Over 200 lines: the walk, its reuse rules and the one codec for
what it stores are one unit.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
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
from friday.sdk.evidence import Evidence
from friday.sdk.todos import Todos

__all__ = ["STEP_ATTEMPTS", "Resume", "RunEnd", "Steps", "run_plan", "stored_results"]

log = logging.getLogger(__name__)

#: Tries of one step (model API error, timeout, crash) before `HandOver
#: step_failed`. A core constant, the same for every step (ticket 13 §3).
STEP_ATTEMPTS = 2

_STOPS = (Ask, HandOver, Retriage, Reply)


@dataclass(frozen=True, slots=True)
class Resume:
    """An agent step continued from its stored `Ask`: `reply` is what the
    reporter said since, the brief the run continues with."""

    ask: Ask
    reply: str


@dataclass(frozen=True, slots=True)
class Steps:
    """What the pass binds for the runner (ticket 14), each closing over the
    task's intake context: `agent` runs an agent step (via `run_agent`), from
    a `Resume` when one is given; `draft` writes the `Reply` over what the
    step reads + the intake context; `planner` returns the next version for a
    `Replan` — gated and frozen — or a `HandOver` (`planner_failed`);
    `replied` is what the reporter said after a time (everything for
    `None`; `""` for nothing); `heard_until` is the newest reporter turn this
    pass's Intake read, stored with an `Ask` so the pass that continues it
    hands on everything said after the question's own Intake."""

    agent: Callable[[AgentStep, Mapping[str, Any], Resume | None], Awaitable[Any]]
    draft: Callable[[DraftStep, Mapping[str, Any]], Awaitable[Reply]]
    planner: Callable[[Frozen, Mapping[str, Any], Replan], Awaitable[Frozen | HandOver]]
    replied: Callable[[datetime | None], Awaitable[str]] | None = None
    heard_until: datetime | None = None


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
    pass_no: int = 1,
) -> RunEnd:
    """Walk `frozen` in pass `pass_no`, replanning on a fresh `Replan` until
    the plan stops. `replans_used` is what the task has already used (13 §5
    counts reply-driven replans too); a replan counts once the Planner is
    called, whether it answers with a plan or `planner_failed`."""
    plans = [frozen]
    while True:
        results, end = await _walk(
            db, frozen, agents, placement_identity, steps, pass_no
        )
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


async def stored_results(
    db: Any,
    frozen: Frozen,
    agents: Mapping[str, AgentSpec],
    placement_identity: tuple,
) -> dict[str, Any]:
    """What `frozen`'s steps came to under `placement_identity`, by step id —
    for a Planner replanning from it (ticket 14). A step never run is absent."""
    plan = frozen.plan
    keys = step_keys(plan, placement_identity)
    stored = await db.step_results(plan.task_id)
    return {
        step.id: _decode(row.kind, row.body, step, agents)
        for step in plan.steps
        if (row := stored.get(keys[step.id])) is not None
    }


async def _walk(
    db: Any,
    frozen: Frozen,
    agents: Mapping[str, AgentSpec],
    placement_identity: tuple,
    steps: Steps,
    pass_no: int,
) -> tuple[dict[str, Any], Ask | HandOver | Retriage | Reply | Replan]:
    """One version, first step to where it stops: `(results by step id, the
    outcome)`, the outcome a fresh `Replan` when an agent just raised one or
    the reporter answered the Planner's own question."""
    plan = frozen.plan
    keys = step_keys(plan, placement_identity)
    stored = await db.step_results(plan.task_id)
    results: dict[str, Any] = {}
    for step in plan.steps:
        row = stored.get(keys[step.id])
        value = None if row is None else _decode(row.kind, row.body, step, agents)
        resume = None
        if row is not None and row.pass_no != pass_no:
            if isinstance(value, Ask) and value.history is None:
                reply = await _replied(steps, _heard(row))
                return results, Replan(
                    reason="the reporter answered the plan's question",
                    found=reply or "(nothing new)",
                )
            if isinstance(value, Ask):
                resume = Resume(value, await _replied(steps, _heard(row)))
                row = None
            elif isinstance(value, HandOver):
                row = None
        if row is not None:
            if isinstance(value, Replan) and row.plan_version == plan.plan_version:
                results[step.id] = value
                return results, value
        else:
            reads = {r: results[r] for r in step.reads}
            try:
                value = await _run_step(step, reads, steps, resume)
            except _StepFailed as failed:
                return results, HandOver(f"step_failed: {step.id}: {failed}")
            kind, body = _encode(value, step, agents)
            if kind == "ask" and steps.heard_until is not None:
                body["heard_until"] = steps.heard_until.isoformat()
            await db.put_step_result(
                task_id=plan.task_id,
                step_key=keys[step.id],
                step_id=step.id,
                plan_version=plan.plan_version,
                kind=kind,
                body=body,
                pass_no=pass_no,
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


async def _replied(steps: Steps, since: datetime | None) -> str:
    return "" if steps.replied is None else await steps.replied(since)


def _heard(row: Any) -> datetime | None:
    """The newest reporter turn the asking pass's Intake read — what the
    reply is measured from (`None`: they had said nothing, so all of it)."""
    at = row.body.get("heard_until")
    return datetime.fromisoformat(at) if at else None


async def _run_step(
    step: Step, reads: Mapping[str, Any], steps: Steps, resume: Resume | None
) -> Any:
    if isinstance(step, AskStep):
        return Ask(step.question)
    if isinstance(step, HandOverStep):
        return HandOver(step.reason)
    reason = ""
    for attempt in range(1, STEP_ATTEMPTS + 1):
        try:
            if isinstance(step, AgentStep):
                return await steps.agent(step, reads, resume)
            return await steps.draft(step, reads)
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
#: Subclasses of an outcome are stored as it (`PlannerFailed` is a `HandOver`).
_KINDS = {t: kind for kind, t in _TYPES.items()}
#: Not stored: `HandOver.interruption` (DAG only).
_NOT_STORED = ("interruption",)


def _encode(
    value: Any, step: Step, agents: Mapping[str, AgentSpec]
) -> tuple[str, dict]:
    """`(kind, JSON body)`. An agent's own result goes through its declared
    `result` type; an `Ask`'s `Evidence` and `Todos` as their own JSON
    (ticket 14, extended by ticket 24)."""
    kind = next((k for t, k in _KINDS.items() if isinstance(value, t)), None)
    if kind is None:
        return "result", TypeAdapter(agents[step.agent].result).dump_python(
            value, mode="json"
        )
    body = {
        f.name: getattr(value, f.name)
        for f in fields(_TYPES[kind])
        if f.name not in _NOT_STORED
    }
    if isinstance(body.get("evidence"), Evidence):
        body["evidence"] = body["evidence"].dump()
    if isinstance(body.get("todos"), Todos):
        body["todos"] = body["todos"].dump()
    return kind, body


def _decode(kind: str, body: dict, step: Step, agents: Mapping[str, AgentSpec]) -> Any:
    if kind == "result":
        return TypeAdapter(agents[step.agent].result).validate_python(body)
    if kind == "ask":
        body = {k: v for k, v in body.items() if k != "heard_until"}
        if body.get("evidence") is not None:
            body["evidence"] = Evidence.load(body["evidence"])
        if body.get("todos") is not None:
            body["todos"] = Todos.load(body["todos"])
    return _TYPES[kind](**body)
