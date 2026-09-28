"""The agent declaration: how one agent behaves, declared in code.

A pure value, so a plugin declares its own agents without naming the kernel.
Where the model lives is a *tier* in `config.yaml`; everything else about an
agent is a knob — the same on every machine — so it is a constant beside the
agent, pinned by a test (board `domains-plug-in`, tickets 07 and 17).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AgentDeclaration"]


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
