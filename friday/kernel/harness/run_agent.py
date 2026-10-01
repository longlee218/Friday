"""The one core entry that runs any declared agent (build-the-spine ticket 10).

`run_agent` joins an `AgentSpec` with its tier and the run's toolsets and runs
it through the `Harness`; no vendor import here — only `harness.py` names
Pydantic AI. Decisions in board `domains-plug-in`: tickets 03 §3–4 (the
declaration, the core's terminal tools), 13 §1 (`replan`), 14 §4 (a stored
`Ask` is a continuation point), 16 §1 (`retriage`), 17 (the budget).

A run ends in exactly one of: the agent's declared `result`, or an outcome
from a **terminal tool** — `ask_reporter` → `Ask`, `hand_over` → `HandOver`,
`replan` → `Replan`, `retriage` → `Retriage`. A run the budget stopped is a
`HandOver` `budget_spent` (trying again with the same budget only costs
twice); a result the agent's own `check` voids is a `HandOver` `ungrounded`
(ticket 14); any other run that ended without one raises `AgentRunFailed`, which
the runner treats as a failed step (operator, 2026-09-29).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any

from friday.kernel.config import AgentConfig, TierConfig
from friday.kernel.harness.harness import Harness
from friday.sdk.action import ActionContract
from friday.sdk.actions import Ask, HandOver, Replan, Retriage
from friday.sdk.agent import AgentSpec
from friday.sdk.sources import Reads
from friday.sdk.toolset import RunContext, ToolsetSpec

__all__ = [
    "AgentRunFailed",
    "ask_reporter",
    "build_tools",
    "hand_over",
    "reads_for",
    "replan",
    "retriage",
    "run_agent",
    "terminal_tools",
]


class AgentRunFailed(Exception):
    """The run ended with neither a result nor an outcome — the provider
    failed past its attempts, or the answer never fit. `reason` is scrubbed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def ask_reporter(question: str) -> Ask:
    """Ask the reporter for something you cannot find by reading. Ends your
    turn; you continue from here when they reply.

    Args:
        question: the question, ready to send to the reporter as written.
    """
    return Ask(question)


def hand_over(reason: str) -> HandOver:
    """Hand this task to the operator: nothing you can read settles it.

    Args:
        reason: why, for the operator (never sent to the reporter).
    """
    return HandOver(reason)


def replan(reason: str, found: str) -> Replan:
    """Ask for a new plan. Use it when the task needs a toolset or an agent
    you were not granted, or the brief's premise is wrong. Do not use it for
    a dead end you can get past by reading more with the tools you have —
    keep reading.

    Args:
        reason: why this step's direction is wrong.
        found: what you read that the next plan should know, with line ids.
    """
    return Replan(reason=reason, found=found)


def retriage(reason: str, found: str) -> Retriage:
    """This task is another kind of work than the one you were given: send it
    back to triage.

    Args:
        reason: why it is another kind of work.
        found: what you read that shows it, with line ids.
    """
    return Retriage(reason=reason, found=found)


def terminal_tools(contract: ActionContract) -> list[Any]:
    """The core's terminal tools an agent gets under `contract`:
    `ask_reporter` only when the contract allows an `ask` step. The Planner
    shows the same list to itself (`spine/planner_prompt.py`)."""
    terminals: list[Any] = [hand_over, replan, retriage]
    if "ask" in contract.allowed_step_types:
        terminals.insert(0, ask_reporter)
    return terminals


def reads_for(toolset: ToolsetSpec, servers: Mapping[str, Any]) -> dict[str, Reads]:
    """The servers `toolset` declared, each narrowed to the tools it listed.

    **The core alone holds a raw server** (board `domains-plug-in` ticket 03
    §2): a factory is handed only this, so `backend.logs`, which declares
    `loki_query_range`, cannot call `release_rollback` on the same
    `devops-generic` server, and `backend.logs` never sees `backend.db`'s
    reads. A declared server that is not open this run is absent; the
    factory says which one it wanted.
    """
    return {
        name: Reads(servers[name], frozenset(tools))
        for name, tools in toolset.mcp.items()
        if name in servers
    }


def build_tools(
    toolsets: Sequence[ToolsetSpec],
    context: RunContext,
    servers: Mapping[str, Any],
) -> list[Any]:
    """Every tool of `toolsets`, each factory given `context` with its own
    narrowed `mcp` (`reads_for`)."""
    return [
        built
        for toolset in toolsets
        for built in toolset.factory(replace(context, mcp=reads_for(toolset, servers)))
    ]


async def run_agent(
    spec: AgentSpec,
    tier: TierConfig,
    contract: ActionContract,
    toolsets: Sequence[ToolsetSpec],
    context: RunContext,
    brief: str,
    history: Ask | None = None,
    *,
    model: Any = None,
    record: Any = None,
    servers: Mapping[str, Any] | None = None,
) -> Any:
    """Run `spec` once and return its `result`, or an `Ask` / `HandOver` /
    `Replan` / `Retriage`.

    `toolsets` are the registered specs to choose from; the run gets those
    named by both `contract.allowed_toolsets` and `spec.toolsets`, each built
    by its factory from `context` with `mcp` narrowed to that toolset from the
    raw `servers` (whatever `context.mcp` held is replaced). `ask_reporter` is offered only when the
    contract allows an `ask` step.

    `history` is a stored `Ask` to continue from: its messages, `Evidence`
    and `core.todo` checklist are carried on and `brief` is the reporter's
    reply, so nothing already read is read again, line ids keep their
    meaning, and the checklist picks up where it left off. `model` (a
    scripted transport) and `record` (the call sink) pass through to the
    `Harness`.
    """
    if history is not None:
        # Copies: the stored `Ask` stays as it was, so a step that crashes
        # and re-runs from it numbers its reads from the same place.
        if history.evidence is not None:
            context = replace(context, evidence=deepcopy(history.evidence))
        if history.todos is not None:
            context = replace(context, todos=deepcopy(history.todos))

    granted = contract.allowed_toolsets & set(spec.toolsets)
    tools = build_tools(
        [toolset for toolset in toolsets if toolset.name in granted],
        context,
        servers or {},
    )
    terminals = terminal_tools(contract)

    harness = Harness(
        config=AgentConfig(
            name=spec.name,
            api_key=tier.api_key,
            base_url=tier.base_url,
            model=tier.model,
            settings={**tier.settings, "temperature": spec.temperature},
            max_turns=spec.budget.max_turns,
            tokens=spec.budget.tokens,
        ),
        instructions=spec.instructions,
        tools=tools,
        model=model,
        record=record,
        answers=spec.result,
        ends_with=terminals,
    )
    # No `context=`: a factory's tools close over `context`, and the SDK's
    # per-run slot means the `FridayState` only (`tests/test_run_context.py`).
    got = await harness.run_structured(
        brief,
        task_id=context.task_id,
        node=spec.name,
        history=None if history is None else history.history,
    )
    if isinstance(got, Ask):
        return Ask(
            got.text,
            history=harness.messages,
            evidence=context.evidence,
            todos=context.todos,
        )
    if isinstance(got, (HandOver, Replan, Retriage)):
        return got
    if got is not None:
        # The grounding gate (ticket 14): an answer the agent's own check
        # voids is handed over, never drafted from.
        void = spec.check(got, context.evidence) if spec.check else None
        return HandOver(f"ungrounded: {void}") if void else got
    if harness.over_budget:
        return HandOver(f"budget_spent: {harness.last_error}")
    raise AgentRunFailed(harness.last_error or "no result")
