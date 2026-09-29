"""The agent declaration: how one agent behaves, declared in code.

A pure value, so a plugin declares its own agents without naming the kernel.
Where the model lives is a *tier* in `config.yaml`; everything else about an
agent is a knob — the same on every machine — so it is a constant beside the
agent, pinned by a test (board `domains-plug-in`, tickets 07 and 17).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["AgentDeclaration", "AgentSpec", "Budget"]


@dataclass(frozen=True, slots=True)
class AgentDeclaration:
    """One agent: its tier, its temperature, its budget and its request timeout.

    `max_turns` counts every request to the model in one run, tool turns
    included; `tokens` is input + output summed over one run. Together they
    are the whole per-agent budget — there is no time budget on a run.
    `request_timeout_seconds` bounds **one** request to the model (Pydantic
    AI's `ModelSettings.timeout`); a request that runs past it is an attempt
    that failed, and is tried again. Temperature is per job, not per tier.
    """

    name: str
    tier: str
    temperature: float
    max_turns: int
    tokens: int
    request_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class Budget:
    """One run of an agent: `max_turns` counts every model request, tool turns
    included; `tokens` is input + output summed over the run. No time."""

    max_turns: int
    tokens: int


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """A named agent a plugin registers (`api.agent`) — a declaration, not a
    Pydantic AI agent. The core joins it with its tier from `config.yaml` and
    the run's toolsets (contract ∩ `toolsets`) and runs it through the Harness.

    `description` is for the Planner (1–3 sentences), separate from the
    agent's own `instructions`. `result` is the agent's terminal output type;
    the terminal tools (`ask_reporter`, `hand_over`, `replan`) are the core's
    and never listed here. `toolsets` is the agent's ceiling.

    `check(result, evidence)` is the agent's grounding gate (build-the-spine
    ticket 14): given its `result` and the run's `Evidence`, the reason the
    answer is void, or `None` — `run_agent` turns a reason into a `HandOver`,
    so an ungrounded answer never reaches a `draft`.
    """

    name: str
    description: str
    instructions: str
    result: type
    tier: str
    toolsets: tuple[str, ...]
    budget: Budget
    temperature: float
    check: Callable[[Any, Any], str | None] | None = None
